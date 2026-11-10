"""断网 Edge 回归：20 + 20 两批，检查详情读取不重置列表，普通空页不截断翻页。"""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from pdd.browser.mobile import configure_mobile_page
from pdd.scraping import service
from pdd.scraping.pacing import PagePacer
from pdd.storage import results


LIST = """<!doctype html><meta name="viewport" content="width=device-width">
<main id="list" style="height:400px;overflow:auto"></main>
<script>
const list=document.querySelector('#list'), first=[];
sessionStorage.setItem("fixture","batch-session");
window.Navigation={getNextPageQuery:()=>({refer_page_id:'fixture-page',refer_page_name:'search_result'})};
function add(id){
    const model={goodsID:id,goodsName:'商品'+id,salesTip:'已拼'+(id+100)+'件',linkURL:'/goods.html?goods_id='+id+'&card=batch'};
    const el=document.createElement('div'); el.style.height='75px';
    el.innerHTML='<img alt="商品">商品'+id;
    el.onclick=()=>{history.replaceState(null,'','?list_changed=yes');list.scrollTop=0};
    el.__reactFiber$offline={memoizedProps:{data:model}};
    list.append(el); return model;
}
for(let id=1;id<=20;id++) first.push(add(id));
window.rawData={stores:{store:{data:{ssrListData:{list:first}}}}};
let more=true;
list.onscroll=async()=>{
    if(more && list.scrollTop+list.clientHeight>=list.scrollHeight-40){
        more=false; const res=await fetch('/proxy/api/search');
        for(const item of (await res.json()).items) add(item.item_data.goods_model.goods_id);
    }
};
</script>"""


def verify(browser, directory, empty):
    ctx = browser.new_context(offline=True, service_workers="block")
    opened, pages = [], []
    def route(r):
        u = urlsplit(r.request.url)
        if u.path == "/search_result.html":
            pages.append("list")
            return r.fulfill(content_type="text/html", body=LIST)
        if u.path == "/proxy/api/search":
            pages.append(len(opened))
            return r.fulfill(json={"has_more":False, "items":[{"item_data":{"goods_model":{
                "goods_id":n, "goods_name":f"商品{n}", "sales_tip":f"已拼{n+100}件", "link_url":f"/goods.html?goods_id={n}&card=batch"
            }}} for n in range(21,41)]})
        if u.path == "/goods.html":
            q = parse_qs(u.query)
            assert q["card"] == ["batch"] and urlsplit(r.request.headers["referer"]).path == "/search_result.html"
            assert q.get("_x_query")==["批次"] and q.get("page_from")==["23"] and q.get("refer_page_id")==["fixture-page"]
            key = int(q["goods_id"][0])
            opened.append(key)
            if empty:
                return r.fulfill(status=404, body="unavailable")
            return r.fulfill(content_type="text/html;charset=UTF-8", body=f"""<div id="main">
                <p>¥10 <span>已拼{key}件</span></p>
                <div><img><div>店铺{key}</div><span role="button">进店逛逛</span></div>
                <img aria-label="商品大图" data-src="https://img.example/image-{key}">
                <img aria-label="查看图片" data-src="https://img.example/detail-{key}"></div>
                <script>if(window.opener || sessionStorage.getItem('fixture')!=='batch-session')
                    document.getElementById('main').remove();</script>""")
        r.abort()
    ctx.route("**/*", route)
    anchor = ctx.new_page()
    configure_mobile_page(anchor)
    stats = {}
    with patch.object(service, "capture_session") as session, patch.object(service,"find_cookie_file"), \
            patch.object(results,"DATA_DIR",directory), patch.object(PagePacer,"ready",return_value=True):
        session.return_value.__enter__.return_value = anchor
        _, _, errors = service.run_search("批次", limit=40, diagnostics=stats,
                                          on_batch=lambda batch: results.merge_goods("批次", batch))
        rows = results.result_feed("批次")["goods"]
    assert {int(r["goods_id"]) for r in rows} == set(range(1,41)), stats
    assert "list_changed" not in anchor.url and stats["stop_reason"] == "limit", stats
    if empty:
        assert errors and pages == ["list",3] and opened == [1,2,3] and stats["detail_skip_reason"] == "unavailable", stats
    else:
        assert not errors and pages == ["list",20] and opened == list(range(1,41)), (pages,opened,stats)
        assert stats["detail_complete"] == 40
        for row in rows:
            key=row["goods_id"]
            assert row["sales"] == f"已拼{int(key)+100}件" and row["shop_name"] == f"店铺{key}"
            assert row["detail"]["images"] == [f"https://img.example/image-{key}",f"https://img.example/detail-{key}"]
    print(f"两批共保存 {len(rows)} 条，详情访问 {len(opened)} 次，完整 {stats['detail_complete']} 条")
    ctx.close()


if __name__ == "__main__":
    root = Path.home()/"Documents"/"ChatGPT"
    with TemporaryDirectory(dir=root, prefix="pdd-batch-replay-") as temp, sync_playwright() as p:
        b=p.chromium.launch(channel="msedge",headless=True,args=["--disable-background-networking"])
        try:
            verify(b,Path(temp)/"ok",False)
            verify(b,Path(temp)/"empty",True)
        finally:
            b.close()
