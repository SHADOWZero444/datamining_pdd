from support import wait_for_state
"""真实表单与本地接口集成验证；模拟商品来源，使用临时数据，不访问拼多多。"""
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdd.server import http as app
from pdd.storage import results, library
from pdd.services import auth as auth_service, jobs
from pdd.scraping import filters as filter_tools, service
from pdd.scraping import collector as scraper
from playwright.sync_api import sync_playwright, expect


def main():
    from support import artifact_dir
    output = artifact_dir()
    output.mkdir(exist_ok=True)
    received = []

    def fake_search(keyword, filters, on_batch, diagnostics, **kwargs):
        received.append(filters)
        rows = [{"goods_id": str(i), "keyword": keyword, "title": title, "price": "99"}
                for i, title in enumerate(("蓝牙 ANC 降噪耳机", "蓝牙耳机", "二手蓝牙 ANC 降噪耳机"), 1)]
        goods = filter_tools.apply_filters(rows, filters)
        on_batch(goods)
        diagnostics.update(stop_reason="end", message="已读取全部结果")
        return goods, len(rows), []

    with tempfile.TemporaryDirectory() as temp, \
         patch.object(auth_service, "ROOT", Path(temp)), \
         patch.object(results, "DATA_DIR", Path(temp) / "searches"), \
         patch.object(library, "LIBRARY_FILE", Path(temp) / "library.json"), \
         patch.object(auth_service, "auth_state", return_value={"running": False, "status": "idle"}), \
         patch.object(service, "run_search", side_effect=fake_search):
        results.result_feeds.clear()
        jobs.job.update(running=False, keyword="", status="idle")
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, channel="msedge")
                page = browser.new_page(viewport={"width": 1280, "height": 820})
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin)
                page.evaluate("FEATURE_ACTIONS.keywordSearch()")
                required = page.get_by_label("关键词", exact=True)
                expect(required).to_be_empty()
                assert required.bounding_box()["y"] < page.get_by_label("禁用词", exact=True).bounding_box()["y"]
                page.get_by_label("搜索词", exact=True).fill("耳机")
                required.fill(" 蓝牙，anc ")
                page.get_by_label("禁用词", exact=True).fill("二手")
                required.focus()
                page.keyboard.press("Tab")
                expect(page.get_by_label("禁用词", exact=True)).to_be_focused()
                page.screenshot(path=str(output / "keywords-desktop.png"))
                page.set_viewport_size({"width": 880, "height": 560})
                required.scroll_into_view_if_needed()
                page.screenshot(path=str(output / "keywords-min-window.png"))
                assert page.evaluate("document.querySelector('#stage').scrollWidth <= document.querySelector('#stage').clientWidth")
                page.get_by_role("button", name="开始", exact=True).click()
                wait_for_state(page, "state.goods['耳机']?.length === 1")
                wait_for_state(page, "!state.job.running")
                assert received[0]["required_words"] == "蓝牙，anc"
                expect(page.locator(".conditions")).to_contain_text("关键词：蓝牙，anc")
                assert [row["goods_id"] for row in results.load_goods("耳机")] == ["1"]
                assert results.search_metadata("耳机")["filters"]["required_words"] == "蓝牙，anc"
                page.locator("[data-fixed]").click()
                expect(required).to_have_value("蓝牙，anc")
                # 留空仍按原有禁用词规则收集；不同搜索词避免混入历史结果。
                page.get_by_label("搜索词", exact=True).fill("耳机不限")
                required.fill("")
                page.get_by_role("button", name="开始", exact=True).click()
                wait_for_state(page, "state.goods['耳机不限']?.length === 2")
                wait_for_state(page, "!state.job.running")
                expect(page.locator(".conditions")).to_contain_text("关键词：无")
                assert received[1]["required_words"] == ""
                assert not errors, errors
                browser.close()
            print("PASS: keyword placement, keyboard access, window sizes, submission, filtering, saved metadata, form restore and blank compatibility")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
