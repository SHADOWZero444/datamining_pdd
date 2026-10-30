import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

import pdd.browser.verification as verification
from pdd.browser.session import import_cookies


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
             patch.object(verification, "AUTO_WAIT_SECONDS", .6), \
             patch.object(verification, "MANUAL_WAIT_SECONDS", 4):
            return verification.handle_verification(**params)

    def test_page_own_check_completes_automatically(self):
        def service():
            if self.clock >= .2:
                self.state = ""
        self.assertEqual(self.run_flow(service=service), "resumed")
        self.assertEqual([x[0] for x in self.messages], ["recovering", "running"])

    def test_manual_verification_resumes_same_task(self):
        def service():
            if self.clock >= 1:
                self.state = ""
        self.assertEqual(self.run_flow(service=service), "resumed")
        self.assertEqual([x[0] for x in self.messages], ["recovering", "verification", "running"])

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
