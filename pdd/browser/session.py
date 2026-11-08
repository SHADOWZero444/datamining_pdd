"""本程序专用浏览器引擎；后台抓取与主动登录共用持久会话。"""
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright
from .mobile import configure_mobile_page

_session_lock = threading.Lock()


def browser_executable():
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if not root:
            continue
        for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            candidate = Path(root) / relative
            if candidate.is_file():
                return candidate
    raise RuntimeError("请先安装 Microsoft Edge 或 Chrome，再开始抓取。")


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def import_cookies(context, cookie_file, marker):
    """只导入用户新保存的 Cookie，避免旧文件覆盖验证产生的新会话。"""
    text = cookie_file.read_text(encoding="utf-8").strip() if cookie_file.exists() else ""
    previous = marker.read_text(encoding="ascii").strip() if marker.exists() else ""
    items = []
    for part in text.split(";"):
        if "=" in part:
            name, value = part.strip().split("=", 1)
            if name:
                items.append({"name": name, "value": value, "domain": ".pinduoduo.com", "path": "/"})
    if previous == fingerprint(text):
        # Session Cookie 在正常退出后可能消失；只补缺失项，不覆盖仍有效的新值。
        existing = {item["name"] for item in context.cookies(["https://mobile.pinduoduo.com/"])}
        missing = [item for item in items if item["name"] not in existing]
        if missing:
            context.add_cookies(missing)
        return
    context.clear_cookies()
    # 切换账号或清除 Cookie 时，同时清除本程序会话内的站点存储。
    page = context.new_page()
    session = context.new_cdp_session(page)
    try:
        for origin in ("https://mobile.pinduoduo.com", "https://www.pinduoduo.com"):
            session.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
    finally:
        session.detach()
        page.close()
    if items:
        context.add_cookies(items)
    marker.write_text(fingerprint(text), encoding="ascii")


def save_cookies(context, cookie_file, marker):
    items = context.cookies(["https://mobile.pinduoduo.com/"])
    if not items:
        return
    text = "; ".join(f"{item['name']}={item['value']}" for item in items)
    temporary = cookie_file.with_suffix(".session.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, cookie_file)
    marker.write_text(fingerprint(text), encoding="ascii")


@contextmanager
def capture_session(root, cookie_file, should_stop=None, *, headless=True):
    """仅管理本程序独立配置目录下启动的浏览器，不接管用户日常窗口。"""
    if not _session_lock.acquire(blocking=False):
        raise RuntimeError("抓取或登录窗口正在使用中，请先完成当前操作。")
    process = browser = None
    try:
        profile = Path(root) / "data" / "login-browser"
        profile.mkdir(parents=True, exist_ok=True)
        marker = profile.parent / "browser-cookie.sha256"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        executable = browser_executable()
        arguments = [
            str(executable), f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
        ]
        arguments += ["--headless=new", "--window-size=1000,760"] if headless else ["--new-window"]
        process = subprocess.Popen(arguments + ["about:blank"],
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        endpoint = f"http://127.0.0.1:{port}"
        with sync_playwright() as factory:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if should_stop and should_stop():
                    raise RuntimeError("已停止打开浏览器")
                try:
                    with urlopen(endpoint + "/json/version", timeout=.5) as response:
                        json.load(response)
                    browser = factory.chromium.connect_over_cdp(endpoint, timeout=2000)
                    break
                except Exception:
                    if process.poll() is not None:
                        raise RuntimeError("抓取浏览器未能启动，请关闭本程序遗留的抓取窗口后重试。")
                    time.sleep(.2)
            if browser is None:
                raise RuntimeError("抓取浏览器启动超时，请重试。")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(2000)
            device_session = configure_mobile_page(page, edge=executable.name.casefold() == "msedge.exe")
            try:
                import_cookies(context, Path(cookie_file), marker)
                yield page
            finally:
                try:
                    save_cookies(context, Path(cookie_file), marker)
                except Exception:
                    # 用户关闭浏览器时保留磁盘上的上一份会话，不影响已保存商品。
                    pass
                try:
                    device_session.detach()
                    browser.close()
                except Exception:
                    pass
    finally:
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        _session_lock.release()
