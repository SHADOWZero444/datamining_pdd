import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import urlopen

# 打包成 exe 后，程序会去自己的目录找浏览器。这里改成用本机已经装好的浏览器。
_local_browsers = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
if _local_browsers.exists():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_local_browsers)

from playwright.sync_api import sync_playwright
from capture_browser import capture_session
from capture_verification import handle_verification

# 改这个词，就会搜这个词。
SEARCH_VALUE = ""
# 用无进展超时防止无限等待，而不是截断正常加载的搜索结果。
STALL_SECONDS = 60
PAGE_RETRY_DELAYS = (15, 30, 60)
MIN_PAGE_INTERVAL = 2.5
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


def normal_browser_executable():
    """寻找真实安装的浏览器，而不是由 Playwright 启动的自动化浏览器。"""
    roots = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")),
        Path(os.environ.get("PROGRAMFILES", "")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    candidates = []
    for root in roots:
        if root:
            candidates.extend([
                root / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                root / "Google" / "Chrome" / "Application" / "chrome.exe",
            ])
    return next((path for path in candidates if path.is_file()), None)


def available_debug_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def open_login_and_capture_cookie(timeout_seconds=600):
    """登录使用抓取所用的持久会话，验证后的站点存储也会保留。"""
    with capture_session(program_dir(), find_cookie_file(), headless=False) as page:
        page.goto(BASE + "login.html", wait_until="domcontentloaded", timeout=30000)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            items = page.context.cookies([BASE])
            if has_login_cookie(items):
                return cookie_header(items)
            if page.is_closed():
                raise RuntimeError("登录窗口已关闭，尚未获取到登录信息。")
            page.wait_for_timeout(500)
        raise RuntimeError("等待登录超时，请重新点击“前往获取”。")


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
            const cards = [...new Set([...document.querySelectorAll("img")]
                .map((image) => image.parentElement).filter(Boolean))];
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


def wait_with_cancel(page, milliseconds, should_stop, service=None):
    """把等待拆短，让“停止”不会被滚动间隔卡住。"""
    remaining = milliseconds
    while remaining > 0:
        if should_stop and should_stop():
            return False
        if service:
            service()
        step = min(200, remaining)
        page.wait_for_timeout(step)
        remaining -= step
    return True


def loading_error_retry_button(page):
    """只处理现场确认的列表加载错误，不点击验证码中的刷新或重载整页。"""
    try:
        error = page.get_by_text("手机网络有问题，请检查网络", exact=False)
        if error.count() != 1 or not error.is_visible():
            return None
        button = error.locator("..").get_by_text("刷新", exact=True)
        if button.count() == 1 and button.is_visible():
            return button
    except Exception:
        pass
    return None


def retry_delay(value, fallback):
    """Retry-After 支持秒数和 HTTP 日期，不提前重试。"""
    if value.isdigit():
        return max(fallback, int(value))
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return max(fallback, (deadline - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return fallback


def scroll_results(page):
    """连续滚动中的一段；接近底部减速，到底后不再发送滚轮事件。"""
    target = page.evaluate("""() => {
        const root = document.scrollingElement || document.documentElement;
        const candidates = [...document.querySelectorAll('div, main, section')].filter(el => {
            const style = getComputedStyle(el);
            return /(auto|scroll)/.test(style.overflowY) && el.clientHeight > 200 &&
                el.scrollHeight > el.clientHeight && el.getBoundingClientRect().width > 200 &&
                el.getBoundingClientRect().bottom > 0 && el.getBoundingClientRect().top < innerHeight;
        });
        const el = candidates.sort((a, b) =>
            b.querySelectorAll('img').length - a.querySelectorAll('img').length || b.clientHeight - a.clientHeight)[0] || root;
        const rect = el === root ? {left:0, top:0, width:innerWidth, height:innerHeight} : el.getBoundingClientRect();
        const remaining = Math.max(0, el.scrollHeight - el.clientHeight - el.scrollTop);
        const viewport = Math.min(el.clientHeight, innerHeight);
        const ratio = Math.min(1, remaining / Math.max(1, viewport * 2));
        const ease = ratio * ratio * (3 - 2 * ratio);
        const step = Math.min(remaining, Math.max(80, viewport * (.35 + .5 * ease)));
        return {x:Math.max(1, Math.min(innerWidth-2, rect.left+rect.width/2)),
                y:Math.max(1, Math.min(innerHeight-2, rect.top+Math.min(rect.height, innerHeight)/2)),
                step, at_bottom:remaining <= 2, settle_ms:Math.round(260 - 120 * ease),
                top:el.scrollTop, height:el.scrollHeight, viewport:el.clientHeight};
    }""")
    if not target["at_bottom"]:
        page.mouse.move(target["x"], target["y"])
        page.mouse.wheel(0, target["step"])
    return target


def page_state(page):
    main_state = "loading"
    for frame in page.frames:
        try:
            if frame != page.main_frame and not frame.frame_element().is_visible():
                continue
            state = frame.evaluate(r"""() => {
                const text = document.body?.innerText || '';
                if (/login\.html/.test(location.pathname) || /手机登录|请登录/.test(text)) return 'login';
                if (/captcha|verify|verification/i.test(location.pathname) ||
                    /请完成验证|安全验证|人机验证|拖动.{0,12}滑块|向右滑动|按住.{0,12}拖动|点击.{0,8}验证|验证后继续|Verify you are human|Just a moment/i.test(text)) return 'verification';
                if (!document.body || !text.trim()) return 'loading';
                if (/系统繁忙/.test(text) && /请稍后再试/.test(text)) return 'busy';
                if (/访问过于频繁|操作过于频繁/.test(text)) return 'limited';
                if (/没有更多商品|没有更多了|已显示全部结果/.test(text)) return 'end';
                return '';
            }""")
            if state in ("verification", "login"):
                return state
            if frame == page.main_frame:
                main_state = state
            elif "challenges.cloudflare.com" in frame.url:
                return "verification"
        except Exception:
            # 导航过程中 Frame 可能被替换，下次短轮询再次检查。
            continue
    return main_state


def search_page_ready(page):
    try:
        return "search_result.html" in page.url and page.evaluate("""() =>
            document.readyState !== 'loading' && !!document.body &&
            (!!window.rawData?.stores || !!document.querySelector('img'))""")
    except Exception:
        return False


def response_summary(data, response):
    """只记录结构和已知错误代码，不记录凭证、验证令牌或原始正文。"""
    summary = {"status": response.status, "content_type": response.headers.get("content-type", ""),
               "body_type": type(data).__name__}
    if isinstance(data, dict):
        summary["keys"] = list(data)[:20]
        summary["data_type"] = type(data.get("data")).__name__
        for key in ("error_code", "code", "success"):
            value = data.get(key)
            if isinstance(value, (int, bool)):
                summary[key] = value
    return summary


def collect(page, limit=None, should_stop=None, progress=None, filters=None,
            on_batch=None, diagnostics=None, on_intervention=None, browser_request=None,
            resume_request=None, capture_view=None):
    """返回当前可访问的结果；只有明确的结束信号才称为完成。"""
    stats = diagnostics if diagnostics is not None else {}
    stats.update(scanned=0, matched=0, api_responses=0, unreadable_responses=0,
                 rate_limits=0, http_errors={}, stop_reason="running", message="",
                 response_samples=[], verification_count=0, scrolls=0, page_retries=0)
    seen, accepted, pending = {}, {}, {}
    total = None
    blocked = []
    exhausted = False
    challenge_response = False
    last_new = time.monotonic()
    pending_requests = {}
    last_request = float("-inf")
    page_interval = MIN_PAGE_INTERVAL

    def request_started(request):
        nonlocal last_request
        if is_search_api(request):
            last_request = time.monotonic()
            pending_requests[id(request)] = last_request

    def request_finished(request):
        nonlocal page_interval
        started = pending_requests.pop(id(request), None)
        if started is not None:
            latency = time.monotonic() - started
            floor = min(30, MIN_PAGE_INTERVAL * 2 ** stats["rate_limits"])
            page_interval = min(30, max(floor, page_interval * .7 + latency * 1.5 * .3))
            stats["page_interval_seconds"] = round(page_interval, 2)

    def finish(reason, message):
        stats.update(stop_reason=reason, message=message)

    def report(message=None):
        if progress:
            progress(message or f"正在抓取，已读取 {len(seen)} 条，符合条件 {len(accepted)} 条",
                     len(accepted), total)

    def service_browser():
        if browser_request and browser_request.is_set():
            browser_request.clear()
            if capture_view:
                capture_view.open()
        if capture_view:
            capture_view.service(page, should_stop)

    def resume_requested():
        if resume_request and resume_request.is_set():
            resume_request.clear()
            return True
        return False

    def retry_page():
        if stats["page_retries"] >= len(PAGE_RETRY_DELAYS):
            return False
        if capture_view and capture_view.opened.is_set():
            return False
        button = loading_error_retry_button(page)
        if button is None:
            return False
        stats["page_retries"] += 1
        try:
            button.click(timeout=1500)
        except Exception:
            pass
        return True

    def intervene(kind, require_confirmation=False):
        nonlocal last_new, exhausted, challenge_response
        stats["verification_count"] += 1
        # 进入等待后再接受新的“继续”指令，避免旧点击跳过本次处理。
        if resume_request:
            resume_request.clear()
        result = handle_verification(
            page, kind, check=lambda: page_state(page),
            wait=lambda ms: wait_with_cancel(page, ms, should_stop),
            notify=on_intervention, should_stop=should_stop, service=service_browser,
            ready=lambda: search_page_ready(page), resume_requested=resume_requested,
            require_confirmation=require_confirmation,
        )
        if result == "resumed":
            if capture_view:
                capture_view.close()
            blocked.clear()
            exhausted = False
            challenge_response = False
            last_new = time.monotonic()
            # 验证弹层消失后，离开列表底部再向下，触发页面自身的下一页加载。
            page.mouse.wheel(0, -120)
            return True
        finish("stopped" if result == "stopped" else "verification_timeout",
               "已停止抓取" if result == "stopped" else "等待验证超时，已保留结果，请重新开始")
        return False

    def absorb_rows(rows):
        nonlocal last_new
        for row in rows:
            key = row.get("goods_id")
            if not key:
                continue
            if key not in seen:
                last_new = time.monotonic()
            # DOM 中缺失的字段不覆盖接口已提供的标签等信息。
            previous = seen.get(key, {})
            row = {**previous, **{k: v for k, v in row.items() if v not in (None, "", [])}}
            seen[key] = row
            if not apply_filters([row], filters):
                continue
            if key not in accepted and limit is not None and len(accepted) >= limit:
                continue
            if accepted.get(key) != row:
                accepted[key] = row
                pending[key] = dict(row)
        stats.update(scanned=len(seen), matched=len(accepted))

    def flush():
        if pending and on_batch:
            on_batch(list(pending.values()))
        pending.clear()

    def on_response(response):
        nonlocal total, exhausted, challenge_response
        if not is_search_api(response):
            return
        stats["api_responses"] += 1
        if response.status != 200:
            code = str(response.status)
            stats["http_errors"][code] = stats["http_errors"].get(code, 0) + 1
            blocked.append((response.status, response.headers.get("retry-after", "")))
            return
        try:
            data = response.json()
            stats["response_samples"] = (stats["response_samples"] + [response_summary(data, response)])[-3:]
            if not isinstance(data, dict):
                raise ValueError("not an object")
            data = data.get("data") if isinstance(data.get("data"), dict) else data
            message = " ".join(str(data.get(key) or "") for key in ("error_msg", "error_message", "message"))
            if re.search(r"验证|captcha|verification", message, re.I):
                challenge_response = True
            if str(data.get("total", "")).isdigit():
                total = int(data["total"])
            # total 仅是参考值，不作为抓取上限。
            if data.get("has_more") is False or data.get("hasMore") is False:
                exhausted = True
            rows = goods_from_api(data)
            if not rows and "items" not in data:
                stats["unreadable_responses"] += 1
            absorb_rows(rows)
        except Exception:
            # 响应体可能已失效或并非 JSON；继续从浏览器已渲染的卡片读取。
            stats["unreadable_responses"] += 1

    page.on("response", on_response)
    page.on("request", request_started)
    page.on("requestfinished", request_finished)
    page.on("requestfailed", request_finished)
    try:
        if should_stop and should_stop():
            finish("stopped", "已停止抓取")
            return [], None
        report("正在打开搜索页…")
        # commit 后以短等待读取页面，让首批数据和停止操作不必等待整个页面加载。
        page.goto(search_url(SEARCH_VALUE), wait_until="commit", timeout=15000)
        while True:
            service_browser()
            if should_stop and should_stop():
                finish("stopped", "已停止抓取")
                break
            if capture_view and capture_view.opened.is_set():
                # 用户接管时暂停自动滚动，避免验证控件或阅读位置移动。
                # 验证等待循环自身也会服务页面，不需要另一个浏览器或线程。
                if resume_requested():
                    capture_view.close()
                last_new = time.monotonic()
                wait_with_cancel(page, 200, should_stop)
                continue
            absorb_rows(read_live_goods(page))
            absorb_rows(read_dom_goods(page))
            flush()
            report()
            if limit is not None and len(accepted) >= limit:
                finish("limit", f"已达到本次上限 {limit} 条")
                break
            state = page_state(page)
            if state in ("login", "verification") or challenge_response:
                flush()
                if on_intervention and state != "login":
                    if intervene(state if state in ("login", "verification") else "verification",
                                 require_confirmation=challenge_response and state not in ("login", "verification")):
                        continue
                    break
            if state == "login":
                finish("login", "登录已失效，请在主界面重新获取登录信息后重试")
                break
            if state == "verification":
                finish("verification", "网站要求验证，请在浏览器完成验证后重试")
                break
            if state == "busy":
                finish("site_busy", "网站提示系统繁忙，请稍后再试；已保存的商品仍会保留")
                break
            if state == "limited":
                finish("rate_limited", "网站提示访问频繁，已停止抓取，请稍后再试；已保存的商品仍会保留")
                break
            if blocked:
                events = list(blocked)
                blocked.clear()
                if any(code in (401, 403) for code, _ in events):
                    finish("blocked", "网站拒绝了后续访问，请检查登录状态或稍后再试")
                    break
                limited = next((retry for code, retry in events if code == 429), None)
                if limited is not None:
                    stats["rate_limits"] += 1
                    page_interval = min(30, page_interval * 2)
                    if stats["rate_limits"] > 3:
                        finish("rate_limited", "网站持续限速，已停止抓取，请稍后再试")
                        break
                    delay = retry_delay(limited, 15 * 2 ** (stats["rate_limits"] - 1))
                    if delay > 120:
                        finish("rate_limited", "网站要求较长等待，已停止抓取，请稍后再试")
                        break
                    report(f"网站暂时限速，等待 {delay:.0f} 秒后继续；可随时停止")
                    if not wait_with_cancel(page, delay * 1000, should_stop, service_browser):
                        continue
                    # 站点限速后可能停留在错误卡片；等待结束后用原页面的刷新按钮续载。
                    retry_page()
                    last_new = time.monotonic()
                    continue
            if exhausted or state == "end":
                finish("end", "当前搜索页已没有更多结果")
                break
            if time.monotonic() - last_new >= STALL_SECONDS:
                if stats["page_retries"] < len(PAGE_RETRY_DELAYS) and loading_error_retry_button(page) is not None:
                    delay = PAGE_RETRY_DELAYS[stats["page_retries"]]
                    report(f"页面提示网络异常，等待 {delay} 秒后重试；可随时停止")
                    if not wait_with_cancel(page, delay * 1000, should_stop, service_browser):
                        continue
                    retry_page()
                    last_new = time.monotonic()
                    continue
                finish("stalled", "页面持续未加载新商品，已保留结果；可稍后重试。这不代表全部抓取完成")
                break
            # 请求未结束时只读取和等待；响应回来后保留最小间隔，避免快速连续翻页。
            if pending_requests or time.monotonic() - last_request < page_interval:
                wait_with_cancel(page, 200, should_stop)
                continue
            stats["last_scroll"] = scroll_results(page)
            stats["scrolls"] += 1
            position = stats["last_scroll"] or {}
            wait_with_cancel(page, 500 if position.get("at_bottom") else position.get("settle_ms", 200), should_stop)
            absorb_rows(read_dom_goods(page))
            flush()
    except Exception as exc:
        finish("error", "页面读取中断，已保留此前结果，请重试")
        stats["error_type"] = type(exc).__name__
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("request", request_started)
        page.remove_listener("requestfinished", request_finished)
        page.remove_listener("requestfailed", request_finished)
        flush()
        stats["api_total"] = total
    return list(accepted.values()), total


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


def run_search(keyword, limit=None, filters=None, cancel_event=None, progress=None,
               on_batch=None, diagnostics=None, on_intervention=None, browser_request=None,
               resume_request=None, on_browser_ready=None, capture_view=None):
    """给界面调用。keyword 是搜索词，limit 是这次最多收多少条。"""
    global SEARCH_VALUE
    SEARCH_VALUE = (keyword or "").strip()
    if not SEARCH_VALUE:
        raise RuntimeError("搜索词不能为空")
    errors = []
    diagnostics = diagnostics if diagnostics is not None else {}
    goods = []
    total = None
    try:
        with capture_session(program_dir(), find_cookie_file(),
                             cancel_event.is_set if cancel_event else None) as page:
            if on_browser_ready:
                on_browser_ready(True)
            goods, total = collect(
                page,
                limit=limit,
                should_stop=cancel_event.is_set if cancel_event else None,
                progress=progress,
                filters=filters,
                on_batch=on_batch,
                diagnostics=diagnostics,
                on_intervention=on_intervention,
                browser_request=browser_request,
                resume_request=resume_request,
                capture_view=capture_view,
            )
    except RuntimeError as exc:
        errors.append(str(exc))
    finally:
        if capture_view:
            capture_view.close()
        if on_browser_ready:
            on_browser_ready(False)
    if diagnostics.get("stop_reason") not in (None, "end", "limit", "stopped"):
        errors.append(diagnostics["message"])
    if not goods and diagnostics.get("scanned") and not errors:
        errors.append(f"已读取 {diagnostics['scanned']} 条商品，但没有符合筛选条件的结果")
    return goods, total, errors


if __name__ == "__main__":
    main()
