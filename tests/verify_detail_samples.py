"""离线浏览器回放两份本机 HTML 样本；不导入 Cookie，不请求线上站点。"""
import argparse
import json
import re
import sys
from pathlib import Path
from threading import Event
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from pdd.browser.mobile import configure_mobile_page
from pdd.scraping import service
from pdd.storage import results


def fixture(path):
    html = path.read_text(encoding="utf-8")
    match = re.search(r"(?:window\.)?rawData\s*=\s*\{", html)
    raw, _ = json.JSONDecoder().raw_decode(html[match.end()-1:])
    goods = raw["store"]["initDataObj"]["goods"]
    key = str(goods["goodsID"])
    # 只回放 DOM，不运行站点脚本或注入预期字段。
    html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.I | re.S)
    return key, html


def replay(samples, workspace, limited=False):
    expected = {}
    documents = {}
    for name, shop, sales in [
        ("无品牌黑标的商品详情html元素.md", "英迪耐数码旗舰店", "已拼123件"),
        ("有品牌黑标的拼多多商品详情页面html元素.md", "惠普", "已拼456件"),
    ]:
        key, html = fixture(samples / name)
        documents[key], expected[key] = html, (shop, sales)
    cards, models = [], []
    for index, key in enumerate(expected):
        url = f"/goods.html?goods_id={key}&from_card=yes"
        models.append({"goodsID": key, "goodsName": "测试商品" + str(index), "linkURL": url, "salesTip":expected[key][1]})
        # 复现列表点击更新 history 的行为。详情流程必须不触发这个处理器。
        click = "history.replaceState(null,'','?lost_list=yes');return false"
        if index:
            cards.append(f'<a href="{url}" onclick="{click}">商品二</a>')
        else:
            cards.append(f'<div id="card" onclick="{click}"><img width=80 height=80 alt="商品一"></div>')
    listing = ("<!doctype html><meta name='viewport' content='width=device-width'>" + "".join(cards) +
        "<script>const list=" + json.dumps(models) + ";window.rawData={stores:{store:{data:{ssrListData:{list}}}}};"
        "card.__reactFiber$offline={memoizedProps:{data:list[0]}};</script>")
    delivered = []
    saved_batches = []
    def route(r):
        u = urlsplit(r.request.url)
        if u.path == "/search_result.html":
            return r.fulfill(content_type="text/html;charset=UTF-8", body=listing)
        if u.path == "/goods.html":
            q = parse_qs(u.query)
            key = q["goods_id"][0]
            delivered.append((key, q.get("from_card"), urlsplit(r.request.headers.get("referer", "")).path))
            if limited and key == list(expected)[1]:
                return r.fulfill(status=429, headers={"Retry-After":"600"}, body="busy")
            if key in documents and q.get("from_card")==["yes"]:
                return r.fulfill(content_type="text/html;charset=UTF-8", body=documents[key],
                                 headers={"Content-Security-Policy":"script-src 'none'; connect-src 'none'"})
            return r.fulfill(status=403)
        r.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--disable-background-networking"])
        ctx = browser.new_context(offline=True, service_workers="block")
        ctx.route("**/*", route)
        page = ctx.new_page()
        configure_mobile_page(page)
        cancel = Event()
        def save(batch):
            results.merge_goods("回放", batch)
            saved_batches.extend(r["goods_id"] for r in batch if r.get("detail"))
            if not limited and set(saved_batches) == set(expected):
                cancel.set()
        stats = {}
        with patch.object(service, "capture_session") as session, patch.object(service, "find_cookie_file"), \
             patch.object(results, "DATA_DIR", workspace):
            session.return_value.__enter__.return_value = page
            _, _, errors = service.run_search("回放", cancel_event=cancel, diagnostics=stats, on_batch=save)
            saved = results.result_feed("回放")["goods"]
            print([(r["goods_id"], r.get("shop_name"), r.get("sales"), len((r.get("detail") or {}).get("images", []))) for r in saved])
        assert sorted(delivered) == sorted((k, ["yes"], "/search_result.html") for k in expected), delivered
        assert stats["detail_navigation"] == {"card_link": 2}, stats
        assert "lost_list" not in page.url
        if limited:
            assert errors and stats["detail_stop_reason"] == "rate_limited" and stats["detail_wait_seconds"] >= 600, stats
            assert len(saved)==2 and set(saved_batches)=={next(iter(expected))} and stats["detail_complete"]==1
            assert all(r["sales"] == expected[r["goods_id"]][1] for r in saved)
            print("详情 429：第一件详情和两条列表记录保留，未提前重试。")
            browser.close()
            return
        assert not errors and stats["stop_reason"] == "stopped", (errors, stats)
        assert set(saved_batches) == set(expected)
        # 用同一 JSON 和实际前端渲染函数检查列表字段、详情图，不加载图片资源。
        ui = ctx.new_page()
        root = Path(__file__).resolve().parents[1]
        ui.add_script_tag(path=str(root/"web/js/core/ui.js"))
        ui.add_script_tag(path=str(root/"web/js/features/result-filters.js"))
        ui.add_script_tag(path=str(root/"web/js/features/search.js"))
        for row in saved:
            key = row["goods_id"]
            assert (row["shop_name"], row["sales"]) == expected[key], (key,row.get("detail"))
            gallery = row["detail"]["images"]
            assert len(set(gallery)) == len(gallery) and len(gallery) > 1
            ui.evaluate("""row => {
                const state={goods:{sample:[row]},job:{},metadata:{},views:{},auth:{},refreshErrors:{}};
                const f=createSearchFeature({state,esc,money,safeWebUrl,badgeText:String,numericValue,
                  entryOf:()=>null,TAGS:[],preferences:{pageSize:10,images:false}});
                document.body.innerHTML=f.resultPage('sample') + f.detailPage({keyword:'sample',goodsId:row.goods_id});
            }""", row)
            assert ui.locator(".catalog .shop").inner_text() == expected[key][0]
            assert ui.locator(".catalog .sales").inner_text() == expected[key][1]
            assert ui.locator(".detail-gallery img").count() == len(gallery)
            for value in expected[key]:
                assert value in ui.locator(".detail dl").inner_text()
            print(row["shop_name"], row["sales"], len(gallery), "张详情图片")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(dir=args.samples, prefix="pdd-detail-replay-") as temp:
        replay(args.samples, Path(temp)/"normal")
        replay(args.samples, Path(temp)/"limited", limited=True)
