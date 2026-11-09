import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from pdd.server import http as app
from pdd.storage import results, library
from pdd.services import auth as auth_service, jobs
from pdd.scraping import filters as filter_tools, service
from pdd.browser.view import CaptureView


class CaptureViewTests(unittest.TestCase):
    def test_rejects_invalid_input_and_clears_pending_commands(self):
        view = CaptureView()
        with self.assertRaises(ValueError):
            view.submit({"action": "text", "text": "closed"})
        view.open()
        for payload in ({"action": "script", "text": "alert(1)"},
                        {"action": "pointer", "points": [[float("nan"), 0, 0]]},
                        {"action": "pointer", "points": [[2, 0, 0]]},
                        {"action": "pointer", "points": [[0, 0, 0]] * 129},
                        {"action": "key", "key": "Control+L"},
                        {"action": "wheel", "delta": 99999}):
            with self.assertRaises(ValueError):
                view.submit(payload)
        view.submit({"action": "text", "text": "fixture-input"})
        view.close()
        self.assertTrue(view._commands.empty())
        self.assertIsNone(view.snapshot()["image"])

    def test_http_job_scope_and_same_origin(self):
        view = CaptureView()
        current = {"running": True, "id": "fixture-current", "browser_available": True, "status": "verification", "intervention_kind": "verification"}
        resume = threading.Event()
        with patch.object(jobs, "job", current), patch.object(jobs, "job_capture_view", view), \
             patch.object(jobs, "job_resume_request", resume):
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            origin = f"http://127.0.0.1:{server.server_port}"

            def request(payload=None, headers=None, path="/api/search/browser"):
                data = json.dumps(payload).encode() if payload is not None else None
                req = Request(origin + path, data=data, headers=headers or {})
                try:
                    with urlopen(req) as response:
                        return response.status, json.load(response)
                except HTTPError as response:
                    return response.code, json.load(response)

            headers = {"X-Capture-View": "1", "Content-Type": "application/json", "Origin": origin}
            try:
                payload = {"action": "open", "job_id": "fixture-current"}
                self.assertEqual(request(payload)[0], 403)
                self.assertEqual(request(payload, {**headers, "Origin": "https://example.com"})[0], 403)
                self.assertEqual(request({**payload, "job_id": "old"}, headers)[0], 409)
                for status, kind in (("running", None), ("recovering", "verification"), ("verification", "loading")):
                    current.update(status=status, intervention_kind=kind)
                    self.assertEqual(request(payload, headers)[0], 409)
                current.update(status="verification", intervention_kind="verification")
                self.assertEqual(request(payload, headers)[0], 200)
                self.assertTrue(view.opened.is_set())
                self.assertEqual(request(headers=headers, path="/api/search/browser/frame?job_id=old")[0], 409)
                self.assertEqual(request(headers=headers, path="/api/search/browser/frame?job_id=fixture-current")[0], 200)
                self.assertEqual(request({**payload, "action": "resume"}, headers)[0], 200)
                self.assertTrue(resume.is_set())
                self.assertTrue(view.opened.is_set())  # 点击继续不应丢弃未完成的验证界面。
                current["running"] = False
                self.assertEqual(request(payload, headers)[0], 409)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
