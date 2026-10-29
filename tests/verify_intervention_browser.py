"""真实浏览器离线验证：20 条后 iframe 验证、同页续抓、停止和隐形 iframe。"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdd_search as scraper
import capture_verification
from capture_view import CaptureView
from playwright.sync_api import sync_playwright


HTML = """<!doctype html><meta charset=utf-8>
<div id=list style="height:400px;overflow-y:auto"></div>
<script>
window.rawData={stores:{}}; window.loaded=20; window.verified=false; window.loading=false;
const list=document.querySelector('#list');
function add(start){for(let i=start;i<start+20;i++){
 const card=document.createElement('div'); card.style.height='60px';card.innerHTML='<img alt="">商品'+i;
 card.__reactFiber$test={memoizedProps:{data:{goodsID:i,goodsName:'商品'+i,priceInfo:'10'}}};list.append(card);
}}
add(1);
addEventListener('message',event=>{if(event.data==='verified'){window.verified=true;document.querySelector('iframe')?.remove();}});
list.addEventListener('scroll',async()=>{
 if(window.loading||window.loaded>20||list.scrollTop+list.clientHeight<list.scrollHeight-80)return;
 window.loading=true;
 if(!window.verified){
   const frame=document.createElement('iframe');frame.src='/captcha.html';frame.style='position:fixed;inset:20px;width:300px;height:200px;background:white';document.body.append(frame);
 }else{await fetch('/proxy/api/search');add(21);window.loaded=40;}
 window.loading=false;
});
</script>"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        def route(request):
            url = request.request.url
            if "captcha.html" in url:
                request.fulfill(content_type="text/html", body="<p>安全验证</p><button onclick=\"parent.postMessage('verified','*')\">测试验证通过</button>")
            elif "/proxy/api/search" in url:
                request.fulfill(json={"items": [], "has_more": False})
            elif "search_result.html" in url:
                request.fulfill(content_type="text/html", body=HTML)
            else:
                request.abort()
        page.route("**/*", route)
        notifications, batches, stats = [], [], {}
        view = CaptureView()
        def intervene(status, message, kind):
            notifications.append(status)
            if status == "verification":
                assert sum(len(batch) for batch in batches) >= 20
                assert scraper.page_state(page) == "verification"
                # 仅点击本地测试夹具的“通过”按钮，模拟用户，不处理真实验证码。
                box = page.frame_locator("iframe").get_by_role("button", name="测试验证通过").bounding_box()
                view.open()
                view.submit({"action": "pointer", "points": [[(box['x'] + 10) / 390, (box['y'] + 10) / 844, 0]]})
        with patch.object(capture_verification, "AUTO_WAIT_SECONDS", .4), \
             patch.object(capture_verification, "MANUAL_WAIT_SECONDS", 8):
            goods, _ = scraper.collect(page, limit=40, on_batch=batches.append,
                                       on_intervention=intervene, diagnostics=stats, capture_view=view)
        assert len(goods) == 40, stats
        assert notifications == ["recovering", "verification", "running"], notifications
        assert len({g["goods_id"] for g in goods}) == 40
        assert not view.opened.is_set()
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<iframe style=\"display:none\" src=\"/captcha.html\"></iframe>')")
        page.wait_for_timeout(100)
        assert scraper.page_state(page) != "verification"
        print(json.dumps({"records": len(goods), "states": notifications, "stop_reason": stats["stop_reason"]}))
        # 现场出现的错误卡片：滚动不会重试，必须使用该卡片自己的刷新按钮。
        retry_page = browser.new_page(viewport={"width": 1000, "height": 760})
        fixture = HTML.replace("add(1);", """add(1);
          const error=document.createElement('div');error.id='network-error';
          error.innerHTML='<span>手机网络有问题，请检查网络</span><button onclick="add(21);this.parentElement.remove()">刷新</button>';
          document.body.append(error);""")
        retry_page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=fixture))
        retry_stats = {}
        with patch.object(scraper, "STALL_SECONDS", 0), patch.object(scraper, "PAGE_RETRY_DELAYS", (.1, .2, .3)):
            retried, _ = scraper.collect(retry_page, limit=40, diagnostics=retry_stats)
        assert len(retried) == 40, retry_stats
        assert retry_stats['page_retries'] == 1
        print('Page error: bounded in-page retry resumed 20 to 40 records')
        retry_page.set_content('<p>系统繁忙</p><p>请稍后再试</p><button>刷新</button>')
        assert scraper.page_state(retry_page) == 'busy'
        retry_page.set_content('<p>访问过于频繁，请稍后再试</p>')
        assert scraper.page_state(retry_page) == 'limited'
        browser.close()


if __name__ == "__main__":
    main()
