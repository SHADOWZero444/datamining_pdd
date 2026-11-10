"""离线结果页回归。仅替换保存对话框，实际写入并读回 CSV 和 XLSX。"""
import csv
import os
import sys
import tempfile
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
from pdd.server.http import AppHandler
from pdd.services import auth, export
from pdd.storage import library, results
from support import artifact_dir


def main():
    artifacts = artifact_dir()
    with tempfile.TemporaryDirectory(dir=artifacts) as scratch, \
         patch.object(results, "DATA_DIR", Path(scratch) / "searches"), \
         patch.object(library, "LIBRARY_FILE", Path(scratch) / "library.json"), \
         patch.object(auth, "ROOT", Path(scratch)):
        term = "耳机"
        goods = []
        for index, (title, price, sales, badges, shop) in enumerate([
            ("蓝牙耳机 Pro", "99", "近30天已拼1.2万件", ["black", "official"], "声动旗舰店"),
            ("蓝牙耳机 Mini", "49", "已拼12件", ["black"], "声动旗舰店"),
            ("翻新蓝牙耳机", "99", "已拼1.2万件", ["black", "official"], "声动旗舰店"),
            ("蓝牙耳机 Air", "99", "已拼1.2万件", ["black", "official"], "声动旗舰店"),
            ("有线耳机", "", "", [], ""),
        ], 1):
            goods.append(dict(keyword=term, seq=index, goods_id=str(index), title=title, price=price,
                              sales=sales, badges=badges, detail={"shop_name": shop}))
        results.merge_goods(term, goods)
        results.merge_goods("键盘", [dict(goods[0], keyword="键盘")])
        server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        def save_path(keyword, fmt):
            return str(Path(scratch) / f"{keyword}.{fmt}")
        try:
            with patch.object(export, "choose_path", side_effect=save_path), sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 820})
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin)
                page.locator('[data-section="tools"]').click()
                page.locator('[data-keyword="耳机"]').first.click()
                toggle = page.get_by_role("button", name="数据筛选", exact=True)
                form = page.locator("[data-result-filters]")
                expect(form).to_be_hidden()
                page.locator('[name="pageSize"]').fill("1")
                page.locator(".list-apply").click()
                csv_button = page.get_by_role("button", name="导出 CSV")
                excel_button = page.get_by_role("button", name="导出 Excel")
                csv_button.click()
                expect(page.locator(".export-status")).to_contain_text("5 条商品")
                def exported_csv():
                    with Path(save_path(term, "csv")).open(encoding="utf-8-sig", newline="") as stream:
                        return list(csv.DictReader(stream))
                assert [r["商品编号"] for r in exported_csv()] == ["1", "2", "3", "4", "5"]
                toggle.press("Enter")
                form.get_by_label("品牌黑标").check()
                form.get_by_label("官方旗舰").check()
                form.get_by_label("商品关键词").fill("蓝牙，耳机")
                form.get_by_label("商品禁用词").fill("翻新")
                form.get_by_label("店名关键词").fill("声动")
                form.get_by_label("价格最低").fill("99")
                form.get_by_label("价格最高").fill("99")
                form.get_by_label("销量最低").fill("12000")
                form.get_by_label("销量最高").fill("12000")
                expect(page.locator(".filter-summary")).to_have_text("已用 6 项 · 匹配 2 条")
                page.locator('[data-sort-field="seq"]').click(click_count=1)
                page.locator('[data-sort-field="seq"]').click()
                expect(page.locator(".seq")).to_have_text("4")
                page.screenshot(path=artifacts / "filters-desktop.png")
                toggle.click()
                csv_button.click()
                expect(page.locator(".export-status")).to_contain_text("2 条商品")
                assert [r["商品编号"] for r in exported_csv()] == ["4", "1"]
                excel_button.click()
                expect(page.locator(".export-status")).to_contain_text(".xlsx")
                with zipfile.ZipFile(save_path(term, "xlsx")) as book:
                    root = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
                    rows = root.findall(".//{*}sheetData/{*}row")
                    assert len(rows) == 3
                    assert [r.findall("{*}c")[1].findtext(".//{*}t") for r in rows[1:]] == ["4", "1"]
                page.locator('[data-keyword="键盘"]').first.click()
                expect(form).to_be_hidden()
                expect(page.locator(".filter-summary")).to_be_empty()
                page.locator('[data-keyword="耳机"]').first.click()
                expect(page.locator(".filter-summary")).to_contain_text("匹配 2 条")
                toggle.click()
                form.get_by_label("价格最高").fill("98")
                expect(csv_button).to_be_disabled()
                expect(page.locator(".filter-error")).to_contain_text("价格下限不能大于上限")
                form.get_by_label("价格最高").fill("99")
                form.get_by_label("店名关键词").fill("无此店")
                expect(page.locator(".catalog .empty")).to_contain_text("没有匹配的商品")
                expect(excel_button).to_be_disabled()
                page.get_by_role("button", name="清除筛选").click()
                expect(page.locator(".table-count")).to_contain_text("/ 5 条")
                form.get_by_label("店名关键词").fill("新店")
                results.merge_goods(term, [dict(goods[1], detail={"shop_name": "新店"})])
                expect(page.locator(".shop")).to_have_text("新店")
                form.get_by_label("店名关键词").fill("")
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=artifacts / "filters-narrow.png")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                with patch.object(export, "choose_path", return_value=""):
                    csv_button.click()
                    expect(page.locator(".export-status")).to_have_text("已取消导出。")
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    print("PASS: filter combinations, sorting, pagination, live updates, per-term state, XLSX and CSV")


if __name__ == "__main__":
    main()
