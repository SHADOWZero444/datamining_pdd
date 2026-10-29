"""离线回归：不读取用户 cookie，不访问拼多多，不写用户数据。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

import app
import pdd_search as scraper


def row(n, price="10"):
    return {"goods_id": str(n), "title": f"商品{n}", "price": price, "keyword": "测试"}


class Response:
    url = "https://mobile.pinduoduo.com/proxy/api/search"

    def __init__(self, data=None, status=200, retry=""):
        self.data, self.status = data or {}, status
        self.headers = {"retry-after": retry}

    def json(self):
        return self.data


class FakePage:
    def __init__(self, batches, responses=None, fail_at=None):
        self.batches = batches
        self.responses = responses or {}
        self.index = 0
        self.clock = 0
        self.callbacks = {}
        self.fail_at = fail_at
        self.wait_hook = None

    def on(self, event, callback):
        self.callbacks[event] = callback

    def remove_listener(self, event, callback):
        self.callbacks.pop(event, None)

    def goto(self, *args, **kwargs):
        for response in self.responses.get(0, []):
            self.callbacks["response"](response)

    def wait_for_timeout(self, ms):
        self.clock += ms / 1000
        if self.wait_hook:
            self.wait_hook()

    def scroll(self):
        self.index += 1
        if self.index == self.fail_at:
            raise RuntimeError("simulated page crash")
        for response in self.responses.get(self.index, []):
            self.callbacks["response"](response)


class CollectorTests(unittest.TestCase):
    def collect(self, page, **kwargs):
        current_state = kwargs.pop("page_state", "")
        with patch.object(scraper, "read_live_goods", side_effect=lambda _: page.batches[min(page.index, len(page.batches)-1)]), \
             patch.object(scraper, "read_dom_goods", return_value=[]), \
             patch.object(scraper, "scroll_results", side_effect=lambda _: page.scroll()), \
             patch.object(scraper, "page_state", return_value=current_state), \
             patch.object(scraper.time, "monotonic", side_effect=lambda: page.clock):
            return scraper.collect(page, **kwargs)

    def test_filtered_limit_and_batches_before_finish(self):
        page = FakePage([[row(n, "1") for n in range(20)], [row(n, "20") for n in range(20, 50)]])
        batches, stats = [], {}
        goods, _ = self.collect(page, limit=10, filters={"price_min": 10},
                                on_batch=batches.append, diagnostics=stats)
        self.assertEqual(len(goods), 10)
        self.assertEqual(len(batches[0]), 10)
        self.assertEqual(stats["scanned"], 50)
        self.assertEqual(stats["stop_reason"], "limit")

    def test_unlimited_exceeds_old_caps_and_ignores_total(self):
        page = FakePage([[row(n) for n in range(20)], [row(n) for n in range(20, 650)]],
                        {0: [Response({"total": 20, "items": []})], 2: [Response({"has_more": False, "items": []})]})
        stats, batches = {}, []
        goods, total = self.collect(page, diagnostics=stats, on_batch=batches.append)
        self.assertEqual(len(goods), 650)
        self.assertEqual(total, 20)
        self.assertEqual(stats["stop_reason"], "end")
        self.assertEqual(len(batches), 2)

    def test_stall_is_partial_and_listener_removed(self):
        page, stats = FakePage([[row(1)]]), {}
        goods, _ = self.collect(page, diagnostics=stats)
        self.assertEqual(stats["stop_reason"], "stalled")
        self.assertEqual(len(goods), 1)
        self.assertFalse(page.callbacks)
        self.assertGreaterEqual(page.clock, scraper.STALL_SECONDS)

    def test_cancel_during_rate_limit_preserves_data(self):
        event, stats = threading.Event(), {}
        page = FakePage([[row(1)]], {0: [Response(status=429, retry="30")]})
        page.wait_hook = event.set
        goods, _ = self.collect(page, should_stop=event.is_set, diagnostics=stats)
        self.assertEqual(len(goods), 1)
        self.assertEqual(stats["stop_reason"], "stopped")
        self.assertLessEqual(page.clock, .2)

    def test_access_denied_is_not_success(self):
        stats = {}
        self.collect(FakePage([[row(1)]], {0: [Response(status=403)]}), diagnostics=stats)
        self.assertEqual(stats["stop_reason"], "blocked")
        self.assertEqual(stats["http_errors"], {"403": 1})

    def test_busy_site_stops_without_manual_verification(self):
        stats, intervention = {}, Mock()
        goods, _ = self.collect(FakePage([[row(1)]]), diagnostics=stats,
                                page_state="busy", on_intervention=intervention)
        self.assertEqual(stats["stop_reason"], "site_busy")
        self.assertEqual(len(goods), 1)
        intervention.assert_not_called()

    def test_long_retry_after_stops_without_requesting_early(self):
        stats = {}
        page = FakePage([[row(1)]], {0: [Response(status=429, retry="Wed, 21 Oct 2099 07:28:00 GMT")]})
        self.collect(page, diagnostics=stats)
        self.assertEqual(stats["stop_reason"], "rate_limited")
        self.assertEqual(page.index, 0)

    def test_repeated_rate_limits_are_bounded(self):
        stats = {}
        responses = {i: [Response(status=429)] for i in range(4)}
        goods, _ = self.collect(FakePage([[row(1)]], responses), diagnostics=stats)
        self.assertEqual(stats["stop_reason"], "rate_limited")
        self.assertEqual(stats["rate_limits"], 4)
        self.assertEqual(len(goods), 1)

    def test_page_crash_keeps_preceding_batch(self):
        batches, stats = [], {}
        goods, _ = self.collect(FakePage([[row(1)]], fail_at=1), on_batch=batches.append, diagnostics=stats)
        self.assertEqual(goods, batches[0])
        self.assertEqual(stats["stop_reason"], "error")

    def test_unreadable_response_still_uses_rendered_goods(self):
        response, stats = Response(), {}
        with patch.object(response, "json", side_effect=RuntimeError("response body unavailable")):
            goods, _ = self.collect(FakePage([[row(1), row(2)]], {0: [response]}), limit=2, diagnostics=stats)
        self.assertEqual(len(goods), 2)
        self.assertEqual(stats["unreadable_responses"], 1)
        self.assertEqual(stats["stop_reason"], "limit")

    def test_delayed_first_page_and_duplicate_ids(self):
        page = FakePage([[], [], [row(1), row(1), row(2)]])
        goods, _ = self.collect(page, limit=2)
        self.assertEqual([x["goods_id"] for x in goods], ["1", "2"])

    def test_waits_for_loading_before_more_scrolling(self):
        class LoadingPage(FakePage):
            def goto(self, *args, **kwargs):
                self.request = Response()
                self.callbacks['request'](self.request)

            def scroll(self):
                self.first_scroll = self.clock
                super().scroll()

        page = LoadingPage([[row(1)], [row(2)]])
        def complete():
            if page.clock >= 3:
                page.callbacks['requestfinished'](page.request)
        page.wait_hook = complete
        goods, _ = self.collect(page, limit=2)
        self.assertEqual(len(goods), 2)
        self.assertGreaterEqual(page.first_scroll, 3)
        self.assertFalse(page.callbacks)

    def test_user_takeover_pauses_scroll_and_remains_cancellable(self):
        view = app.CaptureView()
        view.open()
        page = FakePage([[row(1)]])
        cancelled = threading.Event()
        page.wait_hook = cancelled.set
        stats = {}
        with patch.object(view, "service"):
            self.collect(page, capture_view=view, should_stop=cancelled.is_set, diagnostics=stats)
        self.assertEqual(page.index, 0)
        self.assertEqual(stats["stop_reason"], "stopped")
        self.assertLessEqual(page.clock, .2)

    def test_page_error_retry_is_bounded(self):
        button, stats = Mock(), {}
        page = FakePage([[row(1)]])
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, diagnostics=stats)
        self.assertEqual(button.click.call_count, 3)
        self.assertEqual(stats["page_retries"], 3)
        self.assertEqual(stats["stop_reason"], "stalled")

    def test_429_waits_before_clicking_page_retry(self):
        button, stats = Mock(), {}
        page = FakePage([[row(1)]], {0: [Response(status=429, retry="30")]})
        cancelled = threading.Event()
        clicked_at = []
        def clicked(**kwargs):
            clicked_at.append(page.clock)
            cancelled.set()
        button.click.side_effect = clicked
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, should_stop=cancelled.is_set, diagnostics=stats)
        self.assertEqual(button.click.call_count, 1)
        self.assertGreaterEqual(clicked_at[0], 29.999)
        self.assertEqual(stats["stop_reason"], "stopped")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(app, "ROOT", self.root),
                        patch.object(app, "DATA_DIR", self.root / "searches"),
                        patch.object(app, "LIBRARY_FILE", self.root / "library.json")]
        for item in self.patches:
            item.start()
        app.job.update(running=False, keyword="", status="idle")
        app.result_feeds.clear()
        app._read_jsonl_cached.cache_clear()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_delta_updates_deletes_and_unknown_revision(self):
        app.merge_goods("测试", [row(1), row(2)])
        first = app.result_feed("测试")
        self.assertTrue(app.result_feed("测试", first["revision"])["unchanged"])
        app.merge_goods("测试", [row(1, "12"), row(3)])
        second = app.result_feed("测试", first["revision"])
        self.assertTrue(second["delta"])
        self.assertEqual({r["goods_id"] for r in second["goods"]}, {"1", "3"})
        app.write_jsonl(app.result_path("测试"), [r for r in app.load_goods("测试") if r["goods_id"] != "2"])
        third = app.result_feed("测试", first["revision"])
        self.assertEqual(third["removed"], ["2"])
        self.assertEqual(len(app.result_feed("测试", "expired")["goods"]), 2)

    def test_atomic_write_failure_preserves_old_file(self):
        path = app.result_path("测试")
        app.write_jsonl(path, [row(1)])
        with patch.object(app.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                app.write_jsonl(path, [row(2)])
        self.assertEqual(app.read_jsonl(path), [row(1)])
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_run_job_publishes_before_return_and_preserves_on_error(self):
        event = threading.Event()
        def fake_search(keyword, on_batch, **kwargs):
            on_batch([row(1)])
            self.assertEqual(len(app.result_feed(keyword)["goods"]), 1)
            self.assertTrue(app.job["running"])
            raise RuntimeError("simulated browser crash")
        app.job.update(running=True, keyword="测试")
        with patch.object(scraper, "run_search", side_effect=fake_search):
            app.run_job("测试", None, {}, event)
        self.assertFalse(app.job["running"])
        self.assertEqual(app.job["status"], "partial")
        self.assertEqual(len(app.load_goods("测试")), 1)
        self.assertEqual(json.loads((app.DATA_DIR / "测试.run.json").read_text(encoding="utf-8"))["stop_reason"], "error")

    def test_http_stream_stop_and_blank_limit(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        ready, ended = threading.Event(), threading.Event()
        limits = []
        def fake_search(keyword, limit, on_batch, cancel_event, diagnostics, **kwargs):
            limits.append(limit)
            on_batch([row(1)])
            ready.set()
            cancel_event.wait(3)
            diagnostics.update(stop_reason="stopped")
            ended.set()
            return [row(1)], None, []
        def post(path, payload):
            req = Request(endpoint + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
            return json.load(urlopen(req, timeout=5))
        try:
            with patch.object(scraper, "run_search", side_effect=fake_search):
                for bad in [0, -1, 1.2, True, "10001"]:
                    with self.assertRaises(HTTPError) as error:
                        post("/api/search", {"keyword": "测试", "limit": bad})
                    self.assertEqual(error.exception.code, 400)
                post("/api/search", {"keyword": "测试", "limit": ""})
                self.assertTrue(ready.wait(3))
                self.assertEqual(limits, [None])
                self.assertEqual(len(json.load(urlopen(endpoint + "/api/results?keyword=%E6%B5%8B%E8%AF%95"))["goods"]), 1)
                post("/api/search/stop", {})
                self.assertTrue(ended.wait(3))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
