"""抓取入口；搜索词属于本次调用，不使用跨任务全局变量。"""
from pdd.paths import program_dir
from pdd.browser.session import capture_session
from .login import find_cookie_file
from .collector import collect

def run_search(keyword, limit=None, filters=None, cancel_event=None, progress=None,
               on_batch=None, diagnostics=None, on_intervention=None, browser_request=None,
               resume_request=None, on_browser_ready=None, capture_view=None):
    """给界面调用。keyword 是搜索词，limit 是这次最多收多少条。"""
    keyword = (keyword or "").strip()
    if not keyword:
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
                keyword=keyword,
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
