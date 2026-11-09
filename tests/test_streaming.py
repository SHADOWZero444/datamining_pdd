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

from pdd.server import http as app
from pdd.storage import results, library
from pdd.services import auth as auth_service, jobs
from pdd.scraping import filters as filter_tools, service
from pdd.scraping import collector as scraper
from pdd.scraping import page as page_tools
from pdd.browser.view import CaptureView
from pdd.scraping.pacing import PagePacer


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

    def is_closed(self):
        return False

    def remove_listener(self, event, callback):
        self.callbacks.pop(event, None)

    def goto(self, *args, **kwargs):
        for response in self.responses.get(0, []):
            self.emit_response(response)

    def emit_response(self, response):
        self.callbacks["request"](response)
        self.callbacks["response"](response)
        self.callbacks["requestfinished"](response)

    def wait_for_timeout(self, ms):
        self.clock += ms / 1000
        if self.wait_hook:
            self.wait_hook()

    def scroll(self):
        self.index += 1
        if self.index == self.fail_at:
            raise RuntimeError("simulated page crash")
        for response in self.responses.get(self.index, []):
            self.emit_response(response)


class CollectorTests(unittest.TestCase):
    def collect(self, page, **kwargs):
        current_state = kwargs.pop("page_state", "")
        with patch.object(scraper, "read_live_goods", side_effect=lambda _, **kwargs: page.batches[min(page.index, len(page.batches)-1)]), \
             patch.object(scraper, "read_dom_goods", return_value=[]), \
             patch.object(scraper, "scroll_results", side_effect=lambda _, **options: page.scroll()), \
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

    def test_keywords_filter_before_streaming_and_counting_limit(self):
        page = FakePage([
            [{**row(1), "title": "普通耳机"}, {**row(2), "title": "蓝牙耳机"}],
            [{**row(3), "title": "蓝牙降噪耳机"}, {**row(4), "title": "二手蓝牙降噪耳机"}],
            [{**row(5), "title": "新款蓝牙降噪耳机"}],
        ])
        batches, stats = [], {}
        goods, _ = self.collect(page, limit=2,
                                filters={"required_words": "蓝牙,降噪", "banned": "二手"},
                                on_batch=batches.append, diagnostics=stats)
        self.assertEqual([item["goods_id"] for item in goods], ["3", "5"])
        self.assertEqual([item["goods_id"] for batch in batches for item in batch], ["3", "5"])
        self.assertEqual(stats["scanned"], 5)
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["stop_reason"], "limit")

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
        self.assertEqual(stats["stop_reason"], "rate_limited")
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

    def test_desktop_repeated_rejections_require_manual_confirmation(self):
        resume, stats, ready_at = threading.Event(), {}, []
        page = FakePage([[row(1)]] * 4 + [[row(1), row(2)]],
                        {i: [Response(status=429)] for i in range(4)})
        def notify(status, message, kind):
            if kind == "rate_limit_ready":
                ready_at.append(page.clock)
                resume.set()
        goods, _ = self.collect(page, limit=2, diagnostics=stats,
                                resume_request=resume, on_intervention=notify)
        self.assertEqual(len(goods), 2)
        self.assertEqual(stats["auto_recovery_attempts"], 3)
        self.assertEqual(stats["manual_resumes"], 1)
        self.assertGreaterEqual(ready_at[0] + .001, 60 + 120 + 240 + 480)

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
        self.assertGreaterEqual(page.first_scroll + .001, 3 + PagePacer.RENDER_SETTLE)
        self.assertFalse(page.callbacks)

    def test_user_takeover_pauses_scroll_and_remains_cancellable(self):
        view = CaptureView()
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

    def test_recovers_after_180_and_four_separate_limits_to_1200(self):
        class PagedApi(FakePage):
            """每次真实调用生产解析器，模拟 20 条一页；429 不推进页码。"""
            def __init__(self):
                super().__init__([[]])
                self.served = 0
                self.limited_at = set()
                self.requests = []

            def goto(self, *args, **kwargs):
                self.scroll()

            def scroll(self):
                limited = self.served in (180, 400, 600, 800) and self.served not in self.limited_at
                self.requests.append((self.clock, 429 if limited else 200))
                if limited:
                    self.limited_at.add(self.served)
                    self.emit_response(Response(status=429, retry="45"))
                    return
                end = min(1200, self.served + 20)
                items = [{"item_data": {"goods_model": {"goods_id": str(n), "goods_name": f"商品{n}"}}}
                         for n in range(self.served + 1, end + 1)]
                self.served = end
                self.emit_response(Response({"items": items, "has_more": end < 1200, "total": 300}))

        page, stats, batches = PagedApi(), {}, []
        button = Mock()
        button.click.side_effect = lambda **kwargs: page.scroll()
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            goods, total = self.collect(page, diagnostics=stats, on_batch=batches.append)
        self.assertEqual(len({item["goods_id"] for item in goods}), 1200)
        self.assertEqual(len({item["goods_id"] for batch in batches for item in batch}), 1200)
        self.assertEqual(total, 300)
        self.assertEqual(stats["stop_reason"], "end")
        self.assertEqual(stats["rate_limits"], 4)
        self.assertEqual(stats["rate_limit_recoveries"], 4)
        self.assertEqual(stats["consecutive_rate_limits"], 0)
        self.assertEqual(stats["rate_limit_retries"], 4)
        self.assertEqual(stats["page_retries"], 0)
        self.assertEqual(stats["auto_recovery_attempts"], 4)
        self.assertGreater(stats["page_interval_seconds"], PagePacer.MIN_INTERVAL)
        for (before, status), (after, _) in zip(page.requests, page.requests[1:]):
            self.assertGreaterEqual(after - before + .001, 60 if status == 429 else PagePacer.MIN_INTERVAL)
        self.assertEqual(len(stats["response_timeline"]), 60)
        self.assertFalse(page.callbacks)

    def test_duplicate_success_pages_do_not_reset_rate_limit_budget(self):
        item = {"item_data": {"goods_model": {"goods_id": "1", "goods_name": "商品1"}}}
        responses = {0: [Response({"items": [item]})]}
        for index in range(1, 8):
            responses[index] = [Response(status=429) if index % 2 else Response({"items": [item]})]
        stats = {}
        goods, _ = self.collect(FakePage([[row(1)]], responses), diagnostics=stats)
        self.assertEqual(len(goods), 1)
        self.assertEqual(stats["stop_reason"], "rate_limited")
        # 首次恢复返回重复页，不能再自动滚动触发后续请求。
        self.assertEqual(stats["consecutive_rate_limits"], 1)
        self.assertEqual(stats["api_responses"], 3)
        self.assertEqual(stats["rate_limit_recoveries"], 0)

    def test_successful_encrypted_page_and_new_dom_rows_reset_budget(self):
        page = FakePage([[row(1)], [row(1), row(2)]], {
            0: [Response(status=429)],
            1: [Response({"rc-res-data": "test-only-wrapped-response", "content-type": "application/json"})],
            2: [Response({"items": [], "has_more": False})],
        })
        stats = {}
        goods, _ = self.collect(page, diagnostics=stats, limit=2)
        self.assertEqual(len(goods), 2)
        self.assertEqual(stats["rate_limit_recoveries"], 1)
        self.assertEqual(stats["stop_reason"], "limit")

    def test_new_dom_rows_without_success_do_not_reset_budget(self):
        page = FakePage([[row(n) for n in range(i + 1)] for i in range(4)],
                        {i: [Response(status=429)] for i in range(4)})
        stats = {}
        self.collect(page, diagnostics=stats)
        self.assertEqual(stats["stop_reason"], "rate_limited")
        self.assertEqual(stats["rate_limit_recoveries"], 0)

    def test_business_failure_does_not_count_as_recovery(self):
        page = FakePage([[row(1)], [row(1), row(2)]], {
            0: [Response(status=429)],
            1: [Response({"success": False, "data": {"items": []}})],
            2: [Response(status=429)], 3: [Response(status=429)], 4: [Response(status=429)],
        })
        stats = {}
        self.collect(page, diagnostics=stats)
        self.assertEqual(stats["stop_reason"], "rate_limited")
        self.assertEqual(stats["rate_limit_recoveries"], 0)

    def test_429_during_cooldown_extends_deadline(self):
        page = FakePage([[row(1)]], {0: [Response(status=429, retry="30")]})
        button, stats, clicked_at = Mock(), {}, []
        cancelled = threading.Event()
        delayed_at = []
        def arrive():
            if page.clock >= 10 and not delayed_at:
                delayed_at.append(page.clock)
                page.emit_response(Response(status=429, retry="90"))
        def clicked(**kwargs):
            clicked_at.append(page.clock)
            cancelled.set()
        page.wait_hook = arrive
        button.click.side_effect = clicked
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, should_stop=cancelled.is_set, diagnostics=stats)
        self.assertEqual(len(clicked_at), 1)
        self.assertGreaterEqual(clicked_at[0] + .001, delayed_at[0] + 90)
        self.assertEqual(stats["rate_limits"], 2)
        self.assertEqual(stats["stop_reason"], "stopped")

    def test_multiple_retry_headers_use_longest_wait(self):
        page = FakePage([[row(1)]], {0: [Response(status=429, retry="30"), Response(status=429, retry="180")]})
        button, clicked_at = Mock(), []
        cancelled = threading.Event()
        def clicked(**kwargs):
            clicked_at.append(page.clock)
            cancelled.set()
        button.click.side_effect = clicked
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, should_stop=cancelled.is_set)
        self.assertGreaterEqual(clicked_at[0], 179.999)

    def test_no_retry_click_while_request_is_pending(self):
        class PendingPage(FakePage):
            def goto(self, *args, **kwargs):
                self.request = Response(status=429)
                self.callbacks["request"](self.request)
                self.callbacks["response"](self.request)
        page = PendingPage([[row(1)]])
        button, clicked_at, finished_at = Mock(), [], []
        cancelled = threading.Event()
        def complete():
            if page.clock >= 40 and not finished_at:
                finished_at.append(page.clock)
                page.callbacks["requestfinished"](page.request)
        def clicked(**kwargs):
            clicked_at.append(page.clock)
            cancelled.set()
        page.wait_hook = complete
        button.click.side_effect = clicked
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, should_stop=cancelled.is_set)
        self.assertGreaterEqual(clicked_at[0], max(60, finished_at[0] + PagePacer.RENDER_SETTLE) - .001)

    def test_verification_during_cooldown_does_not_click_retry(self):
        page = FakePage([[row(1)]], {0: [Response(status=429)]})
        button, stats = Mock(), {}
        page.wait_hook = lambda: page.callbacks["response"](Response({"message": "请完成验证", "data": {"items": []}}))
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, diagnostics=stats)
        button.click.assert_not_called()
        self.assertEqual(stats["stop_reason"], "verification")

    def test_manual_verification_does_not_discard_retry_after(self):
        page = FakePage([[row(1)]], {0: [Response(status=429, retry="90"),
                                      Response({"message": "请完成验证"})]})
        page.mouse = Mock()
        button, stats, clicked_at = Mock(), {}, []
        cancelled = threading.Event()
        def clicked(**kwargs):
            clicked_at.append(page.clock)
            cancelled.set()
        button.click.side_effect = clicked
        with patch.object(scraper, "loading_error_retry_button", return_value=button), \
             patch.object(scraper, "handle_verification", return_value="resumed"):
            self.collect(page, on_intervention=Mock(), should_stop=cancelled.is_set, diagnostics=stats)
        page.mouse.wheel.assert_not_called()
        self.assertGreaterEqual(clicked_at[0], 89.999)
        self.assertEqual(stats["stop_reason"], "stopped")

    def test_429_during_network_retry_wait_prevents_early_click(self):
        page, button, stats = FakePage([[row(1)]]), Mock(), {}
        limited_at, clicked_at = [], []
        cancelled = threading.Event()
        def arrive():
            if page.clock >= scraper.STALL_SECONDS + 1 and not limited_at:
                limited_at.append(page.clock)
                page.emit_response(Response(status=429, retry="90"))
        def clicked(**kwargs):
            clicked_at.append(page.clock)
            cancelled.set()
        page.wait_hook = arrive
        button.click.side_effect = clicked
        with patch.object(scraper, "loading_error_retry_button", return_value=button):
            self.collect(page, should_stop=cancelled.is_set, diagnostics=stats)
        self.assertGreaterEqual(clicked_at[0] + .001, limited_at[0] + 90)
        self.assertEqual(stats["rate_limit_retries"], 1)
        self.assertEqual(stats["page_retries"], 0)

    def test_user_takeover_during_cooldown_never_clicks_retry(self):
        page = FakePage([[row(1)]], {0: [Response(status=429)]})
        view, button, stats = CaptureView(), Mock(), {}
        cancelled = threading.Event()
        def take_over():
            view.open()
            if page.clock >= 35:
                cancelled.set()
        page.wait_hook = take_over
        with patch.object(scraper, "loading_error_retry_button", return_value=button), patch.object(view, "service"):
            self.collect(page, capture_view=view, should_stop=cancelled.is_set, diagnostics=stats)
        button.click.assert_not_called()
        self.assertEqual(page.index, 0)
        self.assertEqual(stats["stop_reason"], "stopped")


class PageRecoveryTests(unittest.TestCase):
    def test_window_blocks_seventh_request_and_drains_after_idle(self):
        with patch("pdd.scraping.pacing.time.monotonic", return_value=0) as clock:
            pacer = PagePacer()
            for start in (0, 3, 6, 9, 12, 15):
                pacer.started(start)
                pacer.finished(start, start + 0.1)
            clock.return_value = 59.9
            self.assertFalse(pacer.ready())
            clock.return_value = 60
            self.assertTrue(pacer.ready())
            pacer.started(60)
            clock.return_value = 121
            self.assertTrue(pacer.ready())

    def test_rate_limit_spacing_does_not_reset_after_one_success(self):
        pacer = PagePacer()
        pacer.slow_down()
        for _ in range(100):
            pacer.new_page()
        self.assertGreaterEqual(pacer.interval, pacer.WINDOW_SECONDS / pacer.WINDOW_REQUESTS)

    def test_retry_after_formats(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 31, tzinfo=timezone.utc)
        with patch.object(page_tools, "datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(page_tools.retry_delay("Sat, 31 Oct 2026 00:02:00 GMT", 30), 120)
            self.assertEqual(page_tools.retry_delay("Sat, 31 Oct 2026 00:00:10 GMT", 30), 30)
        self.assertEqual(page_tools.retry_delay(" 180 ", 30), 180)
        for value in (None, "", "invalid", "-1", "0"):
            self.assertEqual(page_tools.retry_delay(value, 30), 30)

    def test_only_explicit_recovery_leaves_bottom(self):
        page = Mock()
        page._pdd_touch_session = None
        page.evaluate_handle.return_value.evaluate.return_value = {"x": 100, "y": 200, "at_bottom": True, "step": 0, "touch_height": 800}
        page_tools.scroll_results(page)
        page.mouse.wheel.assert_not_called()
        page_tools.scroll_results(page, retry_bottom=True)
        page.mouse.move.assert_called_once_with(100, 200)
        page.mouse.wheel.assert_called_once_with(0, -120)

    def test_no_motion_uses_container_only_when_pacing_allows(self):
        page = Mock()
        handle = page.evaluate_handle.return_value
        metrics = dict(step=250, top=0, viewport=430, at_bottom=False)
        previous = dict(metrics, strategy="gesture", unmoved=1)
        handle.evaluate.side_effect = [dict(metrics), dict(metrics), 250]
        self.assertEqual(page_tools.scroll_results(page, previous=previous, can_scroll=lambda: False)["strategy"], "idle")
        result = page_tools.scroll_results(page, previous=previous, can_scroll=lambda: True)
        self.assertEqual((result["strategy"], result["after_top"]), ("container", 250))
        self.assertEqual(handle.dispose.call_count, 2)

    def test_response_summary_has_counts_without_raw_payload(self):
        data = {"data": {"items": [{"secret": "do-not-store"}], "has_more": True, "is_repeated": False},
                "token": "do-not-store"}
        summary = page_tools.response_summary(data, Response(data))
        self.assertEqual(summary["item_count"], 1)
        self.assertTrue(summary["has_more"])
        self.assertFalse(summary["is_repeated"])
        self.assertNotIn("do-not-store", json.dumps(summary))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(auth_service, "ROOT", self.root),
                        patch.object(results, "DATA_DIR", self.root / "searches"),
                        patch.object(library, "LIBRARY_FILE", self.root / "library.json")]
        for item in self.patches:
            item.start()
        jobs.job.update(running=False, keyword="", status="idle")
        results.result_feeds.clear()
        results._read_jsonl_cached.cache_clear()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_delta_updates_deletes_and_unknown_revision(self):
        results.merge_goods("测试", [row(1), row(2)])
        first = results.result_feed("测试")
        self.assertTrue(results.result_feed("测试", first["revision"])["unchanged"])
        results.merge_goods("测试", [row(1, "12"), row(3)])
        second = results.result_feed("测试", first["revision"])
        self.assertTrue(second["delta"])
        self.assertEqual({r["goods_id"] for r in second["goods"]}, {"1", "3"})
        results.write_jsonl(results.result_path("测试"), [r for r in results.load_goods("测试") if r["goods_id"] != "2"])
        third = results.result_feed("测试", first["revision"])
        self.assertEqual(third["removed"], ["2"])
        self.assertEqual(len(results.result_feed("测试", "expired")["goods"]), 2)

    def test_refresh_compares_each_batch_with_start_and_resets_previous_flags(self):
        term = "测试"
        results.merge_goods(term, [
            {**row(1), "sales": "已拼10件", "badges": ["black", "bybt"]},
            {**row(2), "updated": True}, {**row(3), "updated": True},
        ])
        first = results.result_feed(term)
        def fake_search(keyword, on_batch, **options):
            self.assertTrue(options["refresh"])
            batch = [row(1, "12"), {**row(2), "url": "https://example.org/new"}, row(4)]
            on_batch(batch)
            delta = results.result_feed(term, first["revision"])
            self.assertTrue(delta["delta"])
            self.assertEqual([r["updated"] for r in delta["goods"]], [True, False, False, False])
            on_batch([{**row(1, "12"), "sales": "", "badges": ["bybt", "black"]}])
            return batch, None, []
        with patch.object(service, "run_search", side_effect=fake_search):
            jobs.run_job(term, None, {}, threading.Event(), refresh=True)
        saved = results.load_goods(term)
        self.assertEqual([r["seq"] for r in saved], [1, 2, 3, 4])
        self.assertEqual([r["updated"] for r in saved], [True, False, False, False])
        self.assertEqual(saved[0]["sales"], "已拼10件")
        self.assertIn("本轮更新 1 条，新增 1 条", jobs.job["message"])
        with patch.object(service, "run_search", return_value=([row(1, "12")], None, [])):
            jobs.run_job(term, None, {}, threading.Event(), refresh=True)
        self.assertFalse(any(r["updated"] for r in results.load_goods(term)))

    def test_refresh_failure_preserves_last_result_until_first_batch_commits(self):
        term = "测试"
        results.merge_goods(term, [{**row(1), "updated": True}, row(2)])
        before = results.result_path(term).read_bytes()
        with patch.object(service, "run_search", return_value=([], None, ["登录已失效"])):
            jobs.run_job(term, None, {}, threading.Event(), refresh=True)
        self.assertEqual(results.result_path(term).read_bytes(), before)
        self.assertIn("保留原有数据和更新标记", jobs.job["message"])
        def interrupt(keyword, on_batch, **kwargs):
            on_batch([row(2, "13")])
            raise RuntimeError("模拟更新中断")
        with patch.object(service, "run_search", side_effect=interrupt):
            jobs.run_job(term, None, {}, threading.Event(), refresh=True)
        self.assertEqual([r["updated"] for r in results.load_goods(term)], [False, True])
        self.assertEqual(jobs.job["status"], "partial")

    def test_refresh_restores_legacy_settings_and_counts_this_run_only(self):
        term = "测试"
        old = [row(1), row(2)]
        results.write_jsonl(results.result_path(term), old)
        results.atomic_write(results.metadata_path(term), json.dumps({"keyword": term, "filters": {"price_max": 15}}))
        results.atomic_write(results.result_path(term).with_suffix(".run.json"), json.dumps({"keyword": term, "limit": 2}))
        with patch.object(jobs.threading, "Thread") as thread:
            jobs.start_job(term, None, {}, refresh=True)
            keyword, limit, filters, cancel, refresh = thread.call_args.kwargs["args"]
            self.assertEqual((keyword, limit, filters["price_max"], refresh), (term, 2, 15, True))
            with self.assertRaises(RuntimeError):
                jobs.start_job(term, None, {}, refresh=True)
        target = service.CollectionTarget(limit, old, filters, refresh=True)
        self.assertFalse(target.reached)
        self.assertTrue(target.accept(row(1)))
        self.assertTrue(target.accept(row(1)))
        self.assertFalse(target.accept(row(3, "20")))
        self.assertTrue(target.accept(row(4)))
        self.assertTrue(target.reached)
        self.assertEqual(target.new_ids, {"4"})

    def test_atomic_write_failure_preserves_old_file(self):
        path = results.result_path("测试")
        results.write_jsonl(path, [row(1)])
        with patch.object(results.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                results.write_jsonl(path, [row(2)])
        self.assertEqual(results.read_jsonl(path), [row(1)])
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_run_job_publishes_before_return_and_preserves_on_error(self):
        event = threading.Event()
        def fake_search(keyword, on_batch, **kwargs):
            on_batch([row(1)])
            self.assertEqual(len(results.result_feed(keyword)["goods"]), 1)
            self.assertTrue(jobs.job["running"])
            raise RuntimeError("simulated browser crash")
        jobs.job.update(running=True, keyword="测试")
        with patch.object(service, "run_search", side_effect=fake_search):
            jobs.run_job("测试", None, {}, event)
        self.assertFalse(jobs.job["running"])
        self.assertEqual(jobs.job["status"], "partial")
        self.assertEqual(len(results.load_goods("测试")), 1)
        self.assertEqual(json.loads((results.DATA_DIR / "测试.run.json").read_text(encoding="utf-8"))["stop_reason"], "error")

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
            req = Request(endpoint + path, json.dumps(payload).encode(), {"Content-Type": "application/json", "X-PDD-App": "1"})
            return json.load(urlopen(req, timeout=5))
        try:
            with patch.object(service, "run_search", side_effect=fake_search):
                for bad in [0, -1, 1.2, True, "10001"]:
                    with self.assertRaises(HTTPError) as error:
                        post("/api/search", {"keyword": "测试", "limit": bad})
                    self.assertEqual(error.exception.code, 400)
                post("/api/search", {"keyword": "测试", "limit": ""})
                self.assertTrue(ready.wait(3))
                self.assertEqual(limits, [None])
                self.assertEqual(len(json.load(urlopen(Request(endpoint + "/api/results?keyword=%E6%B5%8B%E8%AF%95", headers={"X-PDD-App": "1"})))["goods"]), 1)
                post("/api/search/stop", {})
                self.assertTrue(ended.wait(3))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
