"""抓取入口；搜索词属于本次调用，不使用跨任务全局变量。"""
from pdd.paths import program_dir
from pdd.browser.session import capture_session
from .login import find_cookie_file
from .collector import collect
from .target import CollectionTarget

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
    if target.reached and not (cancel_event and cancel_event.is_set()):
        message = f"已达到累计目标 {limit} 条，无需重复抓取"
        diagnostics.update(stop_reason="limit", message=message, scanned=0, matched=0)
        if progress:
            progress(message, target.count, None)
        return [], None, []
    goods = []
    total = None
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
            goods, total = collect(
                page,
                keyword=keyword,
                limit=limit,
                target=target,
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
            record_environment()
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
