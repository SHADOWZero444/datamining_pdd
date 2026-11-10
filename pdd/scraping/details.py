"""在搜索分页间隙，同一登录会话串行读取商品详情；不重放 HAR。"""
import math
import time
from contextlib import suppress
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError
from pdd.browser.mobile import configure_mobile_page
from pdd.browser.verification import handle_verification, VerificationTarget
from .detail_navigation import goods_url, open_product, product_tab
from .detail_fields import DETAIL_FIELDS, complete
from .page import page_state, retry_delay, wait_with_cancel
from .pacing import remember_cooldown
from .responses import failure_kind, verification_hint


def read_one(anchor, row, stop, intervention, resume, view, stats, allowed):
    """一次导航；返回字段、阻断原因、Retry-After 等待秒数。"""
    tab = product_tab(anchor)
    key = str(row["goods_id"])
    reason, wait_seconds, challenge_link = "", 0, None
    pending = set()

    def started(request):
        u = urlsplit(request.url)
        if u.netloc.lower() == "mobile.pinduoduo.com" and u.path.startswith("/proxy/api/api/oak/"):
            pending.add(request)

    def finished(request):
        pending.discard(request)

    def observe(response):
        nonlocal reason, wait_seconds, challenge_link
        u = urlsplit(response.url)
        if u.netloc.lower() != "mobile.pinduoduo.com":
            return
        api = u.path.startswith("/proxy/api/api/oak/")
        if not api and not (response.request.is_navigation_request() and response.frame == tab.main_frame):
            return
        statuses = stats.setdefault("detail_api_status" if api else "detail_document_status", {})
        status_key = str(response.status)
        statuses[status_key] = statuses.get(status_key, 0) + 1
        data = None
        if api and response.status != 429:
            try:
                data = response.json()
            except (BrowserError, ValueError):
                pass
        failure = failure_kind(response.status, data)
        if failure == "rate_limited" or (response.status == 503 and response.headers.get("retry-after")):
            reason = "rate_limited"
            wait_seconds = max(wait_seconds, retry_delay(response.headers.get("retry-after"), 0))
        elif reason != "rate_limited":
            if failure == "login":
                reason = "login"
            elif reason != "login":
                required, link = verification_hint(data)
                if required:
                    reason, challenge_link = "verification", link
                elif response.status == 403:
                    reason = "blocked"
                elif response.status >= 400 and not api:
                    reason = "unavailable"

    try:
        tab.on("response", observe)
        tab.on("request", started)
        tab.on("requestfinished", finished)
        tab.on("requestfailed", finished)
        configure_mobile_page(tab, edge=getattr(anchor, "_pdd_edge", True))
        navigation = open_product(anchor, tab, row, stop, allowed, stats)
        if navigation == "deferred":
            return {}, navigation, 0
        deadline = time.monotonic() + 10
        found = {}
        last_change = last_scroll = time.monotonic()
        handled = False
        while not stop():
            state = page_state(tab)
            if reason == "rate_limited" or state in ("busy", "limited"):
                return {}, "rate_limited", wait_seconds
            if reason == "login" or state == "login":
                return {}, "login", 0
            if state == "verification" or reason == "verification":
                if handled or not (intervention and resume and view):
                    return {}, "verification", 0
                handled = True
                target = VerificationTarget(tab, challenge_link)
                def service():
                    if view.opened.is_set():
                        active = target.page_for_input(page_state)
                        if target.failed:
                            view.unavailable("详情验证链接未能打开，可停止任务，已保存的数据会保留。")
                        else:
                            view.service(active, stop)
                def confirmed():
                    if resume.is_set():
                        resume.clear()
                        return True
                    return False
                resume.clear()
                try:
                    result = handle_verification(tab, "verification", lambda: target.state(page_state),
                        lambda ms: reason != "rate_limited" and wait_with_cancel(tab, ms, stop), intervention, stop, service,
                        ready=lambda: urlsplit(tab.url).path == "/goods.html",
                        resume_requested=confirmed, require_confirmation=state != "verification")
                finally:
                    target.close()
                    view.close()
                if result != "resumed":
                    if reason == "rate_limited":
                        return {}, reason, wait_seconds
                    return {}, "stopped" if stop() else "verification", 0
                if reason == "verification":
                    reason = ""
                deadline = time.monotonic() + 10
                continue
            if reason:
                return {}, reason, wait_seconds
            fields = tab.evaluate(DETAIL_FIELDS, key) or {}
            settled = fields.pop("_settled", False)
            if fields:
                images = list(dict.fromkeys([*found.get("images", []), *fields["images"]]))
                merged = {**found, **{k: v for k, v in fields.items() if v}, "images": images}
                if merged != found:
                    last_change = time.monotonic()
                    found = merged
            if not reason and not pending and settled and complete(found) and time.monotonic() - last_change >= 1:
                return found, "", 0
            if time.monotonic() >= deadline:
                if not reason and not any(found.values()):
                    issue = "fields_missing" if goods_url(tab.url, key) else "unexpected_page"
                    misses = stats.setdefault("detail_unreadable", {})
                    misses[issue] = misses.get(issue, 0) + 1
                return found if fields and not reason else {}, reason, wait_seconds
            # 部分详情区滚到视口才渲染；不用打开图片预览或加载推荐商品。
            if fields and not reason and not pending and not settled and time.monotonic() - last_scroll >= 2:
                tab.evaluate("window.scrollBy(0, innerHeight * 0.8)")
                last_scroll = time.monotonic()
            wait_with_cancel(tab, 400, stop)
        return {}, "stopped", 0
    except BrowserError:
        if not reason and not stop():
            misses = stats.setdefault("detail_unreadable", {})
            misses["browser_error"] = misses.get("browser_error", 0) + 1
        return {}, reason or ("stopped" if stop() else "unavailable"), wait_seconds
    finally:
        with suppress(BrowserError):
            tab.remove_listener("response", observe)
            tab.remove_listener("request", started)
            tab.remove_listener("requestfinished", finished)
            tab.remove_listener("requestfailed", finished)
            if not tab.is_closed():
                tab.close()


def enrich(anchor, rows, pacer, *, stop, save, report, stats, intervention, resume, view, allowed=lambda: True):
    """就地更新成功的行，每件立刻保存；受限时只重试同一商品，不跳过冷却。"""
    for name in ("detail_total", "detail_attempted", "detail_complete", "detail_rate_limits"):
        stats.setdefault(name, 0)
    if not rows:
        return
    stats["detail_total"] += len(rows)
    for row in rows:
        if stop():
            return
        key = str(row["goods_id"])
        stats["detail_attempted"] += 1
        report(f"正在读取第 {stats['detail_attempted']} 件商品详情，已完整取得 {stats['detail_complete']} 件")
        if not key.isascii() or not key.isdigit():
            continue
        for attempt in range(3):
            while not pacer.ready() and not stop():
                wait_with_cancel(anchor, 200, stop)
            if stop():
                return
            if not allowed():
                stats["detail_total"] -= 1
                stats["detail_attempted"] -= 1
                return "deferred"
            started = time.monotonic()
            pacer.started(started)
            try:
                fields, reason, delay = read_one(anchor, row, stop, intervention, resume, view, stats, allowed)
            except BrowserError:
                stats["detail_skip_reason"] = "unavailable"
                return
            pacer.finished(started, time.monotonic())
            if reason == "deferred":
                stats["detail_total"] -= 1
                stats["detail_attempted"] -= 1
                return reason
            if reason == "rate_limited":
                stats["detail_rate_limits"] += 1
                pacer.slow_down()
                delay = max(delay, 60 * 2**attempt)
                remember_cooldown(delay)
                stats["detail_wait_seconds"] = math.ceil(delay)
                if attempt < 2 and delay <= 300:
                    deadline = time.monotonic() + delay
                    while not stop() and (remaining := deadline - time.monotonic()) > 0:
                        report(f"详情页限制访问，至少等待 {math.ceil(remaining)} 秒；已保存的商品不会丢失")
                        wait_with_cancel(anchor, min(1000, remaining * 1000), stop)
                    continue
            if reason in ("login", "blocked", "verification", "rate_limited", "stopped"):
                stats["detail_stop_reason"] = reason
                return
            if any(fields.values()):
                stats["detail_empty_streak"] = 0
                previous = row.get("detail") or {}
                detail = {**previous, **{k: v for k, v in fields.items() if v}}
                if fields.get("images") and not complete(fields):
                    detail["images"] = list(dict.fromkeys([*(previous.get("images") or []), *fields["images"]]))
                row.update(detail=detail, shop_name=detail.get("shop_name", ""))
                if save:
                    save([row])
                if complete(fields):
                    stats["detail_complete"] += 1
            else:
                stats["detail_empty_streak"] = stats.get("detail_empty_streak", 0) + 1
                if stats["detail_empty_streak"] >= 3:
                    # 普通空页不等于网站限速；本次停止请求详情，搜索仍从原位置继续。
                    stats["detail_skip_reason"] = "unavailable"
                    return
            break
