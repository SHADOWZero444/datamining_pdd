import json
import re
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_DIR = ROOT / "data" / "searches"
HOST = "127.0.0.1"
PORT = 8765

# job 是当前这一次抓取。界面轮询它，不在请求里直接开浏览器。
job = {
    "running": False,
    "keyword": "",
    "status": "idle",
    "message": "",
    "count": 0,
    "total": None,
}
job_lock = threading.Lock()


def read_filters(payload):
    """把界面传来的筛选收成抓取函数能用的条件。空着的价格不当成 0。"""
    filters = {
        "tags": [tag for tag in (payload.get("tags") or []) if tag in ("bybt", "black", "official", "flagship")],
        "banned": str(payload.get("banned") or ""),
        "price_min": None,
        "price_max": None,
    }
    for key in ("price_min", "price_max"):
        raw = payload.get(key)
        if raw in ("", None):
            continue
        try:
            filters[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return filters


def safe_name(keyword):
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", keyword).strip()
    return cleaned or "未命名"


def result_path(keyword):
    return DATA_DIR / f"{safe_name(keyword)}.jsonl"


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def load_goods(keyword):
    rows = read_jsonl(result_path(keyword))
    if rows:
        return rows
    # 还没抓过时，用你保存的搜索页，方便界面先看到真实商品。
    saved = ROOT / "pdd.html"
    if keyword == "蓝牙耳机" and saved.exists():
        from pdd_search import parse_goods
        return parse_goods(saved.read_text(encoding="utf-8", errors="replace"))
    return []


def list_keywords():
    found = {}
    if DATA_DIR.exists():
        for path in DATA_DIR.glob("*.jsonl"):
            rows = read_jsonl(path)
            found[path.stem] = len(rows)
    if (ROOT / "pdd.html").exists() and "蓝牙耳机" not in found:
        found["蓝牙耳机"] = len(load_goods("蓝牙耳机"))
    return [{"keyword": name, "count": count} for name, count in found.items()]


def merge_goods(keyword, new_rows):
    """新结果并进旧文件。没有新商品时不覆盖，避免一次失败把历史清掉。"""
    old = read_jsonl(result_path(keyword))
    merged = {row.get("goods_id"): row for row in old if row.get("goods_id")}
    for row in new_rows:
        goods_id = row.get("goods_id")
        if not goods_id:
            continue
        merged[goods_id] = {**merged.get(goods_id, {}), **row}
    rows = list(merged.values())
    if rows:
        write_jsonl(result_path(keyword), rows)
    return rows


def run_job(keyword, limit, filters):
    from pdd_search import run_search

    try:
        goods, total, errors = run_search(keyword, limit=limit, filters=filters)
        saved = merge_goods(keyword, goods) if goods else read_jsonl(result_path(keyword))
        if goods:
            message = f"本次新增或更新 {len(goods)} 条，库里共 {len(saved)} 条"
        elif saved:
            message = (errors[-1] + "。原来的数据还在。") if errors else "这次没有新商品，原来的数据还在。"
        else:
            message = errors[-1] if errors else "没有抓到商品"
        status = "ok" if goods and not errors else ("partial" if saved else "failed")
        with job_lock:
            job.update({
                "running": False,
                "status": status,
                "message": message,
                "count": len(saved),
                "total": total,
            })
    except Exception as exc:
        with job_lock:
            job.update({
                "running": False,
                "status": "failed",
                "message": str(exc),
            })


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            with job_lock:
                current = dict(job)
            self.send_json({"keywords": list_keywords(), "job": current})
            return
        if parsed.path == "/api/results":
            keyword = parse_qs(parsed.query).get("keyword", [""])[0]
            self.send_json({"keyword": keyword, "goods": load_goods(keyword)})
            return
        if parsed.path.startswith("/icons/"):
            self.serve_icon(parsed.path)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        keyword = parse_qs(parsed.query).get("keyword", [""])[0]
        goods_id = parse_qs(parsed.query).get("goods_id", [""])[0]
        if parsed.path != "/api/results" or not keyword:
            self.send_json({"ok": False, "message": "没有指定要删除的词条"}, 400)
            return
        path = result_path(keyword)
        if goods_id:
            rows = [row for row in read_jsonl(path) if row.get("goods_id") != goods_id]
            write_jsonl(path, rows)
            self.send_json({"ok": True, "count": len(rows)})
            return
        if path.exists():
            path.unlink()
        self.send_json({"ok": True, "count": 0})

    def do_POST(self):
        if urlparse(self.path).path != "/api/search":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_json({"ok": False, "message": "请求格式不对"}, 400)
            return
        keyword = str(payload.get("keyword") or "").strip()
        if not keyword:
            self.send_json({"ok": False, "message": "请先填写搜索词"}, 400)
            return
        if len(keyword) > 40:
            self.send_json({"ok": False, "message": "搜索词不要超过 40 个字"}, 400)
            return
        limit = payload.get("limit") or 300
        try:
            limit = max(20, min(500, int(limit)))
        except (TypeError, ValueError):
            limit = 300
        filters = read_filters(payload)
        with job_lock:
            if job["running"]:
                self.send_json({"ok": False, "message": "已有抓取在进行，请等它结束"}, 409)
                return
            job.update({
                "running": True,
                "keyword": keyword,
                "status": "running",
                "message": "正在打开搜索页",
                "count": 0,
                "total": None,
            })
        threading.Thread(target=run_job, args=(keyword, limit, filters), daemon=True).start()
        self.send_json({"ok": True, "keyword": keyword})

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_icon(self, path):
        relative = path.removeprefix("/icons/").replace("\\", "/")
        file_path = (ROOT / "icons" / relative).resolve()
        if not str(file_path).startswith(str((ROOT / "icons").resolve())) or not file_path.is_file():
            self.send_error(404)
            return
        data = file_path.read_bytes()
        kind = "image/png" if file_path.suffix.lower() == ".png" else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def serve():
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def open_window():
    url = f"http://{HOST}:{PORT}"
    icon = ROOT / "icons" / "app.ico"
    try:
        import webview
        webview.create_window(
            "拼多多数据挖掘",
            url,
            width=1280,
            height=820,
            min_size=(880, 560),
            text_select=True,
        )
        webview.start()
        return
    except Exception:
        import webbrowser
        webbrowser.open(url)
        print(f"拼多多数据挖掘已打开：{url}")
        print("关闭这个窗口会退出程序。")
        threading.Event().wait()


if __name__ == "__main__":
    serve()
    open_window()
