"""单次抓取循环：合并、筛选、限流、增量回调与中断处理。"""
import re
import time
from pdd.browser.verification import handle_verification
from .target import CollectionTarget
from .pacing import PagePacer
from .pause import wait_for_manual_resume
from .responses import error_code, failure_kind
from .parsing import read_live_goods, read_dom_goods, goods_from_api
from .page import search_url, is_search_api, wait_with_cancel, loading_error_retry_button, retry_delay, scroll_results, page_state, search_page_ready, response_summary

STALL_SECONDS = 60
PAGE_RETRY_DELAYS = (15, 30, 60)
RATE_LIMIT_INITIAL_WAIT = 60
RATE_LIMIT_MAX_FALLBACK = 900

def collect(page, limit=None, should_stop=None, progress=None, filters=None,
            on_batch=None, diagnostics=None, on_intervention=None, browser_request=None,
            resume_request=None, capture_view=None, keyword="", target=None):
    """返回当前可访问的结果；只有明确的结束信号才称为完成。"""
    target = target if target is not None else CollectionTarget(limit, filters=filters)
    pacer = PagePacer()
    stats = diagnostics if diagnostics is not None else {}
    stats.update(scanned=0, matched=0, api_responses=0, unreadable_responses=0,
                 rate_limits=0, http_errors={}, stop_reason="running", message="",
                 response_samples=[], verification_count=0, scrolls=0, page_retries=0,
                 consecutive_rate_limits=0, rate_limit_retries=0, rate_limit_recoveries=0,
                 response_timeline=[], page_interval_seconds=pacer.interval, pause_count=0, manual_resumes=0)
    stats.update(target.snapshot())
    seen, accepted, pending = {}, {}, {}
    total = None
    blocked = []
    exhausted = False
    challenge_response = False
    login_response = False
    limited_page_acknowledged = False
    started_at = last_new = time.monotonic()
    pending_requests = {}
    # 服务器的等待期限使用单调时钟；累计限速用于诊断，连续失败才消耗恢复预算。
    cooldown_until = 0
    recovery_pending = False
    limited_scanned = 0
    successful_response = False
    successful_scanned = 0

    def request_started(request):
        if is_search_api(request):
            now = time.monotonic()
            pending_requests[id(request)] = now
            pacer.started(now)

    def request_finished(request):
        started = pending_requests.pop(id(request), None)
        if started is not None:
            pacer.finished(started, time.monotonic())
            stats["page_interval_seconds"] = round(pacer.interval, 2)

    def finish(reason, message):
        stats.update(stop_reason=reason, message=message)

    def report(message=None):
        if progress:
            progress(message or f"正在抓取，{target.description()}", target.count, total)

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

    def retry_page(*, rate_limited=False):
        if not rate_limited and stats["page_retries"] >= len(PAGE_RETRY_DELAYS):
            return False
        if capture_view and capture_view.opened.is_set():
            return False
        button = loading_error_retry_button(page)
        if button is None:
            return False
        stats["rate_limit_retries" if rate_limited else "page_retries"] += 1
        try:
            button.click(timeout=1500)
        except Exception:
            return False
        return True

    def register_rate_limit(retry_after=""):
        nonlocal cooldown_until, limited_scanned, successful_response, recovery_pending
        stats["rate_limits"] += 1
        stats["consecutive_rate_limits"] += 1
        # HAR 没有 Retry-After 时只能给本地等待下限，不能宣称网站届时恢复。
        exponent = min(stats["consecutive_rate_limits"] - 1, 4)
        fallback = min(RATE_LIMIT_MAX_FALLBACK, RATE_LIMIT_INITIAL_WAIT * 2 ** exponent)
        delay = retry_delay(retry_after, fallback)
        cooldown_until = max(cooldown_until, time.monotonic() + delay)
        pacer.slow_down()
        stats["page_interval_seconds"] = round(pacer.interval, 2)
        limited_scanned = len(seen)
        successful_response = False
        recovery_pending = True
        return delay

    def pause_rate_limit(message):
        nonlocal last_new, recovery_pending, limited_scanned, successful_response
        # 没有界面继续信号的命令行调用仍正常结束，不进入无法恢复的等待。
        if on_intervention is None or resume_request is None:
            finish("rate_limited", message)
            return False
        stats["pause_count"] += 1
        stats["pause_reason"] = "rate_limited"
        limited_scanned = len(seen)
        successful_response = False
        flush()
        def notify(remaining):
            kind = "rate_limit_wait" if remaining else "rate_limit_ready"
            text = (f"网站限制访问，{target.description()}；至少等待 {remaining} 秒后可手动重试，恢复时间由网站决定"
                    if remaining else f"{target.description()}，已保留当前页面；网站是否恢复尚不确定，可稍后点击“继续抓取”重试")
            on_intervention("paused", text, kind)
            report(text)
        if not wait_for_manual_resume(page, resume_request, lambda: cooldown_until, notify, should_stop):
            finish("stopped", "已停止抓取，已保留结果")
            return False
        stats["manual_resumes"] += 1
        stats.pop("pause_reason", None)
        # 手动继续只给一次恢复机会；只有新商品实际到达才重置失败预算。
        blocked[:] = [code for code in blocked if code != 429]
        recovery_pending = True
        last_new = time.monotonic()
        on_intervention("running", "正在从当前页面继续抓取", None)
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
            # 人工验证不撤销服务器已给出的限速等待要求。
            blocked[:] = [code for code in blocked if code == 429]
            exhausted = False
            challenge_response = False
            last_new = time.monotonic()
            # 验证弹层消失后，离开列表底部再向下，触发页面自身的下一页加载。
            if time.monotonic() >= cooldown_until and not pending_requests and pacer.ready():
                scroll_results(page, retry_bottom=True)
            return True
        finish("stopped" if result == "stopped" else "verification_timeout",
               "已停止抓取" if result == "stopped" else "等待验证超时，已保留结果，请重新开始")
        return False

    def absorb_rows(rows):
        nonlocal last_new, recovery_pending, successful_response
        for row in rows:
            key = row.get("goods_id")
            if not key:
                continue
            key = str(key)
            row = {**row, "goods_id": key}
            if key not in seen:
                last_new = time.monotonic()
            # DOM 中缺失的字段不覆盖接口已提供的标签等信息。
            previous = seen.get(key, {})
            row = {**previous, **{k: v for k, v in row.items() if v not in (None, "", [])}}
            seen[key] = row
            if not target.accept(row):
                continue
            if accepted.get(key) != row:
                accepted[key] = row
                pending[key] = dict(row)
        stats.update(scanned=len(seen), matched=len(accepted), **target.snapshot())
        # 加密接口可能由 DOM 提供商品；必须同时看到成功响应和新 ID，重复页不重置预算。
        if successful_response and len(seen) > successful_scanned:
            pacer.new_page()
            stats["page_interval_seconds"] = round(pacer.interval, 2)
            if len(seen) > limited_scanned and stats["consecutive_rate_limits"]:
                stats["consecutive_rate_limits"] = 0
                stats["rate_limit_recoveries"] += 1
                recovery_pending = False
            successful_response = False

    def flush():
        if pending and on_batch:
            on_batch(list(pending.values()))
        pending.clear()

    def on_response(response):
        nonlocal total, exhausted, challenge_response, login_response, successful_response, successful_scanned
        if not is_search_api(response):
            return
        stats["api_responses"] += 1
        sample = {"elapsed_seconds": round(time.monotonic() - started_at, 2),
                  "status": response.status, "scanned": len(seen)}
        # 只保留最近 60 次的数值摘要，不保存请求 URL、Cookie、令牌或原始正文。
        stats["response_timeline"] = (stats["response_timeline"] + [sample])[-60:]
        # 错误正文同样需要解析；403/40001 与 429/40002 是不同的恢复路径。
        try:
            data = response.json()
        except Exception:
            data = None
        stats["response_samples"] = (stats["response_samples"] + [response_summary(data, response)])[-3:]
        code = error_code(data)
        if code is not None:
            sample["error_code"] = code
        try:
            body = response.request.post_data_json
            for key in ("page", "size"):
                if isinstance(body, dict) and type(body.get(key)) is int:
                    sample[key] = body[key]
        except Exception:
            pass
        if response.status != 200:
            status = str(response.status)
            stats["http_errors"][status] = stats["http_errors"].get(status, 0) + 1
        failure = failure_kind(response.status, data)
        if failure:
            if failure == "login":
                login_response = True
            elif failure == "rate_limited":
                blocked.append(429)
                sample["retry_delay_seconds"] = register_rate_limit(response.headers.get("retry-after", ""))
            else:
                blocked.append(response.status)
            return
        try:
            if not isinstance(data, dict):
                raise ValueError("not an object")
            envelope = data
            data = data.get("data") if isinstance(data.get("data"), dict) else data
            message = " ".join(str(source.get(key) or "") for source in (envelope, data)
                               for key in ("error_msg", "error_message", "message"))
            if re.search(r"验证|captcha|verification", message, re.I):
                challenge_response = True
            if str(data.get("total", "")).isdigit():
                total = int(data["total"])
            # total 仅是参考值，不作为抓取上限。
            if data.get("has_more") is False or data.get("hasMore") is False:
                exhausted = True
            rows = goods_from_api(data, keyword=keyword)
            sample["parsed_items"] = len(rows)
            # 包装响应由网站解码；仍须看到新商品 ID 才算恢复，不能只凭 HTTP 200。
            has_result_payload = (isinstance(data.get("items"), list)
                                  or bool(envelope.get("rc-res-data")))
            if (has_result_payload and not challenge_response
                    and envelope.get("success") is not False and data.get("success") is not False
                    and 429 not in blocked):
                successful_response = True
                successful_scanned = len(seen)
            if not rows and "items" not in data:
                stats["unreadable_responses"] += 1
            absorb_rows(rows)
        except Exception:
            # 响应体可能已失效或并非 JSON；继续从浏览器已渲染的卡片读取。
            stats["unreadable_responses"] += 1

    phase = "open_search"
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
            phase = "browser_interaction"
            service_browser()
            if should_stop and should_stop():
                finish("stopped", "已停止抓取")
                break
            # 先识别登录跳转，不能让旧页面的 DOM 读取异常覆盖真正的登录失效。
            phase = "page_state"
            state = page_state(page)
            if state == "login" or login_response:
                finish("login", "登录已失效，请在主界面重新获取登录信息后重试")
                break
            if state == "loading" and not blocked and time.monotonic() - last_new < STALL_SECONDS:
                wait_with_cancel(page, 200, should_stop)
                continue
            if capture_view and capture_view.opened.is_set():
                phase = "read_manual_page"
                absorb_rows(read_dom_goods(page, keyword=keyword))
                flush()
                # 用户接管时暂停自动滚动，避免验证控件或阅读位置移动。
                # 验证等待循环自身也会服务页面，不需要另一个浏览器或线程。
                if resume_requested():
                    capture_view.close()
                last_new = time.monotonic()
                wait_with_cancel(page, 200, should_stop)
                continue
            phase = "read_initial_goods"
            absorb_rows(read_live_goods(page, keyword=keyword))
            phase = "read_rendered_goods"
            absorb_rows(read_dom_goods(page, keyword=keyword))
            flush()
            report()
            if target.reached:
                finish("limit", f"已达到累计目标 {target.limit} 条")
                break
            phase = "page_state"
            state = page_state(page)
            if state in ("login", "verification") or challenge_response:
                flush()
                if on_intervention and state != "login":
                    if intervene(state if state in ("login", "verification") else "verification",
                                 require_confirmation=challenge_response and state not in ("login", "verification")):
                        continue
                    break
            if state == "login" or login_response:
                finish("login", "登录已失效，请在主界面重新获取登录信息后重试")
                break
            if state == "verification" or challenge_response:
                finish("verification", "网站要求验证，请在浏览器完成验证后重试")
                break
            if state not in ("busy", "limited"):
                limited_page_acknowledged = False
            elif not limited_page_acknowledged:
                # 网页提示也可能先于接口事件出现；同一提示只计一次失败。
                limited_page_acknowledged = True
                if 429 not in blocked:
                    register_rate_limit()
                    blocked.append(429)
            if blocked:
                events = list(blocked)
                blocked.clear()
                if any(code in (401, 403) for code in events):
                    finish("blocked", "网站拒绝了后续访问，请检查登录状态或稍后再试")
                    break
                if 429 in events:
                    # 手机手动浏览也会限速，首次拒绝即暂停，避免连续自动探测。
                    if pause_rate_limit("网站限制访问，已保留结果，请稍后再试"):
                        continue
                    break
            remaining = cooldown_until - time.monotonic()
            if remaining > 0:
                report(f"网站暂时限速，等待 {remaining:.0f} 秒后继续；可随时停止")
                last_new = time.monotonic()
                # 短轮询让新到达的 429 能延长等待，且每轮重新检查取消、验证和用户接管。
                wait_with_cancel(page, min(200, remaining * 1000), should_stop)
                continue
            if exhausted or state == "end":
                finish("end", "当前搜索页已没有更多结果")
                break
            if recovery_pending and not pending_requests and pacer.ready():
                recovery_pending = False
                last_new = time.monotonic()
                if not retry_page(rate_limited=True):
                    # 没有已知错误卡片时，只离底一次，再由正常滚动触发页面自身加载。
                    if not blocked and not pending_requests:
                        stats["last_scroll"] = scroll_results(page, retry_bottom=True)
                        stats["scrolls"] += 1
                wait_with_cancel(page, 200, should_stop)
                continue
            if time.monotonic() - last_new >= STALL_SECONDS:
                if stats["consecutive_rate_limits"]:
                    # 恢复请求未带来新商品时保留现场，不再进入普通网络自动重试。
                    if pause_rate_limit("重试后仍未加载新商品，已保留结果，请稍后再试"):
                        continue
                    break
                if stats["page_retries"] < len(PAGE_RETRY_DELAYS) and loading_error_retry_button(page) is not None:
                    delay = PAGE_RETRY_DELAYS[stats["page_retries"]]
                    report(f"页面提示网络异常，等待 {delay} 秒后重试；可随时停止")
                    if not wait_with_cancel(page, delay * 1000, should_stop, service_browser):
                        continue
                    # 普通网络重试的等待中也可能收到限速或验证，先交回主循环处理。
                    if (blocked or challenge_response or pending_requests
                            or time.monotonic() < cooldown_until
                            or not pacer.ready()
                            or page_state(page) in ("login", "verification", "busy", "limited", "end")):
                        continue
                    retry_page()
                    last_new = time.monotonic()
                    continue
                finish("stalled", "页面持续未加载新商品，已保留结果；可稍后重试。这不代表全部抓取完成")
                break
            # 请求未结束时只读取和等待；间隔从请求开始计，避免响应后再重复等待。
            if pending_requests or not pacer.ready():
                wait_with_cancel(page, 200, should_stop)
                continue
            phase = "scroll_results"
            stats["last_scroll"] = scroll_results(page)
            stats["scrolls"] += 1
            position = stats["last_scroll"] or {}
            wait_with_cancel(page, 500 if position.get("at_bottom") else position.get("settle_ms", 200), should_stop)
            absorb_rows(read_dom_goods(page, keyword=keyword))
            flush()
    except Exception as exc:
        finish("error", "页面读取中断，已保留此前结果，请重试")
        stats["error_type"] = type(exc).__name__
        stats["error_stage"] = phase
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("request", request_started)
        page.remove_listener("requestfinished", request_finished)
        page.remove_listener("requestfailed", request_finished)
        flush()
        stats["api_total"] = total
    return list(accepted.values()), total
