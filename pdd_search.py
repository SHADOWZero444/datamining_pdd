import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

# 打包成 exe 后，程序会去自己的目录找浏览器。这里改成用本机已经装好的浏览器。
_local_browsers = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
if _local_browsers.exists():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_local_browsers)

from playwright.sync_api import sync_playwright

# 改这个词，就会搜这个词。
SEARCH_VALUE = ""
# 页面上的“1万+”不是接口真正返回的数量。翻页接口有自己的 total，到了就停。
# 这是防止页面卡住时无限滚动的保险上限。
SAFETY_LIMIT = 500
BASE = "https://mobile.pinduoduo.com/"


class SearchCancelled(RuntimeError):
    """保留已读取的数据并正常结束用户主动停止的抓取。"""

    def __init__(self, goods, total):
        super().__init__("已停止抓取")
        self.goods = goods
        self.total = total


def program_dir():
    """软件本体所在的文件夹。打包后是 exe 旁边，开发时是项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_cookie_file():
    """按顺序找登录文件：软件旁边、当前目录、项目目录。"""
    candidates = [
        program_dir() / "cookie.txt",
        Path.cwd() / "cookie.txt",
        Path(__file__).resolve().parent / "cookie.txt",
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return candidates[0]
OUTPUT_FILE = Path("pdd_goods.jsonl")
MANIFEST_FILE = Path("pdd_manifest.json")
PHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)


def load_cookie_items():
    """把 cookie.txt 拆成浏览器能用的一条条记录。这是登录通行证。"""
    cookie_file = find_cookie_file()
    if not cookie_file.is_file():
        raise RuntimeError("还没有登录信息。请把 cookie.txt 放在软件同一文件夹里，然后重新打开软件。")
    text = cookie_file.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("登录信息是空的。请重新保存 cookie.txt 后再试。")
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


def cookie_header(items):
    return "; ".join(f"{item['name']}={item['value']}" for item in items if item.get("name") and item.get("value"))


def has_login_cookie(items):
    """只接受登录后才会出现的身份标记，避免把初始访客 cookie 当作成功。"""
    names = {str(item.get("name") or "").casefold() for item in items}
    return "pddaccesstoken" in names or ({"pdd_user_id", "pdd_user_uin"} <= names)


def open_login_and_capture_cookie(timeout_seconds=600):
    """在独立的本机浏览器资料夹完成登录，再导出该站点的登录 cookie。"""
    profile = program_dir() / "data" / "login-browser"
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as browser_factory:
        context = None
        first_error = None
        for channel in (None, "msedge", "chrome"):
            try:
                options = {
                    "headless": False,
                    "user_agent": PHONE_UA,
                    "viewport": {"width": 390, "height": 844},
                    "locale": "zh-CN",
                }
                if channel:
                    options["channel"] = channel
                context = browser_factory.chromium.launch_persistent_context(str(profile), **options)
                break
            except Exception as exc:
                first_error = exc
        if context is None:
            raise RuntimeError("无法打开登录浏览器，请安装 Microsoft Edge 或 Chrome 后重试。") from first_error
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://mobile.pinduoduo.com/login.html", wait_until="domcontentloaded", timeout=30000)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                items = [
                    item for item in context.cookies()
                    if str(item.get("domain") or "").lstrip(".").endswith("pinduoduo.com")
                ]
                if has_login_cookie(items):
                    return cookie_header(items)
                if page.is_closed():
                    raise RuntimeError("登录窗口已关闭，尚未获取到登录信息。")
                page.wait_for_timeout(1000)
            raise RuntimeError("等待登录超时，请重新点击“前往获取”。")
        finally:
            context.close()


def read_js_string(block, key):
    """从保存下来的页面脚本里取出一个字符串字段。"""
    match = re.search(
        rf"{key}:\s*(\"((?:\\.|[^\"\\])*)\"|'((?:\\.|[^'\\])*)')",
        block,
    )
    if not match:
        return ""
    return json.loads(match.group(1))


# 标签看图片地址里的固定片段，不看整张图是否相同。
BADGE_MARKS = {
    "bybt": "bybt",
    "black": "brand_black_label",
    "official": "6e9dba7f-bef0-4263-a355-e42dc63485c7",
    "flagship": "962149d1-b03a-47fb-be05-7f289e14ed3b",
}


def badges_from_urls(urls):
    """根据角标图片地址判断商品带了哪些标签。"""
    text = " ".join(urls).lower()
    return [key for key, mark in BADGE_MARKS.items() if mark.lower() in text]


def urls_in_block(block):
    raw = re.findall(r'url:\s*"((?:\\.|[^"\\])*)"', block)
    urls = []
    for item in raw:
        try:
            urls.append(json.loads(f'"{item}"'))
        except json.JSONDecodeError:
            urls.append(item)
    return urls


def urls_in_model(model):
    urls = []

    def walk(node):
        if isinstance(node, str) and "http" in node:
            urls.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in ("url", "img_url", "image_url") or "icon" in key.lower():
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for key in ("icon_list", "prefix_icons", "prefixIcons"):
        walk(model.get(key))
    return urls


def parse_price(value):
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def apply_filters(goods, filters):
    """按标签、价格区间、禁用词留下符合条件的商品。"""
    filters = filters or {}
    wanted = set(filters.get("tags") or [])
    banned = [word.strip() for word in str(filters.get("banned") or "").split(",") if word.strip()]
    low = filters.get("price_min")
    high = filters.get("price_max")
    kept = []
    for item in goods:
        marks = set(item.get("badges") or [])
        if wanted and not wanted.issubset(marks):
            continue
        price = parse_price(item.get("price"))
        if low is not None and (price is None or price < low):
            continue
        if high is not None and (price is None or price > high):
            continue
        title = (item.get("title") or "").casefold()
        if any(word.casefold() in title for word in banned):
            continue
        kept.append(item)
    return kept


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
        icon_block = re.search(r"prefixIcons:\s*\[(.*?)\]", block, re.S)
        seen.add(goods_id)
        goods.append({
            "goods_id": goods_id,
            "title": name,
            "price": read_js_string(head, "priceInfo"),
            "sales": read_js_string(head, "salesTip"),
            "image": read_js_string(head, "imgUrl"),
            "mall_id": mall.group(1) if mall else "",
            "tags": tags,
            "badges": badges_from_urls(urls_in_block(icon_block.group(1) if icon_block else "")),
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
                shop_name: item.mallName || item.mall_name || (item.mallEntrance && (item.mallEntrance.mallName || item.mallEntrance.mall_name)) || "",
                tags: (item.tagList || []).map((tag) => tag.text).filter(Boolean),
                icons: (item.prefixIcons || []).map((icon) => icon && icon.url).filter(Boolean),
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
        row["badges"] = badges_from_urls(row.pop("icons", []) or [])
        goods.append(row)
    return goods


def read_dom_goods(page):
    """接口现在是加密的。商品画在页面上，就从卡片里把编号、名字和价格读出来。"""
    rows = page.evaluate(
        """() => {
            function readCard(node) {
                const fiberKey = Object.keys(node).find((key) => key.startsWith("__reactFiber"));
                let fiber = fiberKey ? node[fiberKey] : null;
                for (let i = 0; i < 18 && fiber; i += 1) {
                    const props = fiber.memoizedProps || {};
                    const data = props.data || props.item || props.goods || props.goodsData;
                    if (data && (data.goodsID || data.goods_id)) {
                        return {
                            goods_id: String(data.goodsID || data.goods_id),
                            title: data.goodsName || data.goods_name || "",
                            price: data.priceInfo || data.price_info || "",
                            sales: data.salesTip || data.sales_tip || "",
                            image: data.imgUrl || data.thumb_url || data.hd_thumb_url || "",
                            link: data.linkURL || data.link_url || "",
                            mall_id: String((data.mallEntrance && data.mallEntrance.mall_id) || data.mall_id || ""),
                            shop_name: (data.mallName || data.mall_name || (data.mallEntrance && (data.mallEntrance.mallName || data.mallEntrance.mall_name || data.mallEntrance.name)) || "").replace(/[\uE000-\uF8FF]/g, "").trim(),
                            icons: (data.prefixIcons || []).map((icon) => icon && icon.url).filter(Boolean),
                        };
                    }
                    fiber = fiber.return;
                }
                return null;
            }
            const cards = [...document.querySelectorAll("div")].filter((node) => {
                const text = node.innerText || "";
                return text.includes("¥") && (text.includes("已拼") || text.includes("已抢")) && text.length < 280 && node.querySelector("img");
            });
            const rows = [];
            const seen = new Set();
            cards.forEach((card) => {
                const row = readCard(card);
                if (row && row.goods_id && !seen.has(row.goods_id)) {
                    if (!row.shop_name) {
                        const lines = (card.innerText || "").split("\\n").map((line) => line.replace(/[\uE000-\uF8FF]/g, "").trim()).filter(Boolean);
                        row.shop_name = lines.find((line) => line !== row.title && !line.includes("¥") && !line.includes("已拼") && !line.includes("已抢") && line.length <= 20) || "";
                    }
                    seen.add(row.goods_id);
                    rows.push(row);
                }
            });
            return rows;
        }"""
    )
    goods = []
    for row in rows:
        link = row.get("link") or ""
        row["url"] = urljoin(BASE, link) if link else f"{BASE}goods.html?goods_id={row['goods_id']}"
        row.pop("link", None)
        row["keyword"] = SEARCH_VALUE
        row["tags"] = []
        row["shop_name"] = (row.get("shop_name") or "").strip()
        row["badges"] = badges_from_urls(row.pop("icons", []) or [])
        goods.append(row)
    return goods


def page_needs_login(page):
    url = page.url
    text = page.inner_text("body")
    return "login" in url or "手机登录" in text or "请登录" in text


def is_search_api(response):
    """翻页接口。第一页在页面里，第 21 条开始从这里来。"""
    path = response.url.split("?", 1)[0].rstrip("/")
    return path.endswith("/proxy/api/search")


def normalize_model(model):
    """把接口里的 goods_model 收成一条商品。没有 goods_id 的就丢掉。"""
    goods_id = model.get("goods_id")
    if not goods_id or not model.get("goods_name"):
        return None
    link = model.get("link_url") or ""
    tags = [tag.get("text") for tag in (model.get("tag_list") or []) if isinstance(tag, dict) and tag.get("text")]
    return {
        "goods_id": str(goods_id),
        "title": model.get("goods_name") or "",
        "price": model.get("price_info") or "",
        "sales": model.get("sales_tip") or "",
        "image": model.get("thumb_url") or model.get("hd_thumb_url") or "",
        "mall_id": str(model.get("mall_id") or ""),
        "shop_name": model.get("mall_name") or model.get("mallName") or "",
        "tags": tags,
        "badges": badges_from_urls(urls_in_model(model)),
        "url": urljoin(BASE, link) if link else f"{BASE}goods.html?goods_id={goods_id}",
        "keyword": SEARCH_VALUE,
    }


def goods_from_api(data):
    rows = []
    for item in data.get("items") or []:
        model = ((item or {}).get("item_data") or {}).get("goods_model") or {}
        row = normalize_model(model)
        if row:
            rows.append(row)
    return rows


def wait_with_cancel(page, milliseconds, should_stop):
    """把等待拆短，让“停止”不会被滚动间隔卡住。"""
    remaining = milliseconds
    while remaining > 0:
        if should_stop and should_stop():
            return False
        step = min(200, remaining)
        page.wait_for_timeout(step)
        remaining -= step
    return True


def collect(page, limit=None, should_stop=None, progress=None):
    seen = {}
    total = None
    blocked = []

    def check_cancelled():
        if should_stop and should_stop():
            raise SearchCancelled(list(seen.values()), total)

    def on_response(response):
        nonlocal total
        if not is_search_api(response):
            return
        if response.status != 200:
            blocked.append(response.status)
            return
        try:
            data = response.json()
        except Exception:
            return
        if data.get("total"):
            total = int(data["total"])
        for row in goods_from_api(data):
            seen[row["goods_id"]] = row

    page.on("response", on_response)
    check_cancelled()
    if progress:
        progress("正在打开搜索页…", 0, None)
    page.goto(search_url(SEARCH_VALUE), wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText : "";
                return !!(window.rawData && window.rawData.stores) || text.includes("已拼") || text.includes("已抢");
            }""",
            timeout=20000,
        )
    except Exception:
        pass
    if page_needs_login(page):
        raise RuntimeError(
            "登录已失效。请重新登录拼多多，把新的 cookie.txt 放到软件同一文件夹里。"
        )

    def absorb():
        if page.evaluate("() => !!(window.rawData && window.rawData.stores)"):
            for item in read_live_goods(page):
                seen[item["goods_id"]] = item
        for item in read_dom_goods(page):
            seen[item["goods_id"]] = item

    absorb()
    if progress:
        progress(f"正在抓取，已读取 {len(seen)} 条", len(seen), total)
    if not seen:
        title = page.title()
        raise RuntimeError(f"没有读到商品。当前页面是：{title}")
    # 连续几次滚完都没有新商品，才认为到底了。滚一次没反应就停，会把后面的页丢掉。
    stalls = 0
    cap = limit if limit else SAFETY_LIMIT
    while len(seen) < cap:
        check_cancelled()
        if total and len(seen) >= total:
            break
        before = len(seen)
        page.mouse.move(180, 520)
        page.mouse.wheel(0, 6000)
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        if not wait_with_cancel(page, 1600, should_stop):
            check_cancelled()
        absorb()
        if len(seen) == before:
            stalls += 1
            if blocked and blocked[-1] == 429:
                print("翻页被限速，等 15 秒再继续")
                blocked.clear()
                time.sleep(15)
        else:
            stalls = 0
            print(f"已收集 {len(seen)} 条" + (f" / {total}" if total else ""))
        if progress:
            progress(f"正在抓取，已读取 {len(seen)} 条", len(seen), total)
        if stalls >= 4:
            break
        if not wait_with_cancel(page, 1500, should_stop):
            check_cancelled()
    if blocked:
        print(f"翻页接口被拒绝 {len(blocked)} 次，状态码 {blocked[-1]}")
    if total and len(seen) < total:
        print(f"接口显示最多 {total} 条，这次拿到 {len(seen)} 条")
    return list(seen.values()), total


def write_output(goods, errors, total):
    lines = [json.dumps(item, ensure_ascii=False) for item in goods]
    OUTPUT_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    reached = not total or len(goods) >= total
    status = "ok" if goods and not errors and reached else ("partial" if goods else "failed")
    manifest = {
        "scrape_started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keyword": SEARCH_VALUE,
        "status": status,
        "records_written": len(goods),
        "api_total": total,
        "errors": errors,
        "tool": "playwright",
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def launch_browser(browser_factory):
    """先用本机 Playwright 浏览器。找不到时再用系统自带的 Edge 或 Chrome。"""
    try:
        return browser_factory.chromium.launch(headless=True)
    except Exception as first_error:
        for channel in ("msedge", "chrome"):
            try:
                return browser_factory.chromium.launch(headless=True, channel=channel)
            except Exception:
                continue
        raise first_error


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    errors = []
    goods = []
    total = None
    with sync_playwright() as browser_factory:
        browser = launch_browser(browser_factory)
        context = browser.new_context(
            user_agent=PHONE_UA,
            viewport={"width": 390, "height": 844},
            locale="zh-CN",
        )
        context.add_cookies(load_cookie_items())
        page = context.new_page()
        try:
            goods, total = collect(page)
        except RuntimeError as exc:
            errors.append(str(exc))
            print(exc)
        finally:
            browser.close()
    write_output(goods, errors, total)
    for item in goods[:10]:
        print(f"{item['title']}\t{item['price']}\t{item['sales']}")
    print(f"共 {len(goods)} 条，已写入 {OUTPUT_FILE.name}")


def run_search(keyword, limit=None, filters=None, cancel_event=None, progress=None):
    """给界面调用。keyword 是搜索词，limit 是这次最多收多少条。"""
    global SEARCH_VALUE
    SEARCH_VALUE = (keyword or "").strip()
    if not SEARCH_VALUE:
        raise RuntimeError("搜索词不能为空")
    errors = []
    goods = []
    total = None
    with sync_playwright() as browser_factory:
        browser = launch_browser(browser_factory)
        context = browser.new_context(
            user_agent=PHONE_UA,
            viewport={"width": 390, "height": 844},
            locale="zh-CN",
        )
        context.add_cookies(load_cookie_items())
        page = context.new_page()
        try:
            goods, total = collect(
                page,
                limit=limit,
                should_stop=cancel_event.is_set if cancel_event else None,
                progress=progress,
            )
        except SearchCancelled as exc:
            goods, total = exc.goods, exc.total
            errors.append("已停止抓取")
        except RuntimeError as exc:
            errors.append(str(exc))
        finally:
            browser.close()
    before = len(goods)
    goods = apply_filters(goods, filters)
    if before and not goods:
        errors.append("抓到了商品，但没有一条符合筛选条件")
    return goods, total, errors


if __name__ == "__main__":
    main()
