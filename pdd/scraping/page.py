"""搜索页面状态、翻页和可取消等待。"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from .constants import BASE

def search_url(keyword):
    return f"{BASE}search_result.html?search_key={quote(keyword)}&search_met=manual&source=index"


def is_search_api(response):
    """翻页接口。第一页在页面里，第 21 条开始从这里来。"""
    path = response.url.split("?", 1)[0].rstrip("/")
    return path.endswith("/proxy/api/search")


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
