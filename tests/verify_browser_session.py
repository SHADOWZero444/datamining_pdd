"""独立临时配置目录测试：不读取用户会话，所有页面请求由本地样本拦截。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_browser import capture_session
from capture_view import CaptureView


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        root = Path(temp)
        cookie = root / "cookie.txt"
        for attempt in range(2):
            with capture_session(root, cookie) as page:
                page.context.route("**/*", lambda route: route.fulfill(content_type="text/html", body="""
                    <meta charset=utf-8><p>本地会话测试</p>
                    <button style="position:fixed;left:100px;top:100px;width:200px;height:100px"
                      onclick="document.body.dataset.clicked='yes'">验证操作样本</button>
                    <input id=input style="position:fixed;left:100px;top:300px;width:200px;height:60px">
                """))
                page.goto("https://mobile.pinduoduo.com/search_result.html")
                if attempt == 0:
                    page.evaluate("localStorage.setItem('capture-fixture', 'retained')")
                    page.context.add_cookies([{"name": "capture_fixture", "value": "retained", "domain": ".pinduoduo.com", "path": "/"}])
                    # 默认会话必须是后台的完整 Chromium，引擎运行 JS 并保留站点存储。
                    assert "HeadlessChrome" in page.evaluate("navigator.userAgent")
                    view = CaptureView()
                    view.open()
                    view.service(page)
                    assert view.snapshot()["image"]
                    view.submit({"action": "pointer", "points": [[.2, .2, 0]]})
                    view.service(page)
                    assert page.locator("body").get_attribute("data-clicked") == "yes"
                    view.submit({"action": "pointer", "points": [[.2, .43, 0]]})
                    view.service(page)
                    view.submit({"action": "text", "text": "中文输入测试"})
                    view.service(page)
                    assert page.locator("#input").input_value() == "中文输入测试"
                    view.close()
                    assert view.snapshot() == {"image": None, "error": "", "opened": False}
                else:
                    assert page.evaluate("localStorage.getItem('capture-fixture')") == "retained"
                    assert any(c['name'] == 'capture_fixture' for c in page.context.cookies())
        assert (root / "data/browser-cookie.sha256").exists()
        print("Native headless browser: persistent cookies/storage; in-app screenshot, pointer and Chinese input passed")


if __name__ == "__main__":
    main()
