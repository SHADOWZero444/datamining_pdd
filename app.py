import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import OrderedDict, deque
from functools import lru_cache
from uuid import uuid4
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from capture_view import CaptureView

def program_dir():
    """用户看到的软件目录。数据和登录文件都放在这里。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir():
    """页面和图标所在目录。打包后在程序内部，开发时就是项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ROOT = program_dir()
WEB_ROOT = bundle_dir() / "web"
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
job_cancel = None
job_browser_request = None
job_resume_request = None
job_capture_view = None
data_lock = threading.RLock()
result_feeds = OrderedDict()


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


def metadata_path(keyword):
    return DATA_DIR / f"{safe_name(keyword)}.meta.json"


def storage_path(keyword):
    """返回给界面显示的相对保存位置，避免泄露本机绝对目录。"""
    return (Path("data") / "searches" / f"{safe_name(keyword)}.jsonl").as_posix()


def search_metadata(keyword):
    defaults = {
        "keyword": keyword,
        "filters": {"tags": [], "banned": "", "price_min": None, "price_max": None},
        "storage_path": storage_path(keyword),
    }
    path = metadata_path(keyword)
    if not path.exists():
        return defaults
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    defaults["filters"].update(saved.get("filters") or {})
    defaults["keyword"] = saved.get("keyword") or keyword
    return defaults


def save_search_metadata(keyword, filters):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "keyword": keyword,
        "filters": filters,
        "storage_path": storage_path(keyword),
        "saved_at": int(time.time()),
    }
    metadata_path(keyword).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path):
    with data_lock:
        if not path.exists():
            return []
        stat = path.stat()
        return [dict(row) for row in _read_jsonl_cached(str(path), stat.st_mtime_ns, stat.st_size)]


@lru_cache(maxsize=16)
def _read_jsonl_cached(filename, modified, size):
    path = Path(filename)
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
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    atomic_write(path, text + ("\n" if text else ""))


def atomic_write(path, text):
    """同目录替换，轮询和异常退出都不会读到只写了一半的文件。"""
    with data_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as stream:
                temp_path = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()


def result_feed(keyword, revision=None):
    """返回变动商品；历史游标过期或应用重启时自动返回完整快照。"""
    with data_lock:
        rows = load_goods(keyword)
        current = {row["goods_id"]: row for row in rows if row.get("goods_id")}
        feed = result_feeds.get(keyword)
        if feed is None:
            feed = {"rows": current, "revision": uuid4().hex, "history": deque(maxlen=16)}
            result_feeds[keyword] = feed
        elif current != feed["rows"]:
            changed = [row for key, row in current.items() if feed["rows"].get(key) != row]
            removed = list(feed["rows"].keys() - current.keys())
            previous = feed["revision"]
            feed["revision"] = uuid4().hex
            # 单次大变动直接回退完整快照，避免在历史队列里重复保留大量数据。
            if len(changed) + len(removed) > 2000:
                feed["history"].clear()
            else:
                feed["history"].append((previous, changed, removed))
            feed["rows"] = current
        result_feeds.move_to_end(keyword)
        while len(result_feeds) > 8:
            result_feeds.popitem(last=False)
        payload = {"keyword": keyword, "revision": feed["revision"],
                   "metadata": search_metadata(keyword), "count": len(rows)}
        if revision == feed["revision"]:
            return {**payload, "unchanged": True}
        history = list(feed["history"])
        start = next((i for i, event in enumerate(history) if event[0] == revision), None)
        if start is not None:
            changes, removed = {}, set()
            for _, updated, deleted in history[start:]:
                for key in deleted:
                    changes.pop(key, None)
                    removed.add(key)
                for row in updated:
                    changes[row["goods_id"]] = row
                    removed.discard(row["goods_id"])
            return {**payload, "delta": True, "goods": list(changes.values()), "removed": list(removed)}
        return {**payload, "goods": rows}


def ensure_seq(rows):
    """给没有序号的商品补一个不重复的 ID。已有序号保持不变。"""
    used = [int(row["seq"]) for row in rows if str(row.get("seq", "")).isdigit()]
    next_id = max(used, default=0) + 1
    changed = False
    for row in rows:
        if not str(row.get("seq", "")).isdigit():
            row["seq"] = next_id
            next_id += 1
            changed = True
    return changed


def load_goods(keyword):
    rows = read_jsonl(result_path(keyword))
    if rows:
        if ensure_seq(rows):
            write_jsonl(result_path(keyword), rows)
        return rows
    # 还没抓过时，用你保存的搜索页，方便界面先看到真实商品。
    saved = ROOT / "pdd.html"
    if keyword == "蓝牙耳机" and saved.exists():
        from pdd_search import parse_goods
        return parse_goods(saved.read_text(encoding="utf-8", errors="replace"))
    return []


def cookie_path():
    return ROOT / "cookie.txt"


def read_cookie():
    path = cookie_path()
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def save_cookie(value):
    text = str(value or "").strip()
    if not text or "=" not in text:
        raise RuntimeError("登录信息格式不正确，请粘贴完整的 cookie。")
    cookie_path().write_text(text, encoding="utf-8")


def clear_cookie():
    path = cookie_path()
    if path.exists():
        path.unlink()


auth = {"running": False, "status": "idle", "message": "尚未设置登录信息"}
auth_lock = threading.Lock()


def auth_state(include_cookie=False):
    with auth_lock:
        current = dict(auth)
    value = read_cookie()
    current["configured"] = bool(value)
    if include_cookie:
        current["cookie"] = value
    return current


def retrieve_login_cookie():
    """打开用户主动发起的登录窗口，只读取本程序登录页产生的拼多多 cookie。"""
    from pdd_search import open_login_and_capture_cookie

    try:
        with auth_lock:
            auth.update({"running": True, "status": "waiting", "message": "登录窗口已打开，请完成登录。"})
        value = open_login_and_capture_cookie()
        save_cookie(value)
        with auth_lock:
            auth.update({"running": False, "status": "ok", "message": "登录信息已导入，可以开始抓取。"})
    except Exception as exc:
        with auth_lock:
            auth.update({"running": False, "status": "failed", "message": str(exc) or "未能获取登录信息。"})


LIBRARY_FILE = ROOT / "data" / "library.json"


def load_library():
    if LIBRARY_FILE.exists():
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"groups": [], "entries": []}
    else:
        data = {"groups": [], "entries": []}
    data.setdefault("groups", [])
    data.setdefault("entries", [])
    known = {item.get("keyword") for item in data["entries"]}
    if DATA_DIR.exists():
        for path in DATA_DIR.glob("*.jsonl"):
            rows = read_jsonl(path)
            keyword = rows[0].get("keyword") if rows and rows[0].get("keyword") else path.stem
            if keyword not in known:
                data["entries"].append({"keyword": keyword, "title": keyword, "pinned": False, "groupId": ""})
                known.add(keyword)
    return data


def save_library(data):
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def library_state():
    library = load_library()
    entries = []
    for entry in library["entries"]:
        keyword = entry.get("keyword") or ""
        if not keyword:
            continue
        entries.append({
            "keyword": keyword,
            "title": entry.get("title") or keyword,
            "pinned": bool(entry.get("pinned")),
            "groupId": entry.get("groupId") or "",
            "count": len(read_jsonl(result_path(keyword))),
        })
    entries.sort(key=lambda item: (not item["pinned"], item["title"]))
    return {"groups": library["groups"], "entries": entries}


def update_library(payload):
    library = load_library()
    action = payload.get("action")
    if action == "create-group":
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RuntimeError("请填写分组名称。")
        library["groups"].append({"id": f"g{int(time.time())}", "name": name, "open": True, "pinned": False})
    elif action == "toggle-group":
        group = next((item for item in library["groups"] if item["id"] == payload.get("groupId")), None)
        if group:
            group["open"] = not group.get("open", True)
    elif action in ("rename-group", "pin-group", "delete-group"):
        group = next((item for item in library["groups"] if item["id"] == payload.get("groupId")), None)
        if not group:
            raise RuntimeError("没有找到这个分组。")
        if action == "rename-group":
            name = str(payload.get("name") or "").strip()
            if not name:
                raise RuntimeError("请填写新的分组名称。")
            group["name"] = name
        elif action == "pin-group":
            group["pinned"] = not group.get("pinned", False)
        else:
            for entry in library["entries"]:
                if entry.get("groupId") == group["id"]:
                    entry["groupId"] = ""
            library["groups"] = [item for item in library["groups"] if item.get("id") != group["id"]]
    elif action == "rename":
        entry = next((item for item in library["entries"] if item.get("keyword") == payload.get("keyword")), None)
        title = str(payload.get("title") or "").strip()
        if not entry or not title:
            raise RuntimeError("请填写新的标题。")
        entry["title"] = title
    elif action == "pin":
        entry = next((item for item in library["entries"] if item.get("keyword") == payload.get("keyword")), None)
        if entry:
            entry["pinned"] = not entry.get("pinned")
    elif action == "move":
        keywords = payload.get("keywords") or ([payload.get("keyword")] if payload.get("keyword") else [])
        for entry in library["entries"]:
            if entry.get("keyword") in keywords:
                entry["groupId"] = payload.get("groupId") or ""
    elif action == "delete":
        keywords = set(payload.get("keywords") or [])
        with job_lock:
            if job["running"] and job["keyword"] in keywords:
                raise RuntimeError("请先停止这个词条的抓取，再删除数据。")
        library["entries"] = [item for item in library["entries"] if item.get("keyword") not in keywords]
        for keyword in keywords:
            path = result_path(keyword)
            if path.exists():
                path.unlink()
            meta = metadata_path(keyword)
            if meta.exists():
                meta.unlink()
            (DATA_DIR / f"{safe_name(keyword)}.run.json").unlink(missing_ok=True)
    else:
        raise RuntimeError("无法处理这个操作。")
    save_library(library)
    return {"ok": True, "library": library_state()}


def ensure_library_entry(keyword):
    library = load_library()
    if not any(item.get("keyword") == keyword for item in library["entries"]):
        library["entries"].append({"keyword": keyword, "title": keyword, "pinned": False, "groupId": ""})
        save_library(library)


def remove_library_entries(keywords):
    library = load_library()
    removed = set(keywords)
    library["entries"] = [item for item in library["entries"] if item.get("keyword") not in removed]
    save_library(library)


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
        previous = merged.get(goods_id, {})
        row["seq"] = previous.get("seq") or row.get("seq")
        if not row.get("shop_name"):
            row["shop_name"] = previous.get("shop_name") or ""
        merged[goods_id] = {**previous, **row}
    rows = list(merged.values())
    ensure_seq(rows)
    if rows:
        write_jsonl(result_path(keyword), rows)
    return rows


def run_job(keyword, limit, filters, cancel_event):
    diagnostics = {}
    capture_view = job_capture_view
    try:
        from pdd_search import run_search

        def report_progress(message, count, total=None):
            with job_lock:
                if job.get("running") and not cancel_event.is_set():
                    job.update({"message": message, "count": count, "total": total,
                                "scanned": diagnostics.get("scanned", 0)})

        def save_batch(rows):
            with data_lock:
                saved = merge_goods(keyword, rows)
            with job_lock:
                job["saved"] = len(saved)

        def report_intervention(status, message, kind):
            with job_lock:
                if not cancel_event.is_set():
                    job.update(status=status, message=message, intervention_kind=kind)

        def browser_ready(ready):
            with job_lock:
                job["browser_available"] = ready

        goods, total, errors = run_search(
            keyword,
            limit=limit,
            filters=filters,
            cancel_event=cancel_event,
            progress=report_progress,
            on_batch=save_batch,
            diagnostics=diagnostics,
            on_intervention=report_intervention,
            browser_request=job_browser_request,
            resume_request=job_resume_request,
            on_browser_ready=browser_ready,
            capture_view=capture_view,
        )
        saved = merge_goods(keyword, goods) if goods else read_jsonl(result_path(keyword))
        stopped = cancel_event.is_set()
        if stopped:
            message = f"已停止抓取，已保留 {len(saved)} 条商品"
            status = "stopped"
        elif goods:
            reason = errors[-1] if errors else diagnostics.get("message", "本次抓取结束")
            message = f"{reason}。本次保存 {len(goods)} 条，库里共 {len(saved)} 条"
            status = "ok" if not errors else "partial"
        elif saved:
            message = (errors[-1] + "。原来的数据还在。") if errors else "这次没有新商品，原来的数据还在。"
            status = "partial"
        else:
            message = errors[-1] if errors else "没有抓到商品"
            status = "failed"
        with job_lock:
            job.update({
                "running": False,
                "status": status,
                "message": message,
                "count": len(saved),
                "total": total,
                "stop_reason": diagnostics.get("stop_reason"),
                "browser_available": False,
                "intervention_kind": None,
            })
    except Exception as exc:
        saved = read_jsonl(result_path(keyword))
        with job_lock:
            job.update({
                "running": False,
                "status": "partial" if saved else "failed",
                "message": f"抓取中断，已保存的数据仍然保留。{str(exc)}",
                "count": len(saved),
                "browser_available": False,
            })
        diagnostics.update(stop_reason="error", error_type=type(exc).__name__)
    finally:
        if capture_view:
            capture_view.close()
        diagnostics.update(keyword=keyword, limit=limit, finished_at=int(time.time()))
        try:
            atomic_write(DATA_DIR / f"{safe_name(keyword)}.run.json",
                         json.dumps(diagnostics, ensure_ascii=False, indent=2))
        except OSError:
            pass


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/search/browser/frame":
            if not self.capture_request_allowed():
                return
            requested = parse_qs(parsed.query).get("job_id", [""])[0]
            with job_lock:
                if not job["running"] or requested != job.get("id") or job_capture_view is None:
                    self.send_json({"ok": False, "message": "本次抓取已结束。"}, 409)
                    return
                snapshot = job_capture_view.snapshot()
            self.send_json(snapshot)
            return
        if parsed.path == "/api/state":
            with job_lock:
                current = dict(job)
            self.send_json({"keywords": list_keywords(), "library": library_state(), "job": current, "auth": auth_state()})
            return
        if parsed.path == "/api/cookie":
            self.send_json({"auth": auth_state(include_cookie=True)})
            return
        if parsed.path == "/api/results":
            query = parse_qs(parsed.query)
            keyword = query.get("keyword", [""])[0]
            self.send_json(result_feed(keyword, query.get("revision", [None])[0]))
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
        with job_lock:
            if job["running"] and job["keyword"] == keyword:
                self.send_json({"ok": False, "message": "请先停止这个词条的抓取，再删除数据。"}, 409)
                return
        if goods_id:
            rows = [row for row in read_jsonl(path) if row.get("goods_id") != goods_id]
            write_jsonl(path, rows)
            self.send_json({"ok": True, "count": len(rows)})
            return
        if path.exists():
            path.unlink()
        meta = metadata_path(keyword)
        if meta.exists():
            meta.unlink()
        (DATA_DIR / f"{safe_name(keyword)}.run.json").unlink(missing_ok=True)
        remove_library_entries([keyword])
        self.send_json({"ok": True, "count": 0, "library": library_state()})

    def do_POST(self):
        global job_cancel, job_browser_request, job_resume_request, job_capture_view
        path = urlparse(self.path).path
        if path == "/api/search/browser" and not self.capture_request_allowed():
            return
        length = int(self.headers.get("Content-Length", "0"))
        if path == "/api/search/browser" and not 0 <= length <= 65536:
            self.send_json({"ok": False, "message": "操作内容过长。"}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            self.send_json({"ok": False, "message": "请求格式不对"}, 400)
            return
        if path == "/api/cookie":
            action = payload.get("action")
            try:
                with job_lock:
                    if job["running"]:
                        raise RuntimeError("抓取正在使用登录会话，请先停止抓取再更改登录信息。")
                if action == "save":
                    save_cookie(payload.get("cookie"))
                    with auth_lock:
                        auth.update({"running": False, "status": "ok", "message": "登录信息已保存，可以开始抓取。"})
                elif action == "clear":
                    clear_cookie()
                    with auth_lock:
                        auth.update({"running": False, "status": "idle", "message": "已清除本机保存的登录信息。"})
                elif action == "login":
                    with auth_lock:
                        if auth["running"]:
                            raise RuntimeError("登录窗口已经打开，请先完成登录。")
                        auth.update({"running": True, "status": "starting", "message": "正在打开登录窗口…"})
                    threading.Thread(target=retrieve_login_cookie, daemon=True).start()
                else:
                    raise RuntimeError("无法处理这个操作。")
                self.send_json({"ok": True, "auth": auth_state(include_cookie=True)})
            except RuntimeError as exc:
                self.send_json({"ok": False, "message": str(exc)}, 400)
            return
        if path == "/api/search/browser":
            with job_lock:
                if not job["running"] or not job.get("browser_available") or job_capture_view is None:
                    self.send_json({"ok": False, "message": "抓取页面尚未就绪或已经关闭。"}, 409)
                    return
                if payload.get("job_id") != job.get("id"):
                    self.send_json({"ok": False, "message": "抓取任务已变化，请刷新后重试。"}, 409)
                    return
                action = payload.get("action")
                if action != "close" and not (job["status"] == "verification" and job.get("intervention_kind") == "verification"):
                    self.send_json({"ok": False, "message": "当前无需人工验证，抓取会在后台进行。"}, 409)
                    return
                if action == "open":
                    job_capture_view.open()
                elif action == "close":
                    job_capture_view.close()
                elif action == "resume" and job_resume_request is not None and job["status"] == "verification":
                    job_resume_request.set()
                    job_capture_view.close()
                elif action in ("pointer", "wheel", "key", "text"):
                    try:
                        job_capture_view.submit(payload)
                    except ValueError as exc:
                        self.send_json({"ok": False, "message": str(exc)}, 400)
                        return
                else:
                    self.send_json({"ok": False, "message": "当前无需执行这个操作。"}, 400)
                    return
            self.send_json({"ok": True})
            return
        if path == "/api/search/stop":
            with job_lock:
                if not job["running"] or job_cancel is None:
                    self.send_json({"ok": False, "message": "当前没有正在进行的抓取。"}, 409)
                    return
                job_cancel.set()
                job["status"] = "stopping"
                job["message"] = "正在停止，已读取的商品会保留。"
            self.send_json({"ok": True})
            return
        if path == "/api/library":
            try:
                self.send_json(update_library(payload))
            except RuntimeError as exc:
                self.send_json({"ok": False, "message": str(exc)}, 400)
            return
        if path != "/api/search":
            self.send_error(404)
            return
        keyword = str(payload.get("keyword") or "").strip()
        if not keyword:
            self.send_json({"ok": False, "message": "请先填写搜索词"}, 400)
            return
        if len(keyword) > 40:
            self.send_json({"ok": False, "message": "搜索词不要超过 40 个字"}, 400)
            return
        raw_limit = payload.get("limit")
        limit = None
        if raw_limit not in (None, "") and (not re.fullmatch(r"[1-9][0-9]*", str(raw_limit)) or int(raw_limit) > 10000):
            self.send_json({"ok": False, "message": "抓取上限请留空，或填写 1 到 10000 之间的整数。"}, 400)
            return
        if raw_limit not in (None, ""):
            limit = int(raw_limit)
        filters = read_filters(payload)
        with auth_lock:
            if auth["running"]:
                self.send_json({"ok": False, "message": "请先完成当前登录，再开始抓取。"}, 409)
                return
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
                "scanned": 0,
                "saved": 0,
                "stop_reason": None,
                "id": uuid4().hex,
                "browser_available": False,
                "intervention_kind": None,
            })
            job_cancel = threading.Event()
            job_browser_request = threading.Event()
            job_resume_request = threading.Event()
            job_capture_view = CaptureView()
        save_search_metadata(keyword, filters)
        ensure_library_entry(keyword)
        # 关闭主窗口后仍让任务完成取消、落盘和浏览器退出，避免遗留不可见进程。
        threading.Thread(target=run_job, args=(keyword, limit, filters, job_cancel), daemon=False).start()
        self.send_json({"ok": True, "keyword": keyword})

    def capture_request_allowed(self):
        # 自定义头让外部网页必须先通过 CORS 预检；本站不开放跨域权限。
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        valid_host = urlparse("http://" + host).hostname in ("127.0.0.1", "localhost")
        if (not valid_host or self.headers.get("X-Capture-View") != "1"
                or (origin is not None and origin != "http://" + host)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.send_json({"ok": False, "message": "请在软件内操作验证页面。"}, 403)
            return False
        return True

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_icon(self, path):
        relative = path.removeprefix("/icons/").replace("\\", "/")
        icons_dir = (bundle_dir() / "icons").resolve()
        file_path = (icons_dir / relative).resolve()
        if not str(file_path).startswith(str(icons_dir)) or not file_path.is_file():
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
    icon = bundle_dir() / "icons" / "app.ico"
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
    server = serve()
    try:
        open_window()
    finally:
        with job_lock:
            if job_cancel:
                job_cancel.set()
            if job_capture_view:
                job_capture_view.close()
        server.shutdown()
        server.server_close()
