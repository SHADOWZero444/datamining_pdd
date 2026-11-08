"""搜索响应的错误分类；错误码来自手机网页自身的定义。"""


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
