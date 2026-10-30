"""单次抓取循环：合并、筛选、限流、增量回调与中断处理。"""
import re
import time
from pdd.browser.verification import handle_verification
from .filters import apply_filters
from .parsing import read_live_goods, read_dom_goods, goods_from_api
from .page import search_url, is_search_api, wait_with_cancel, loading_error_retry_button, retry_delay, scroll_results, page_state, search_page_ready, response_summary

STALL_SECONDS = 60
PAGE_RETRY_DELAYS = (15, 30, 60)
MIN_PAGE_INTERVAL = 2.5

def collect(page, limit=None, should_stop=None, progress=None, filters=None,
            on_batch=None, diagnostics=None, on_intervention=None, browser_request=None,
            resume_request=None, capture_view=None, keyword=""):
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
            rows = goods_from_api(data, keyword=keyword)
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
        page.goto(search_url(keyword), wait_until="commit", timeout=15000)
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
            absorb_rows(read_live_goods(page, keyword=keyword))
            absorb_rows(read_dom_goods(page, keyword=keyword))
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
            absorb_rows(read_dom_goods(page, keyword=keyword))
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
