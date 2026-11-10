"""按结果页选定的商品和顺序保存表格。"""
import csv
import os
import re
import tempfile
import threading
from pathlib import Path

from pdd.storage import results

HEADERS = ("序号", "商品编号", "商品", "标签", "店名", "价格（元）", "销量", "更新",
           "商品链接", "主图链接", "详情图片链接", "词条")
BADGES = {"bybt": "百亿补贴", "black": "品牌黑标", "official": "官方旗舰", "flagship": "旗舰店"}
export_lock = threading.Lock()


def choose_path(keyword, fmt):
    from tkinter import Tk, filedialog

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", keyword)
    title = "Excel 工作簿" if fmt == "xlsx" else "CSV 文件"
    root = Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        return filedialog.asksaveasfilename(parent=root, title=f"导出{title}", initialfile=f"{name}-商品.{fmt}",
                                           defaultextension=f".{fmt}", filetypes=[(title, f"*.{fmt}")])
    finally:
        root.destroy()


def record(keyword, item):
    detail = item.get("detail") or {}
    price = str(item.get("price") if item.get("price") is not None else "")
    if re.fullmatch(r"\d+(?:\.\d+)?", price):
        price = float(price)
    return (item.get("seq"), str(item["goods_id"]), item.get("title", ""),
            "、".join(BADGES.get(tag, tag) for tag in item.get("badges") or []),
            detail.get("shop_name", ""), price, item.get("sales", ""), "是" if item.get("updated") is True else "否",
            item.get("url", ""), item.get("image", ""), "\n".join(detail.get("images") or []), keyword)


def write_export(path, fmt, keyword, rows):
    if fmt == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS)
            for item in rows:
                # 阻止表格软件把采集到的文本当作公式执行。
                values = record(keyword, item)
                writer.writerow("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                                else v for v in values)
        return

    import xlsxwriter
    if len(rows) >= 1048576:
        raise ValueError("商品数超过 Excel 单表上限，请导出 CSV。")
    try:
        with xlsxwriter.Workbook(path, {"constant_memory": True, "strings_to_formulas": False,
                                         "strings_to_urls": False}) as book:
            sheet = book.add_worksheet("商品")
            sheet.freeze_panes(1, 2)
            sheet.autofilter(0, 0, len(rows), len(HEADERS) - 1)
            header = book.add_format({"bold": True, "bg_color": "#F0F0F0"})
            sheet.write_row(0, 0, HEADERS, header)
            for col, width in enumerate((9, 21, 48, 22, 26, 14, 18, 8, 36, 36, 36, 20)):
                sheet.set_column(col, col, width)
            for index, item in enumerate(rows, 1):
                values = record(keyword, item)
                if any(isinstance(v, str) and len(v) > 32767 for v in values):
                    raise ValueError("部分文本超过 Excel 单元格上限，请导出 CSV 以保留完整内容。")
                sheet.write_row(index, 0, values)
    except xlsxwriter.exceptions.XlsxWriterException as exc:
        raise ValueError("无法写入 Excel 文件，请检查剩余磁盘空间并重试。") from exc


def export_results(payload):
    fmt, keyword, ids = payload.get("format"), payload.get("keyword"), payload.get("ids")
    if fmt not in ("csv", "xlsx") or not isinstance(keyword, str):
        raise ValueError("请选择有效的词条和导出格式。")
    if not isinstance(ids, list) or not ids or not all(isinstance(key, (str, int)) for key in ids):
        raise ValueError("当前没有可导出的商品。")
    if not export_lock.acquire(blocking=False):
        raise ValueError("已有导出正在进行，请完成后重试。")
    try:
        # 在打开对话框之前固定内容；其后更新任务可以继续写入新批次。
        feed = results.result_feed(keyword)
        if payload.get("revision") != feed["revision"]:
            raise ValueError("词条数据刚刚有变化，请查看最新匹配数量后再次导出。")
        by_id = {str(item["goods_id"]): item for item in feed["goods"] if item.get("goods_id")}
        try:
            rows = [by_id[str(key)] for key in ids]
        except KeyError:
            raise ValueError("商品列表已变化，请刷新后重新导出。") from None
        try:
            destination = choose_path(keyword, fmt)
        except Exception as exc:
            raise ValueError("无法打开保存窗口，请检查桌面环境后重试。") from exc
        if not destination:
            return {"cancelled": True}
        target = Path(destination)
        if target.suffix.lower() != "." + fmt:
            raise ValueError(f"请选择以 .{fmt} 结尾的文件名。")
        # 在同一目录完成写入，再替换已确认的目标文件。
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".tmp") as stream:
                temp = Path(stream.name)
            write_export(temp, fmt, keyword, rows)
            os.replace(temp, target)
        except (OSError, ImportError) as exc:
            raise ValueError("导出未完成，请确认文件未被占用、目录可写且已安装项目依赖。") from exc
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return {"count": len(rows), "path": str(target)}
    finally:
        export_lock.release()
