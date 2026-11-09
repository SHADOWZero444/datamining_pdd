"""验证检测与同页接管。无法确认通过时，不把按钮点击当作验证成功。"""
import time
from urllib.parse import urlsplit
from .mobile import configure_mobile_page

MANUAL_WAIT_SECONDS = 600


class VerificationTarget:
    """显式验证链接在同一浏览器会话的新页中打开；搜索页及其分页不动。"""
    def __init__(self, search, url):
        self.search, self.url = search, url
        self.tab = None
        self.attempted = False
        self.failed = False
        self.inline_seen = False

    def page_for_input(self, state):
        if self.url and not self.attempted and not self.inline_seen and state(self.search) != "verification":
            self.attempted = True
            try:
                self.tab = self.search.context.new_page()
                configure_mobile_page(self.tab, edge=getattr(self.search, "_pdd_edge", True))
                response = self.tab.goto(self.url, wait_until="commit", timeout=8000)
                self.failed = response is None or response.status >= 400
            except Exception:
                self.failed = True
        if self.tab and not self.tab.is_closed():
            parts = urlsplit(self.tab.url)
            if parts.scheme != "https" or parts.netloc.lower() not in ("mobile.pinduoduo.com", "mobile.pinduoduo.com:443"):
                self.failed = True
            if not self.failed:
                return self.tab
        return self.search

    def state(self, read_state):
        current = read_state(self.search)
        if current == "verification":
            self.inline_seen = True
        if current or not self.url:
            return current
        if self.failed or (not self.attempted and not self.inline_seen):
            return "verification"
        if self.tab and not self.tab.is_closed():
            return read_state(self.tab)
        return ""

    def close(self):
        if self.tab and not self.tab.is_closed():
            try:
                self.tab.close()
            except Exception:
                pass  # 浏览器关闭时不覆盖任务的停止原因。


def handle_verification(page, kind, check, wait, notify=None, should_stop=None, service=None,
                        ready=lambda: True, resume_requested=None, require_confirmation=False):
    """验证暂停期间保留原页面。人工操作完成后自动恢复，不丢翻页上下文。"""
    def publish(status, message):
        if notify:
            notify(status, message, kind)

    if should_stop and should_stop():
        return "stopped"
    message = "网站要求人机验证。点击“人机验证”打开当前页面，按网站提示操作。"
    if require_confirmation:
        message = "网站要求人机验证。点击“人机验证”查看网站提示，处理后点击“继续抓取”。"
    publish("verification", message)
    deadline = time.monotonic() + MANUAL_WAIT_SECONDS
    clear_since = None
    confirmed = not require_confirmation
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return "stopped"
        if service:
            service()
        if resume_requested and resume_requested():
            # 点击继续只表达用户意图，仍须确认验证界面已消失。
            confirmed = True
        current = check()
        if current in ("", "end") and ready() and confirmed:
            if clear_since is None:
                clear_since = time.monotonic()
            # 验证弹窗短暂重绘不算通过；原页面需持续恢复。
            if time.monotonic() - clear_since >= 1:
                publish("running", "已返回搜索页，正在尝试继续抓取")
                return "resumed"
        else:
            clear_since = None
        if not wait(200):
            return "stopped"
    return "timeout"
