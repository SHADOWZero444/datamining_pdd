"""限速后等待用户继续；保留当前浏览器、分页和已读取商品。"""
import math
import time


def wait_for_manual_resume(page, resume_request, cooldown_deadline, notify, should_stop=None):
    resume_request.clear()
    previous = None
    while True:
        if should_stop and should_stop():
            return False
        if page.is_closed():
            raise RuntimeError("抓取页面已关闭，已保存的数据仍然保留。")
        remaining = max(0, math.ceil(cooldown_deadline() - time.monotonic()))
        if remaining != previous:
            notify(remaining)
            previous = remaining
        if resume_request.is_set():
            resume_request.clear()
            # 等待中收到新的 Retry-After 时，不允许旧点击提前恢复。
            if remaining == 0:
                return True
        page.wait_for_timeout(200)
