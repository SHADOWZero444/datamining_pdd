"""将后台页面显示在软件内；HTTP 线程只排队，页面操作仍在抓取线程执行。"""
import base64
import math
import queue
import threading
import time


class CaptureView:
    def __init__(self):
        self.opened = threading.Event()
        self._lock = threading.Lock()
        self._commands = queue.Queue(maxsize=32)
        self._snapshot = {"image": None, "error": ""}
        self._last_frame = 0
        self._generation = 0

    def open(self):
        with self._lock:
            self.opened.set()

    def close(self):
        with self._lock:
            self.opened.clear()
            self._generation += 1
            self._snapshot = {"image": None, "error": ""}
            while not self._commands.empty():
                try:
                    self._commands.get_nowait()
                except queue.Empty:
                    break

    def snapshot(self):
        with self._lock:
            return {**self._snapshot, "opened": self.opened.is_set()}

    def submit(self, payload):
        """仅接受用户指针、文字及导航按键，不接收脚本、URL 或 CDP 命令。"""
        action = payload.get("action")
        command = {"action": action}
        if action == "pointer":
            points = payload.get("points")
            if not isinstance(points, list) or not 1 <= len(points) <= 128:
                raise ValueError("操作轨迹无效，请重试。")
            clean = []
            for point in points:
                if not isinstance(point, list) or len(point) != 3:
                    raise ValueError("操作坐标无效。")
                x, y, delay = point
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
                    raise ValueError("操作坐标无效。")
                if not (0 <= x <= 1 and 0 <= y <= 1 and 0 <= delay <= 100):
                    raise ValueError("操作坐标无效。")
                clean.append((x, y, delay))
            command["points"] = clean
        elif action == "key":
            if not isinstance(payload.get("key"), str) or payload["key"] not in {"Tab", "Shift+Tab", "Enter", "Backspace", "Delete",
                                          "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                                          "Home", "End", "Escape", "Control+A"}:
                raise ValueError("不支持这个按键。")
            command["key"] = payload["key"]
        elif action == "text":
            value = payload.get("text")
            if not isinstance(value, str) or not 1 <= len(value) <= 500:
                raise ValueError("每次请输入 1 到 500 个字符。")
            command["text"] = value
        elif action == "wheel":
            value = payload.get("delta")
            if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > 1000:
                raise ValueError("滚动距离无效。")
            command["delta"] = value
        else:
            raise ValueError("不支持这个操作。")
        with self._lock:
            if not self.opened.is_set():
                raise ValueError("请先打开验证页面。")
            try:
                self._commands.put_nowait(command)
            except queue.Full:
                raise ValueError("页面正在处理操作，请稍等再试。") from None

    def service(self, page, should_stop=None):
        if not self.opened.is_set() or (should_stop and should_stop()):
            return
        with self._lock:
            generation = self._generation
        try:
            # 每轮只执行一条，避免连续输入阻塞停止和验证状态检查。
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                command = None
            if command:
                action = command["action"]
                if action == "pointer":
                    size = page.viewport_size or page.evaluate("({width:innerWidth,height:innerHeight})")
                    points = command["points"]
                    def move(point):
                        page.mouse.move(point[0] * (size["width"] - 1), point[1] * (size["height"] - 1))
                    move(points[0])
                    page.mouse.down()
                    try:
                        for point in points[1:]:
                            if not self.opened.is_set() or (should_stop and should_stop()):
                                break
                            page.wait_for_timeout(min(point[2], 30))
                            move(point)
                    finally:
                        page.mouse.up()
                elif action == "key":
                    page.keyboard.press(command["key"])
                elif action == "text":
                    page.keyboard.insert_text(command["text"])
                elif action == "wheel":
                    page.mouse.wheel(0, command["delta"])
            if command or time.monotonic() - self._last_frame >= .5:
                frame = base64.b64encode(page.screenshot(type="jpeg", quality=75, timeout=1500)).decode("ascii")
                with self._lock:
                    if generation == self._generation and self.opened.is_set():
                        self._snapshot = {"image": frame, "error": ""}
                self._last_frame = time.monotonic()
        except Exception:
            with self._lock:
                if generation == self._generation and self.opened.is_set():
                    self._snapshot["error"] = "页面正在切换，请稍等；若长时间未恢复，可关闭后重新打开。"
