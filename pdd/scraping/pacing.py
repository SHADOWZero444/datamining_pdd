"""串行翻页节奏：按请求起点计时，网络响应和渲染期间不发起滚动。"""
import time
import math
from collections import deque

# 桌面端一次只运行一个任务。同进程切词或重新开始也不能提前结束服务器冷却。
_site_deadline = 0.0


def remember_cooldown(seconds):
    global _site_deadline
    _site_deadline = max(_site_deadline, time.monotonic() + seconds)


def remaining_cooldown():
    return max(0, math.ceil(_site_deadline - time.monotonic()))


class PagePacer:
    MIN_INTERVAL = 2.5
    MAX_INTERVAL = 30.0
    RENDER_SETTLE = 0.4
    # 本地滚动窗口预算，不假设这是站点公布的配额。
    WINDOW_SECONDS = 60
    WINDOW_REQUESTS = 6

    def __init__(self):
        self.last_started = self.last_finished = time.monotonic()
        self.latency = 0.0
        self.floor = self.MIN_INTERVAL
        self.starts = deque(maxlen=self.WINDOW_REQUESTS)
        self.recovery_floor = self.MIN_INTERVAL

    @property
    def interval(self):
        return min(self.MAX_INTERVAL, max(self.floor, self.latency * 1.5))

    def started(self, now):
        self.last_started = now
        self.starts.append(now)

    def finished(self, started, now):
        self.last_finished = now
        elapsed = max(0.0, now - started)
        self.latency = elapsed if not self.latency else self.latency * 0.75 + elapsed * 0.25

    def ready(self):
        window_deadline = (self.starts[0] + self.WINDOW_SECONDS
                           if len(self.starts) == self.WINDOW_REQUESTS else 0)
        return time.monotonic() >= max(self.last_started + self.interval,
                                       self.last_finished + self.RENDER_SETTLE, window_deadline)

    def slow_down(self):
        # 限速后不立即退回到曾触发限速的速度，维持本次会话的保守下限。
        average_spacing = self.WINDOW_SECONDS / self.WINDOW_REQUESTS
        self.recovery_floor = min(self.MAX_INTERVAL, max(average_spacing, self.recovery_floor * 1.5))
        self.floor = min(self.MAX_INTERVAL, max(self.recovery_floor, self.interval * 2))

    def new_page(self):
        # 只有收到新的商品后才逐步回调节奏，单独的 HTTP 200 不足以提速。
        self.floor = max(self.recovery_floor, self.floor * 0.9)
