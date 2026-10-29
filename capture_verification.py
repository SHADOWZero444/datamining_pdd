"""验证检测与同页接管。无法确认通过时，不把按钮点击当作验证成功。"""
import time

AUTO_WAIT_SECONDS = 8
MANUAL_WAIT_SECONDS = 600


def verification_kind(page):
    for frame in page.frames:
        if "challenges.cloudflare.com" in frame.url:
            return "cloudflare"
    return "site"


def try_automatic_verification(page, check, wait, should_stop=None, service=None, ready=lambda: True):
    """先让页面自带检测运行；已识别的 Cloudflare 复选框只尝试一次。"""
    provider = verification_kind(page)
    if provider == "cloudflare":
        for frame in page.frames:
            if "challenges.cloudflare.com" not in frame.url:
                continue
            try:
                checkbox = frame.get_by_role("checkbox")
                if checkbox.count() == 1 and checkbox.is_visible():
                    checkbox.check(timeout=1500)
                    break
            except Exception:
                pass
    deadline = time.monotonic() + AUTO_WAIT_SECONDS
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return False
        if service:
            service()
        if check() not in ("verification", "login") and ready():
            return True
        if not wait(200):
            return False
    return False


def handle_verification(page, kind, check, wait, notify=None, should_stop=None, service=None,
                        ready=lambda: True, resume_requested=None, require_confirmation=False):
    """验证暂停期间保留原页面。人工操作完成后自动恢复，不丢翻页上下文。"""
    def publish(status, message):
        if notify:
            notify(status, message, kind)

    if kind == "verification" and not require_confirmation:
        publish("recovering", "网站正在验证，正在尝试自动恢复…")
        if try_automatic_verification(page, check, wait, should_stop, service, ready):
            publish("running", "验证已通过，正在继续抓取")
            return "resumed"
    if should_stop and should_stop():
        return "stopped"
    message = "请点击“前往验证”，在软件内完成登录或验证；完成后会自动继续"
    if require_confirmation:
        message = "后续商品尚未加载，请点击“查看抓取页面”检查；处理后点击“继续抓取”"
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
        if current not in ("verification", "login") and ready() and confirmed:
            clear_since = clear_since or time.monotonic()
            # 验证弹窗短暂重绘不算通过；原页面需持续恢复。
            if time.monotonic() - clear_since >= 1:
                publish("running", "验证已通过，正在继续抓取")
                return "resumed"
        else:
            clear_since = None
        if not wait(200):
            return "stopped"
    return "timeout"
