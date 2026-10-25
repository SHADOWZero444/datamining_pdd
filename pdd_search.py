import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright

# 改这个词，就会搜这个词。
SEARCH_VALUE = "蓝牙耳机"
# 每次向下滚一屏大约再加载 20 条。页面有 1 万多件，一次全抓会把登录态用废。
MAX_PAGES = 5
BASE = "https://mobile.pinduoduo.com/"
COOKIE_FILE = Path("cookie.txt")
OUTPUT_FILE = Path("pdd_goods.jsonl")
MANIFEST_FILE = Path("pdd_manifest.json")
PHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)


def load_cookie_items():
    """把 cookie.txt 拆成浏览器能用的一条条记录。这是登录通行证，不要发到网上。"""
    if not COOKIE_FILE.exists():
        raise RuntimeError("缺少 cookie.txt")
    text = COOKIE_FILE.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("cookie.txt 是空的")
    items = []
    for part in text.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        items.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".pinduoduo.com",
            "path": "/",
        })
    if not items:
        raise RuntimeError("cookie.txt 里没有有效内容")
    return items


def read_js_string(block, key):
    """从保存下来的页面脚本里取出一个字符串字段。"""
    match = re.search(
        rf"{key}:\s*(\"((?:\\.|[^\"\\])*)\"|'((?:\\.|[^'\\])*)')",
        block,
    )
    if not match:
        return ""
    return json.loads(match.group(1))


def parse_goods(html):
    """解析你保存的 pdd.html。线上页面改由浏览器直接读 rawData。"""
    goods = []
    seen = set()
    for block in re.split(r"(?=goodsID:\s*\d+)", html):
        id_match = re.match(r"goodsID:\s*(\d+)", block)
        if not id_match:
            continue
        goods_id = id_match.group(1)
        if goods_id in seen:
            continue
        head = block.split("logData:", 1)[0]
        name = read_js_string(head, "goodsName")
        if not name:
            continue
        link = read_js_string(block, "linkURL")
        mall = re.search(r"mall_id:\s*(\d+)", block)
        tags = [json.loads(f'"{text}"') for text in re.findall(r'text:\s*"((?:\\.|[^"\\])*)"', head)]
        seen.add(goods_id)
        goods.append({
            "goods_id": goods_id,
            "title": name,
            "price": read_js_string(head, "priceInfo"),
            "sales": read_js_string(head, "salesTip"),
            "image": read_js_string(head, "imgUrl"),
            "mall_id": mall.group(1) if mall else "",
            "tags": tags,
            "url": urljoin(BASE, link) if link else f"{BASE}goods.html?goods_id={goods_id}",
            "keyword": SEARCH_VALUE,
        })
    return goods


def search_url(keyword):
    return f"{BASE}search_result.html?search_key={quote(keyword)}&search_met=manual&source=index"


def read_live_goods(page):
    """从已经打开的搜索页里读商品。price 是页面上显示的价格，不是分为单位的原价。"""
    rows = page.evaluate(
        """() => {
            const data = window.rawData;
            const list = data && data.stores && data.stores.store && data.stores.store.data
                && data.stores.store.data.ssrListData && data.stores.store.data.ssrListData.list;
            if (!list) return [];
            return list.filter((item) => item.goodsID).map((item) => ({
                goods_id: String(item.goodsID),
                title: item.goodsName || "",
                price: item.priceInfo || "",
                sales: item.salesTip || "",
                image: item.imgUrl || "",
                mall_id: item.mallEntrance && item.mallEntrance.mall_id ? String(item.mallEntrance.mall_id) : "",
                tags: (item.tagList || []).map((tag) => tag.text).filter(Boolean),
                url: item.linkURL || "",
            }));
        }"""
    )
    goods = []
    for row in rows:
        link = row.get("url") or ""
        if link and not link.startswith("http"):
            row["url"] = urljoin(BASE, link)
        elif not link:
            row["url"] = f"{BASE}goods.html?goods_id={row['goods_id']}"
        row["keyword"] = SEARCH_VALUE
        goods.append(row)
    return goods


def page_needs_login(page):
    url = page.url
    text = page.inner_text("body")
    return "login" in url or "手机登录" in text or "请登录" in text


def collect(page):
    page.goto(search_url(SEARCH_VALUE), wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    if page_needs_login(page):
        raise RuntimeError(
            "网站跳到了登录页。cookie.txt 不是完整登录态。"
            "请在已登录的搜索页按 F12，打开 Network，刷新，点 search_result.html，"
            "把 Request Headers 里的整段 Cookie 覆盖写入 cookie.txt。不要把 cookie 发到聊天里。"
        )
    try:
        page.wait_for_function(
            "() => !!(window.rawData && window.rawData.stores)",
            timeout=15000,
        )
    except Exception as exc:
        raise RuntimeError("页面打开了，但没有商品数据") from exc
    seen = {}
    for _ in range(MAX_PAGES):
        for item in read_live_goods(page):
            seen[item["goods_id"]] = item
        before = len(seen)
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1500)
        for item in read_live_goods(page):
            seen[item["goods_id"]] = item
        if len(seen) == before:
            break
        time.sleep(0.5)
    return list(seen.values())


def write_output(goods, errors):
    lines = [json.dumps(item, ensure_ascii=False) for item in goods]
    OUTPUT_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    status = "ok" if goods and not errors else ("partial" if goods else "failed")
    manifest = {
        "scrape_started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keyword": SEARCH_VALUE,
        "status": status,
        "records_written": len(goods),
        "errors": errors,
        "tool": "playwright",
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    errors = []
    goods = []
    with sync_playwright() as browser_factory:
        browser = browser_factory.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=PHONE_UA,
            viewport={"width": 390, "height": 844},
            locale="zh-CN",
        )
        context.add_cookies(load_cookie_items())
        page = context.new_page()
        try:
            goods = collect(page)
        except RuntimeError as exc:
            errors.append(str(exc))
            print(exc)
        finally:
            browser.close()
    write_output(goods, errors)
    for item in goods[:10]:
        print(f"{item['title']}\t{item['price']}\t{item['sales']}")
    print(f"共 {len(goods)} 条，已写入 {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
