"""本地 HTTP 边界：来源校验、有界 JSON 请求和统一错误响应。"""
import json
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlparse, unquote

from pdd.assets import RUNTIME_ASSETS
from pdd.paths import bundle_dir


class LocalHandler(SimpleHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def request_allowed(self, api=False):
        # 即使 DNS 指向回环地址，也必须使用本机明确的 Host 和当前端口。
        host = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        if (host not in expected or (origin is not None and origin != "http://" + host)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or (api and self.headers.get("X-PDD-App") != "1"
                    and self.headers.get("X-Capture-View") != "1")):
            self.send_json({"ok": False, "message": "请在软件内执行此操作。"}, 403)
            return False
        return True

    def dispatch(self, method):
        if not self.request_allowed(urlparse(self.path).path.startswith("/api/")):
            return
        try:
            method()
        except ValueError as exc:
            self.send_json({"ok": False, "message": str(exc) or "请求参数无效。"}, 400)
        except OSError:
            self.send_json({"ok": False, "message": "无法读写本机数据，请检查软件目录权限和磁盘空间。"}, 500)

    def do_GET(self):
        self.dispatch(self.handle_get)

    def do_POST(self):
        self.dispatch(self.handle_post)

    def do_DELETE(self):
        self.dispatch(self.handle_delete)

    def do_HEAD(self):
        if self.request_allowed():
            super().do_HEAD()

    def read_payload(self, max_bytes=65536):
        if self.headers.get_content_type() != "application/json":
            self.send_json({"ok": False, "message": "请求必须使用 JSON 格式。"}, 415)
            return None
        raw = self.headers.get("Content-Length", "0")
        if not raw.isascii() or not raw.isdigit() or self.headers.get("Transfer-Encoding"):
            raise ValueError("请求长度无效。")
        length = int(raw)
        if length > max_bytes:
            self.send_json({"ok": False, "message": "请求内容过长。"}, 413)
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("请求格式不对。") from None
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return payload

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_head(self):
        path = unquote(urlparse(self.path).path)
        relative = "web/index.html" if path == "/" else (path.lstrip("/") if path.startswith("/icons/") else "web/" + path.lstrip("/"))
        root = bundle_dir().resolve()
        target = (root / relative).resolve()
        if relative not in RUNTIME_ASSETS or not target.is_relative_to(root) or not target.is_file():
            self.send_error(404)
            return None
        stream = target.open("rb")
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(target)))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        return stream

    def log_message(self, fmt, *args):
        return
