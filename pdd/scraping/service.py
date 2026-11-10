"""抓取入口；搜索词属于本次调用，不使用跨任务全局变量。"""
from pdd.paths import program_dir
from playwright.sync_api import Error as BrowserError
from pdd.browser.session import capture_session
from .login import find_cookie_file
from .collector import collect
from .target import CollectionTarget
from .pacing import PagePacer, remaining_cooldown
from .detail_fields import complete
from .details import enrich
from .filters import apply_filters

def run_search(keyword, limit=None, filters=None, cancel_event=None, progress=None,
               on_batch=None, diagnostics=None, on_intervention=None, browser_request=None,
               resume_request=None, on_browser_ready=None, capture_view=None, existing_goods=None, refresh=False):
    """给界面调用。keyword 是搜索词；limit 是历史符合条件商品与本次商品的累计目标。"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise RuntimeError("搜索词不能为空")
    errors = []
    diagnostics = diagnostics if diagnostics is not None else {}
    target = CollectionTarget(limit, existing_goods, filters, refresh=refresh)
    diagnostics.update(target.snapshot())
    history = {str(row["goods_id"]): row for row in apply_filters(existing_goods or [], filters) if row.get("goods_id")}
    backlog = [row for row in list(history.values())[:limit] if not complete(row.get("detail") or {})]
    backfill_only = target.reached
    wanted = {str(row["goods_id"]) for row in backlog}
    if target.reached and not backlog and not (cancel_event and cancel_event.is_set()):
        message = f"已达到累计目标 {limit} 条，无需重复抓取"
        diagnostics.update(stop_reason="limit", message=message, scanned=0, matched=0)
        if progress:
            progress(message, target.count, None)
        return [], None, []
    goods = []
    total = None
    remaining = remaining_cooldown()
    if remaining:
        message = f"网站仍限制访问，请至少等待 {remaining} 秒后再开始；已有数据保留"
        diagnostics.update(stop_reason="rate_limited", message=message)
        return [], None, [message]
    stop = cancel_event.is_set if cancel_event else lambda: False
    pacer = PagePacer()
    try:
        with capture_session(program_dir(), find_cookie_file(),
                             cancel_event.is_set if cancel_event else None) as page:
            if on_browser_ready:
                on_browser_ready(True)
            diagnostics["browser_mode"] = "android-touch-v6"
            def record_environment():
                try:
                    diagnostics["browser_environment"] = page.evaluate("""() => ({
                        user_agent: navigator.userAgent, mobile: navigator.userAgentData?.mobile,
                        touch_points: navigator.maxTouchPoints, width: innerWidth, height: innerHeight,
                        screen_width: screen.width, screen_height: screen.height,
                        pixel_ratio: devicePixelRatio, outer_width: outerWidth, outer_height: outerHeight
                    })""")
                except Exception:
                    pass
            record_environment()
            attempted = set()
            def process(row, safe):
                key = str(row["goods_id"])
                if key in attempted or (backfill_only and key not in wanted):
                    return False
                previous = history.get(key, {}).get("detail") or {}
                if previous and not row.get("detail"):
                    row.update(detail=previous, shop_name=previous.get("shop_name", ""))
                if not refresh and complete(row.get("detail") or {}):
                    attempted.add(key)
                    return False
                outcome = enrich(page, [row], pacer, stop=stop, save=on_batch, stats=diagnostics,
                    report=lambda msg: progress and progress(msg, target.count, total),
                    intervention=on_intervention, resume=resume_request, view=capture_view, allowed=safe)
                if outcome != "deferred":
                    attempted.add(key)
                return True

            def step(rows, safe, closing):
                if diagnostics.get("detail_skip_reason"):
                    return False
                if any(process(row, safe) for row in list(rows.values())):
                    return True
                if closing and not refresh:
                    for old in backlog:
                        key = str(old["goods_id"])
                        if key not in rows and key not in attempted:
                            rows[key] = dict(old)
                            return process(rows[key], safe)
                return False

            goods, total = collect(page, keyword=keyword, limit=limit, target=target, pacer=pacer,
                should_stop=stop, progress=progress, filters=filters, on_batch=on_batch,
                diagnostics=diagnostics, on_intervention=on_intervention, browser_request=browser_request,
                resume_request=resume_request, capture_view=capture_view, detail_step=step)
            if not stop() and diagnostics.get("detail_access_blocked"):
                errors.append("搜索访问尚未恢复，不再发起详情请求；已读取的数据已保留")
            missing = diagnostics.get("detail_total", 0) - diagnostics.get("detail_complete", 0)
            if missing and not stop():
                reason = {"login": "登录已失效", "blocked": "网站拒绝访问", "verification": "网站需要人工验证",
                          "rate_limited": "网站限制详情访问", "unavailable": "详情暂不可读，本轮已优先保留搜索结果"}.get(
                              diagnostics.get("detail_stop_reason") or diagnostics.get("detail_skip_reason"), "详情字段未读全")
                errors.append(f"{reason}，{missing} 条已尝试的详情未完整核对；已读取的数据已保留")
    except RuntimeError as exc:
        errors.append(str(exc))
    except BrowserError:
        errors.append("浏览器页面已中断，已保存的数据会保留；可重新开始以补齐详情")
    finally:
        if capture_view:
            capture_view.close()
        if on_browser_ready:
            on_browser_ready(False)
    if diagnostics.get("stop_reason") not in (None, "end", "limit", "stopped"):
        errors.insert(0, diagnostics["message"])
    if not goods and diagnostics.get("scanned") and not errors:
        errors.append(f"已读取 {diagnostics['scanned']} 条商品，但没有符合筛选条件的结果")
    return goods, total, errors
