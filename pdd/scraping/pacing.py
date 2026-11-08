"""串行翻页节奏：按请求起点计时，网络响应和渲染期间不发起滚动。"""
import time


class PagePacer:
    MIN_INTERVAL = 2.5
    MAX_INTERVAL = 30.0
    RENDER_SETTLE = 0.4

    def __init__(self):
        self.last_started = self.last_finished = time.monotonic()
        self.latency = 0.0
        self.floor = self.MIN_INTERVAL

    @property
    def interval(self):
        return min(self.MAX_INTERVAL, max(self.floor, self.latency * 1.5))

    def started(self, now):
        self.last_started = now

    def finished(self, started, now):
        self.last_finished = now
        elapsed = max(0.0, now - started)
        self.latency = elapsed if not self.latency else self.latency * 0.75 + elapsed * 0.25

    def ready(self):
        return time.monotonic() >= max(self.last_started + self.interval,
                                       self.last_finished + self.RENDER_SETTLE)

    def slow_down(self):
        self.floor = min(self.MAX_INTERVAL, self.interval * 2)

    def new_page(self):
        # 只有收到新的商品后才逐步回调节奏，单独的 HTTP 200 不足以提速。
        self.floor = max(self.MIN_INTERVAL, self.floor * 0.9)
