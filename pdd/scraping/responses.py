"""搜索响应的错误分类；错误码来自手机网页自身的定义。"""
import re
from urllib.parse import urljoin, urlsplit
from .constants import BASE


def verification_hint(data):
    """只识别验证消息和显式验证 URL 字段；不把通用跳转、登录或响应头当验证。"""
    if not isinstance(data, dict):
        return False, None
    required = False
    for source in (data, data.get("data")):
        if not isinstance(source, dict):
            continue
        for key in ("error_msg", "error_message", "message"):
            if re.search(r"人机验证|安全验证|请.{0,6}验证|captcha|verification", str(source.get(key) or ""), re.I):
                required = True
        for key in ("verify_url", "verifyUrl", "captcha_url", "captchaUrl", "verification_url", "verificationUrl"):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            required = True
            if "\\" in value or any(ord(ch) < 32 for ch in value):
                continue
            try:
                link = urljoin(BASE, value.strip())
                parts = urlsplit(link)
                if parts.scheme == "https" and parts.netloc.lower() in ("mobile.pinduoduo.com", "mobile.pinduoduo.com:443"):
                    return True, link
            except ValueError:
                continue
    return required, None


def error_code(data):
    if not isinstance(data, dict):
        return None
    payload = data.get("data")
    for source in (data, payload):
        if not isinstance(source, dict):
            continue
        value = source.get("error_code")
        if type(value) is int:
            return value
        if isinstance(value, str) and value.isdecimal():
            return int(value)
    return None


def failure_kind(status, data):
    code = error_code(data)
    if status == 429:
        return "rate_limited"
    if status == 401 or code == 40001:
        return "login"
    if code == 40002:
        return "rate_limited"
    return "http_error" if status != 200 else None
