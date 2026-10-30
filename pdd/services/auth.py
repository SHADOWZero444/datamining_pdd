"""本机登录信息和登录流程。"""
import threading
from pdd.paths import program_dir
from pdd.storage.results import atomic_write

ROOT = program_dir()
auth = {"running": False, "status": "idle", "message": "尚未设置登录信息"}
auth_lock = threading.RLock()

def cookie_path():
    return ROOT / "cookie.txt"


def read_cookie():
    path = cookie_path()
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def save_cookie(value):
    text = str(value or "").strip()
    if not text or "=" not in text:
        raise RuntimeError("登录信息格式不正确，请粘贴完整的 cookie。")
    atomic_write(cookie_path(), text)


def clear_cookie():
    path = cookie_path()
    if path.exists():
        path.unlink()


def auth_state(include_cookie=False):
    with auth_lock:
        current = dict(auth)
    value = read_cookie()
    current["configured"] = bool(value)
    if include_cookie:
        current["cookie"] = value
    return current


def retrieve_login_cookie():
    """打开用户主动发起的登录窗口，只读取本程序登录页产生的拼多多 cookie。"""
    from pdd.scraping.login import open_login_and_capture_cookie

    try:
        with auth_lock:
            auth.update({"running": True, "status": "waiting", "message": "登录窗口已打开，请完成登录。"})
        value = open_login_and_capture_cookie()
        save_cookie(value)
        with auth_lock:
            auth.update({"running": False, "status": "ok", "message": "登录信息已导入，可以开始抓取。"})
    except Exception as exc:
        with auth_lock:
            auth.update({"running": False, "status": "failed", "message": str(exc) or "未能获取登录信息。"})
