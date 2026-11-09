from support import wait_for_state
"""用临时数据和独立端口验证实时界面；所有浏览器网络请求限定本机。"""
import json
import sys
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdd.server import http as app
from pdd.storage import results, library
from pdd.services import auth as auth_service, jobs
from pdd.scraping import filters as filter_tools, service
from pdd.scraping import collector as scraper
from playwright.sync_api import sync_playwright


def verify_collector(browser):
    """页面、滚动和响应监听用真实浏览器；目标站内容由本地固定样本提供。"""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    api_calls = []
    html = """<!doctype html><meta charset=utf-8>
    <div id=list style="height:500px;overflow-y:auto"></div>
    <script>
    const list=document.querySelector('#list'); let loading=false;
    function add(start) {for(let i=start;i<start+20;i++) {
      const card=document.createElement('div');card.style.height='150px';
      card.innerHTML='<img alt="">商品'+i;
      card.__reactFiber$fixture={memoizedProps:{data:{goodsID:i,goodsName:'商品'+i,priceInfo:'20'}},return:null};
      list.append(card);
    }}
    add(1);
    list.addEventListener('scroll',async()=>{
      if(loading||list.scrollTop+list.clientHeight<list.scrollHeight-100)return;
      loading=true; const response=await fetch('/proxy/api/search'); const data=await response.json();
      add(data.start); loading=false;
    });
    </script>"""
    def route_request(route):
        if "/proxy/api/search" in route.request.url:
            start = 21 + len(api_calls) * 20
            api_calls.append(start)
            route.fulfill(json={"start": start, "total": 20, "items": [
                {"item_data": {"goods_model": {"goods_id": i, "goods_name": f"商品{i}", "price_info": "20"}}}
                for i in range(start, start + 20)]})
        elif "search_result.html" in route.request.url:
            route.fulfill(content_type="text/html", body=html)
        else:
            route.abort()
    context.route("**/*", route_request)
    page = context.new_page()
    batches, stats = [], {}
    try:
        goods, total = scraper.collect(page, limit=225, on_batch=batches.append, diagnostics=stats)
        assert len(goods) == 225, stats
        assert len(api_calls) == 11, api_calls
        assert total == 20
        assert len(batches) > 1
        assert stats["stop_reason"] == "limit"
        print("Browser collector: 225 records, 11 paginated responses, nested scrolling, streamed batches passed")
    finally:
        context.close()


def main():
    from support import artifact_dir
    output = artifact_dir()
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, \
         patch.object(auth_service, "ROOT", Path(temp)), \
         patch.object(results, "DATA_DIR", Path(temp) / "searches"), \
         patch.object(library, "LIBRARY_FILE", Path(temp) / "library.json"):
        results.result_feeds.clear()
        keyword = "蓝牙耳机"
        rows = [{"goods_id": str(i), "seq": i, "keyword": keyword,
                 "title": f"无线蓝牙耳机 · 长续航款 {i}", "price": str(39 + i % 100),
                 "sales": "已拼1000件"} for i in range(1, 20001)]
        results.write_jsonl(results.result_path(keyword), rows)
        library.ensure_library_entry(keyword)
        jobs.job.update(running=True, keyword=keyword, status="running", id="ui-fixture", browser_available=True,
                       message="正在抓取，已读取 20000 条，符合条件 20000 条")
        jobs.job_cancel = threading.Event()
        jobs.job_browser_request = threading.Event()
        jobs.job_resume_request = threading.Event()
        jobs.job_capture_view = app.CaptureView()
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel="msedge")
                page = browser.new_page(viewport={"width": 1280, "height": 850})
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin)
                wait_for_state(page, "state.job.running")
                assert page.locator('[data-capture-browser="open"]').is_hidden()
                for status, kind in (("recovering", "verification"), ("verification", "loading")):
                    jobs.job.update(status=status, intervention_kind=kind)
                    page.evaluate("refresh()")
                    assert page.locator('[data-capture-browser="open"]').is_hidden()
                jobs.job.update(status="running", intervention_kind=None)
                page.get_by_role("button", name="查看结果", exact=True).click()
                page.wait_for_selector(".catalog tbody tr")
                assert page.locator(".catalog tbody tr").count() == 100
                page.get_by_role("button", name="下一页", exact=True).click()
                assert page.locator(".seq").first.inner_text() == "101"
                page.locator('input[name="startId"]').fill("37")
                page.evaluate("document.querySelector('#stage').scrollTop = 120")
                old_scroll = page.evaluate("document.querySelector('#stage').scrollTop")
                page.evaluate("window.savedRow = document.querySelector('.catalog tbody tr')")
                revision = page.evaluate("state.revisions['蓝牙耳机']")
                results.merge_goods(keyword, [{"goods_id": "20001", "keyword": keyword, "title": "新增商品", "price": "55"}])
                delta = results.result_feed(keyword, revision)
                full = results.result_feed(keyword)
                wait_for_state(page, "state.goods['蓝牙耳机'].length === 20001")
                assert page.locator('input[name="startId"]').input_value() == "37"
                assert page.locator('input[name="startId"]').evaluate("el => el === document.activeElement")
                assert page.evaluate("window.savedRow === document.querySelector('.catalog tbody tr')")
                assert page.evaluate("document.querySelector('#stage').scrollTop") == old_scroll
                assert page.locator('input[name="page"]').input_value() == "2"
                # 测量一轮没有全量传输的刷新，包括本机 HTTP 往返。
                elapsed = page.evaluate("async () => {const t=performance.now();await refresh();return performance.now()-t}")
                page.locator('input[name="startId"]').blur()
                # 打开详情、后台增量更新、关闭详情，仍回到第二页的同一行。
                page.locator('.catalog tbody tr').nth(45).scroll_into_view_if_needed()
                page.locator('.catalog tbody tr').nth(45).click()
                saved = page.evaluate("[...stagePositions.values()].at(-1).top")
                assert saved > 500, saved
                assert page.evaluate("state.tabs.find(t => t.id === state.active).kind") == "goods"
                results.merge_goods(keyword, [{"goods_id": "20002", "keyword": keyword, "title": "详情期间新增", "price": "56"}])
                wait_for_state(page, "state.goods['蓝牙耳机'].length === 20002")
                page.locator('#tabs-detail [data-close]').last.click()
                assert page.locator('input[name="page"]').input_value() == "2"
                assert page.evaluate("document.querySelector('#stage').scrollTop") == saved
                # 窄窗口的横向表格位置也随词条保留。
                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate("document.querySelector('.catalog-scroll').scrollLeft=180")
                position = page.evaluate("({top:document.querySelector('#stage').scrollTop,left:document.querySelector('.catalog-scroll').scrollLeft})")
                assert position['left'] > 0
                page.locator('.catalog tbody tr').nth(20).dispatch_event('click')
                page.locator('#tabs-detail [data-close]').last.click()
                assert page.evaluate("document.querySelector('#stage').scrollTop") == position['top']
                assert page.evaluate("document.querySelector('.catalog-scroll').scrollLeft") == position['left']
                page.set_viewport_size({"width": 1280, "height": 850})
                page.evaluate("document.querySelector('#stage').scrollTop=0")
                jobs.job.update(status="verification", intervention_kind="verification",
                               message="请点击“人机验证”，在抓取窗口完成验证；完成后会自动继续")
                page.get_by_role("button", name="人机验证", exact=True).wait_for()
                with page.expect_response(lambda response: response.url.endswith("/api/search/browser")) as opened:
                    page.get_by_role("button", name="人机验证", exact=True).click()
                assert opened.value.status == 200
                assert jobs.job_capture_view.opened.is_set()
                page.locator('#capture-dialog').wait_for(state='visible')
                remote = browser.new_page(viewport={"width": 1000, "height": 760})
                remote.route("**/*", lambda route: route.abort())
                remote.set_content('''<meta charset=utf-8><body style="font:24px sans-serif;padding:40px">
                    <h2>本地验证交互样本</h2><p>点击按钮后返回原抓取任务。</p>
                    <button style="font:24px sans-serif;padding:20px" onclick="this.textContent='已完成'">完成样本操作</button></body>''')
                jobs.job_capture_view.service(remote)
                page.locator('#capture-image').wait_for(state='visible')
                remote_box = remote.locator('button').bounding_box()
                image_box = page.locator('#capture-image').bounding_box()
                with page.expect_response(lambda response: response.url.endswith('/api/search/browser')):
                    page.mouse.click(image_box['x'] + (remote_box['x'] + 30) / 1000 * image_box['width'],
                                     image_box['y'] + (remote_box['y'] + 20) / 760 * image_box['height'])
                jobs.job_capture_view.service(remote)
                assert remote.locator('button').inner_text() == '已完成'
                page.screenshot(path=str(output / "capture-panel-desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "capture-panel-narrow.png"))
                assert page.locator('#capture-dialog').evaluate('el => el.scrollWidth <= el.clientWidth')
                page.set_viewport_size({"width": 1280, "height": 850})
                with page.expect_response(lambda response: response.url.endswith("/api/search/browser")) as resumed:
                    page.locator('#capture-continue').click()
                assert resumed.value.status == 200
                assert jobs.job_resume_request.is_set()
                assert jobs.job_capture_view.opened.is_set()
                jobs.job_capture_view.close()  # 后台确认后才关闭。
                remote.close()
                page.locator('#capture-dialog').wait_for(state='hidden')
                jobs.job.update(status='running', intervention_kind=None)
                page.evaluate('refresh()')
                assert page.locator('[data-capture-browser="open"]').is_hidden()
                page.screenshot(path=str(output / "streaming-desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "streaming-narrow.png"))
                page.get_by_role("button", name="停止抓取", exact=True).click()
                wait_for_state(page, "state.job.status === 'stopping'")
                assert jobs.job_cancel.is_set()
                jobs.job.update(running=False, status="stopped", message="已停止抓取，已保留 20001 条商品")
                wait_for_state(page, "!state.job.running")
                page.locator("#capture-bar").wait_for(state="hidden")
                assert page.locator(".catalog tbody tr").count() == 100
                assert not errors, errors
                print(json.dumps({"rows": 20002, "rendered_rows": 100, "detail_return_scroll": "vertical and horizontal passed",
                                  "full_bytes": len(json.dumps(full, ensure_ascii=False).encode()),
                                  "delta_bytes": len(json.dumps(delta, ensure_ascii=False).encode()),
                                  "refresh_ms": round(elapsed, 1), "browser_errors": errors}, ensure_ascii=False))
                verify_collector(browser)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
