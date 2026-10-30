"""设置页离线回归；匹配当前仅有配色、启动页的产品范围。"""
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from support import artifact_dir, wait_for_state


def main():
    root = Path(__file__).resolve().parents[1]
    output = artifact_dir()
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root / "web")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="msedge")
            page = browser.new_page(viewport={"width": 1280, "height": 820})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/api/**", lambda r: r.fulfill(json={"keywords": [], "library": {"groups": [], "entries": []}, "job": {"running": False}, "auth": {"configured": False}}))
            page.route("**/icons/*", lambda r: r.fulfill(path=str(root / "icons" / r.request.url.rsplit("/", 1)[-1])))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.locator('[data-section="settings"]').click()
            expect(page.locator(".settings-group")).to_have_count(2)
            for theme in ("day", "ink", "warm"):
                page.locator("#setting-theme").select_option(theme)
                expect(page.locator("html")).to_have_attribute("data-theme", theme)
            page.locator("#setting-theme").select_option("system")
            page.emulate_media(color_scheme="dark")
            wait_for_state(page, "document.documentElement.dataset.theme === 'ink'")
            page.locator("#setting-startup").select_option("tools")
            page.reload()
            expect(page.locator(".feature-card")).to_have_count(2)
            page.locator('[data-section="settings"]').click()
            page.evaluate("() => { window.originalSetItem = Storage.prototype.setItem; Storage.prototype.setItem = () => { throw new Error('fixture-full'); }; }")
            page.locator("#setting-theme").select_option("day")
            expect(page.locator("#setting-theme")).to_have_value("system")
            expect(page.locator("#settings-status")).to_contain_text("保存失败")
            page.evaluate("() => { Storage.prototype.setItem = window.originalSetItem; }")
            page.locator(".settings-reset summary").click()
            page.once("dialog", lambda d: d.accept())
            page.locator("#settings-reset").click()
            expect(page.locator("#setting-theme")).to_have_value("day")
            expect(page.locator("#setting-startup")).to_have_value("home")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "settings-narrow.png"))
            assert page.locator("#stage").evaluate("el => el.scrollWidth <= el.clientWidth")
            page.evaluate("localStorage.setItem(SETTINGS_KEY, 'broken')")
            page.reload()
            page.evaluate("state.section='settings'; render()")
            expect(page.locator("#settings-status")).to_contain_text("未能读取")
            assert not errors, errors
            browser.close()
            print("PASS: settings themes, system theme, startup persistence, reset, write failure, corrupt preferences and narrow layout")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
