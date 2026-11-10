"""单个后台抓取任务的状态、取消信号与结果持久化。"""
import json
import threading
import time
from pdd.browser.view import CaptureView
from pdd.storage import results, library
from pdd.scraping.filters import read_filters
from . import auth as auth_service
from uuid import uuid4

# job 是当前这一次抓取。界面轮询它，不在请求里直接开浏览器。
job = {
    "running": False,
    "keyword": "",
    "status": "idle",
    "message": "",
    "count": 0,
    "total": None,
}
job_lock = threading.RLock()
job_cancel = None
job_browser_request = None
job_resume_request = None
job_capture_view = None

def run_job(keyword, limit, filters, cancel_event, refresh=False):
    started_at = time.monotonic()
    diagnostics = {}
    capture_view = job_capture_view
    try:
        from pdd.scraping.service import run_search

        existing_goods = results.read_jsonl(results.result_path(keyword))
        existing_ids = {str(row["goods_id"]) for row in existing_goods if row.get("goods_id")}
        baseline = {str(row["goods_id"]): results.product_values(row) for row in existing_goods
                    if row.get("goods_id")} if refresh else None
        batch_saved = False
        with job_lock:
            job["saved"] = len(existing_goods)

        def report_progress(message, count, total=None):
            with job_lock:
                if job.get("running") and not cancel_event.is_set():
                    job.update({"message": message, "count": count, "total": total,
                                "scanned": diagnostics.get("scanned", 0)})

        def save_batch(rows):
            nonlocal batch_saved
            saved = results.merge_goods(keyword, rows, baseline=baseline,
                                        reset_updates=refresh and not batch_saved)
            batch_saved = True
            with job_lock:
                job["saved"] = len(saved)
            return saved

        def report_intervention(status, message, kind):
            with job_lock:
                if not cancel_event.is_set():
                    job.update(status=status, message=message, intervention_kind=kind)

        def browser_ready(ready):
            with job_lock:
                job["browser_available"] = ready

        goods, total, errors = run_search(
            keyword,
            limit=limit,
            filters=filters,
            existing_goods=existing_goods,
            refresh=refresh,
            cancel_event=cancel_event,
            progress=report_progress,
            on_batch=save_batch,
            diagnostics=diagnostics,
            on_intervention=report_intervention,
            browser_request=job_browser_request,
            resume_request=job_resume_request,
            on_browser_ready=browser_ready,
            capture_view=capture_view,
        )
        stopped = cancel_event.is_set()
        if goods or (refresh and not errors and not stopped):
            saved = save_batch(goods)
        else:
            saved = results.read_jsonl(results.result_path(keyword))
        new_count = len({str(row["goods_id"]) for row in saved if row.get("goods_id")} - existing_ids)
        diagnostics["saved_new_goods"] = new_count
        if refresh:
            updated_count = sum(row.get("updated") is True for row in saved) if batch_saved else 0
            diagnostics["updated_goods"] = updated_count
            if stopped:
                status, reason = "stopped", "已停止更新"
            elif errors:
                status, reason = ("partial" if saved else "failed"), errors[-1]
            else:
                status, reason = "ok", "更新完成"
            if batch_saved:
                message = f"{reason}。本轮更新 {updated_count} 条，新增 {new_count} 条，共保存 {len(saved)} 条"
            else:
                message = f"{reason}。本轮未写入结果，保留原有数据和更新标记。"
        elif stopped:
            message = f"已停止抓取，已保留 {len(saved)} 条商品"
            status = "stopped"
        elif diagnostics.get("stop_reason") == "limit" and not errors:
            message = f"已达到累计目标 {limit} 条，本次新增 {new_count} 条，库里共 {len(saved)} 条"
            status = "ok"
        elif goods:
            reason = errors[-1] if errors else diagnostics.get("message", "本次抓取结束")
            message = f"{reason}。本次新增 {new_count} 条，库里共 {len(saved)} 条"
            status = "ok" if not errors else "partial"
        elif saved:
            message = (errors[-1] + "。原来的数据还在。") if errors else "这次没有新商品，原来的数据还在。"
            status = "partial"
        else:
            message = errors[-1] if errors else "没有抓到商品"
            status = "failed"
        if not refresh and limit is not None and not stopped and diagnostics.get("cumulative_matched", 0) < limit:
            if saved:
                status = "partial"
            message += f"。累计符合条件 {diagnostics.get('cumulative_matched', 0)}/{limit} 条，目标尚未完成"
        if diagnostics.get("detail_total"):
            message += f"。本轮详情完整读取 {diagnostics.get('detail_complete', 0)}/{diagnostics['detail_total']} 条"
        with job_lock:
            job.update({
                "status": status,
                "message": message,
                "count": len(saved),
                "total": total,
                "stop_reason": diagnostics.get("stop_reason"),
                "browser_available": False,
                "intervention_kind": None,
            })
    except Exception as exc:
        try:
            saved = results.read_jsonl(results.result_path(keyword))
            saved_count = len(saved)
        except OSError:
            saved_count = job.get("saved", 0)
        with job_lock:
            job.update({
                "status": "partial" if saved_count else "failed",
                "message": f"抓取中断，已保存的数据仍然保留。{str(exc)}",
                "count": saved_count,
                "browser_available": False,
            })
        diagnostics.update(stop_reason="error", error_type=type(exc).__name__)
    finally:
        if capture_view:
            capture_view.close()
        diagnostics.update(keyword=keyword, limit=limit, finished_at=int(time.time()),
                           elapsed_seconds=round(time.monotonic() - started_at, 2))
        try:
            results.atomic_write(results.result_path(keyword).with_suffix(".run.json"),
                         json.dumps(diagnostics, ensure_ascii=False, indent=2))
        except OSError:
            pass
        finally:
            with job_lock:
                job["running"] = False


def start_job(keyword, limit, filters, *, refresh=False):
    """初始化失败不占用任务槽；持锁完成状态发布与线程启动。"""
    global job_cancel, job_browser_request, job_resume_request, job_capture_view
    with job_lock, auth_service.auth_lock:
        if auth_service.auth["running"]:
            raise RuntimeError("请先完成当前登录，再开始抓取。")
        if job["running"]:
            raise RuntimeError("已有抓取在进行，请等它结束。")
        if refresh:
            if not (results.result_path(keyword).exists() or results.metadata_path(keyword).exists()):
                raise RuntimeError("这个词条已不存在，请重新搜索。")
            metadata = results.search_metadata(keyword, recover_legacy_limit=True)
            limit = metadata["limit"]
            filters = read_filters(metadata["filters"])
        results.save_search_metadata(keyword, filters, limit)
        library.ensure_library_entry(keyword)
        job_cancel = threading.Event()
        job_browser_request = threading.Event()
        job_resume_request = threading.Event()
        job_capture_view = CaptureView()
        job.update(running=True, refresh=refresh, keyword=keyword, status="running", message="正在打开搜索页",
                   count=0, total=None, scanned=0, saved=0, stop_reason=None,
                   id=uuid4().hex, browser_available=False, intervention_kind=None)
        try:
            threading.Thread(target=run_job, args=(keyword, limit, filters, job_cancel, refresh), daemon=False).start()
        except Exception:
            job.update(running=False, status="failed", message="无法启动抓取，请重试。")
            raise
        return dict(job)
