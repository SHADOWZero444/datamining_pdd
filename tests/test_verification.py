import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

import pdd.browser.verification as verification
from pdd.browser.session import import_cookies
from pdd.browser.mobile import manual_pointer
from pdd.scraping.responses import verification_hint


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0
        self.state = "verification"
        self.ready = True
        self.messages = []
        self.page = SimpleNamespace(frames=[])
        self.event = threading.Event()

    def wait(self, ms):
        self.clock += ms / 1000
        return not self.event.is_set()

    def run_flow(self, **options):
        params = dict(page=self.page, kind="verification", check=lambda: self.state,
                      wait=self.wait, notify=lambda *args: self.messages.append(args),
                      should_stop=self.event.is_set, ready=lambda: self.ready)
        params.update(options)
        with patch.object(verification.time, "monotonic", side_effect=lambda: self.clock), \
             patch.object(verification, "MANUAL_WAIT_SECONDS", 4):
            return verification.handle_verification(**params)

    def test_page_own_check_completes_automatically(self):
        def service():
            if self.clock >= .2:
                self.state = ""
        self.assertEqual(self.run_flow(service=service), "resumed")
        self.assertEqual([x[0] for x in self.messages], ["verification", "running"])

    def test_manual_verification_resumes_same_task(self):
        def service():
            if self.clock >= 1:
                self.state = ""
        self.assertEqual(self.run_flow(service=service), "resumed")
        self.assertEqual([x[0] for x in self.messages], ["verification", "running"])

    def test_continue_does_not_override_visible_challenge(self):
        self.assertEqual(self.run_flow(resume_requested=lambda: True), "timeout")
        self.assertNotIn("running", [x[0] for x in self.messages])

    def test_blank_page_is_not_success(self):
        self.state, self.ready = "", False
        self.assertEqual(self.run_flow(resume_requested=lambda: True), "timeout")

    def test_cancel_while_waiting(self):
        def service():
            if self.clock >= 1:
                self.event.set()
        self.assertEqual(self.run_flow(service=service), "stopped")
        self.assertLess(self.clock, 2)

    def test_explicit_challenge_link_is_same_origin_only(self):
        self.assertEqual(verification_hint({"data": {"verify_url": "/captcha.html"}}),
                         (True, "https://mobile.pinduoduo.com/captcha.html"))
        for url in ("https://mobile.pinduoduo.com.evil.invalid/", "//elsewhere.invalid/",
                    "javascript:alert(1)", "https://mobile.pinduoduo.com@elsewhere.invalid/"):
            self.assertEqual(verification_hint({"captchaUrl": url}), (True, None))
        self.assertEqual(verification_hint({"url": "/goods.html", "items": []}), (False, None))

    def test_link_only_opens_once_on_user_input_and_keeps_original_page(self):
        original = Mock()
        tab = original.context.new_page.return_value
        url = "https://mobile.pinduoduo.com/captcha.html"
        tab.url, tab.is_closed.return_value, tab.goto.return_value.status = url, False, 200
        target = verification.VerificationTarget(original, url)
        self.assertEqual(target.state(lambda _: ""), "verification")
        original.context.new_page.assert_not_called()
        with patch.object(verification, "configure_mobile_page"):
            for _ in range(2):
                self.assertIs(target.page_for_input(lambda _: ""), tab)
        tab.goto.assert_called_once()
        self.assertEqual(target.state(lambda p: "verification" if p is tab else ""), "verification")
        self.assertEqual(target.state(lambda _: ""), "")
        target.close()
        tab.close.assert_called_once()
        original.close.assert_not_called()

    def test_user_gesture_uses_touch_and_cancels_on_interruption(self):
        page = Mock(viewport_size={"width": 101, "height": 101})
        points = [(0.1, 0.2, 0), (0.5, 0.3, 77)]
        for active, events in ((lambda: True, ["touchStart", "touchMove", "touchEnd"]),
                               (iter([True, False]).__next__, ["touchStart", "touchCancel"])):
            touch = page._pdd_touch_session = Mock()
            manual_pointer(page, points, active)
            self.assertEqual([call.args[1]["type"] for call in touch.send.call_args_list], events)
            self.assertEqual(touch.send.call_args_list[0].args[1]["touchPoints"], [{"x": 10, "y": 20}])
            self.assertEqual(touch.send.call_args_list[-1].args[1]["touchPoints"], [])
        page.mouse.down.assert_not_called()

    def test_unclassified_loading_requires_confirmation(self):
        self.state = ""
        self.assertEqual(self.run_flow(kind="loading", require_confirmation=True), "timeout")
        self.assertEqual(self.run_flow(kind="loading", require_confirmation=True,
                                      resume_requested=lambda: True), "resumed")

    def test_explicit_import_does_not_overwrite_fresh_browser_cookies(self):
        import tempfile
        from pathlib import Path
        from pdd.browser.session import fingerprint
        with tempfile.TemporaryDirectory() as temp:
            cookie_file, marker = Path(temp) / "cookie.txt", Path(temp) / "hash"
            cookie_file.write_text("demo=old", encoding="utf-8")
            marker.write_text(fingerprint("demo=old"), encoding="ascii")
            context = Mock()
            context.cookies.return_value = [{"name": "demo", "value": "fresh"}]
            import_cookies(context, cookie_file, marker)
            context.add_cookies.assert_not_called()
            context.clear_cookies.assert_not_called()
            context.cookies.return_value = []
            import_cookies(context, cookie_file, marker)
            self.assertEqual(context.add_cookies.call_args.args[0][0]["value"], "old")


if __name__ == "__main__":
    unittest.main()
