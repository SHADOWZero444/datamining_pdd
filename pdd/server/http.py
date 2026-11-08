"""本机 HTTP 协议适配：把请求交给存储、登录与抓取服务。"""
import re
import threading
from .security import LocalHandler
from urllib.parse import parse_qs, urlparse
from pdd.browser.view import CaptureView
from pdd.storage import results, library
from pdd.services import auth as auth_service, jobs
from pdd.scraping.filters import read_filters

class AppHandler(LocalHandler):
    def handle_get(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/search/browser/frame":
            if not self.capture_request_allowed():
                return
            requested = parse_qs(parsed.query).get("job_id", [""])[0]
            with jobs.job_lock:
                if not jobs.job["running"] or requested != jobs.job.get("id") or jobs.job_capture_view is None:
                    self.send_json({"ok": False, "message": "本次抓取已结束。"}, 409)
                    return
                snapshot = jobs.job_capture_view.snapshot()
            self.send_json(snapshot)
            return
        if parsed.path == "/api/state":
            with jobs.job_lock:
                current = dict(jobs.job)
            self.send_json({"keywords": results.list_keywords(), "library": library.library_state(), "job": current, "auth": auth_service.auth_state()})
            return
        if parsed.path == "/api/cookie":
            self.send_json({"auth": auth_service.auth_state(include_cookie=True)})
            return
        if parsed.path == "/api/results":
            query = parse_qs(parsed.query)
            keyword = query.get("keyword", [""])[0]
            self.send_json(results.result_feed(keyword, query.get("revision", [None])[0]))
            return
        stream = self.send_head()
        if stream:
            try:
                self.copyfile(stream, self.wfile)
            finally:
                stream.close()

    def handle_delete(self):
        parsed = urlparse(self.path)
        keyword = parse_qs(parsed.query).get("keyword", [""])[0]
        goods_id = parse_qs(parsed.query).get("goods_id", [""])[0]
        if parsed.path != "/api/results" or not keyword:
            self.send_json({"ok": False, "message": "没有指定要删除的词条"}, 400)
            return
        path = results.result_path(keyword)
        with jobs.job_lock:
            if jobs.job["running"] and jobs.job["keyword"] == keyword:
                self.send_json({"ok": False, "message": "请先停止这个词条的抓取，再删除数据。"}, 409)
                return
            if goods_id:
                rows = [row for row in results.read_jsonl(path) if row.get("goods_id") != goods_id]
                results.write_jsonl(path, rows)
                self.send_json({"ok": True, "count": len(rows)})
                return
            if path.exists():
                path.unlink()
            meta = results.metadata_path(keyword)
            if meta.exists():
                meta.unlink()
            path.with_suffix(".run.json").unlink(missing_ok=True)
            library.remove_library_entries([keyword])
            self.send_json({"ok": True, "count": 0, "library": library.library_state()})

    def handle_post(self):
        path = urlparse(self.path).path
        if path == "/api/search/browser" and not self.capture_request_allowed():
            return
        payload = self.read_payload()
        if payload is None:
            return
        if path == "/api/cookie":
            action = payload.get("action")
            try:
                with jobs.job_lock:
                    if jobs.job["running"] or auth_service.auth["running"]:
                        raise RuntimeError("抓取正在使用登录会话，请先停止抓取再更改登录信息。")
                    if action == "save":
                        auth_service.save_cookie(payload.get("cookie"))
                        with auth_service.auth_lock:
                            auth_service.auth.update({"running": False, "status": "ok", "message": "登录信息已保存，可以开始抓取。"})
                    elif action == "clear":
                        auth_service.clear_cookie()
                        with auth_service.auth_lock:
                            auth_service.auth.update({"running": False, "status": "idle", "message": "已清除本机保存的登录信息。"})
                    elif action == "login":
                        with auth_service.auth_lock:
                            if auth_service.auth["running"]:
                                raise RuntimeError("登录窗口已经打开，请先完成登录。")
                            auth_service.auth.update({"running": True, "status": "starting", "message": "正在打开登录窗口…"})
                        threading.Thread(target=auth_service.retrieve_login_cookie, daemon=True).start()
                    else:
                        raise RuntimeError("无法处理这个操作。")
                self.send_json({"ok": True, "auth": auth_service.auth_state(include_cookie=True)})
            except RuntimeError as exc:
                self.send_json({"ok": False, "message": str(exc)}, 400)
            return
        if path == "/api/search/browser":
            with jobs.job_lock:
                if not jobs.job["running"] or not jobs.job.get("browser_available") or jobs.job_capture_view is None:
                    self.send_json({"ok": False, "message": "抓取页面尚未就绪或已经关闭。"}, 409)
                    return
                if payload.get("job_id") != jobs.job.get("id"):
                    self.send_json({"ok": False, "message": "抓取任务已变化，请刷新后重试。"}, 409)
                    return
                action = payload.get("action")
                if action == "resume" and jobs.job["status"] == "paused":
                    if jobs.job.get("intervention_kind") != "rate_limit_ready" or jobs.job_resume_request is None:
                        self.send_json({"ok": False, "message": "请等待当前冷却结束后再继续。"}, 409)
                        return
                    jobs.job_resume_request.set()
                    jobs.job.update(status="resuming", message="正在恢复抓取…")
                    self.send_json({"ok": True})
                    return
                if action != "close" and not (jobs.job["status"] == "verification" and jobs.job.get("intervention_kind") == "verification"):
                    self.send_json({"ok": False, "message": "当前无需人工验证，抓取会在后台进行。"}, 409)
                    return
                if action == "open":
                    jobs.job_capture_view.open()
                elif action == "close":
                    jobs.job_capture_view.close()
                elif action == "resume" and jobs.job_resume_request is not None and jobs.job["status"] == "verification":
                    jobs.job_resume_request.set()
                    jobs.job_capture_view.close()
                elif action in ("pointer", "wheel", "key", "text"):
                    try:
                        jobs.job_capture_view.submit(payload)
                    except ValueError as exc:
                        self.send_json({"ok": False, "message": str(exc)}, 400)
                        return
                else:
                    self.send_json({"ok": False, "message": "当前无需执行这个操作。"}, 400)
                    return
            self.send_json({"ok": True})
            return
        if path == "/api/search/stop":
            with jobs.job_lock:
                if not jobs.job["running"] or jobs.job_cancel is None:
                    self.send_json({"ok": False, "message": "当前没有正在进行的抓取。"}, 409)
                    return
                jobs.job_cancel.set()
                jobs.job["status"] = "stopping"
                jobs.job["message"] = "正在停止，已读取的商品会保留。"
            self.send_json({"ok": True})
            return
        if path == "/api/library":
            try:
                with jobs.job_lock:
                    response = library.update_library(payload, jobs.job["keyword"] if jobs.job["running"] else None)
                self.send_json(response)
            except RuntimeError as exc:
                self.send_json({"ok": False, "message": str(exc)}, 400)
            return
        if path != "/api/search":
            self.send_error(404)
            return
        if not isinstance(payload.get("keyword"), str):
            raise ValueError("搜索词必须为文字。")
        keyword = payload["keyword"].strip()
        if not keyword:
            self.send_json({"ok": False, "message": "请先填写搜索词"}, 400)
            return
        if len(keyword) > 40:
            self.send_json({"ok": False, "message": "搜索词不要超过 40 个字"}, 400)
            return
        raw_limit = payload.get("limit")
        limit = None
        if raw_limit not in (None, "") and (not re.fullmatch(r"[1-9][0-9]*", str(raw_limit)) or int(raw_limit) > 10000):
            self.send_json({"ok": False, "message": "累计商品目标请留空，或填写 1 到 10000 之间的整数。"}, 400)
            return
        if raw_limit not in (None, ""):
            limit = int(raw_limit)
        filters = read_filters(payload)
        try:
            jobs.start_job(keyword, limit, filters)
        except RuntimeError as exc:
            self.send_json({"ok": False, "message": str(exc)}, 409)
            return
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

