"""使用本程序专用浏览器会话获取登录信息。"""
import time
from pdd.paths import program_dir
from pdd.browser.session import capture_session
from .constants import BASE

def find_cookie_file():
    return program_dir() / "cookie.txt"

def cookie_header(items):
    return "; ".join(f"{item['name']}={item['value']}" for item in items if item.get("name") and item.get("value"))


def has_login_cookie(items):
    """只接受登录后才会出现的身份标记，避免把初始访客 cookie 当作成功。"""
    names = {str(item.get("name") or "").casefold() for item in items}
    return "pddaccesstoken" in names or ({"pdd_user_id", "pdd_user_uin"} <= names)


def open_login_and_capture_cookie(timeout_seconds=600):
    """登录使用抓取所用的持久会话，验证后的站点存储也会保留。"""
    with capture_session(program_dir(), find_cookie_file(), headless=False) as page:
        page.goto(BASE + "login.html", wait_until="domcontentloaded", timeout=30000)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            items = page.context.cookies([BASE])
            if has_login_cookie(items):
                return cookie_header(items)
            if page.is_closed():
                raise RuntimeError("登录窗口已关闭，尚未获取到登录信息。")
            page.wait_for_timeout(500)
        raise RuntimeError("等待登录超时，请重新点击“前往获取”。")
