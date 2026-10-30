"""浏览器验证产物统一存放在项目外；可用 PDD_TEST_ARTIFACTS 指定任务目录。"""
import os
import time
from datetime import datetime
from pathlib import Path

def artifact_dir():
    path = Path(os.environ.get("PDD_TEST_ARTIFACTS") or (Path.home() / "Documents" / "ChatGPT" / ("pdd-ui-tests-" + datetime.now().strftime("%Y%m%d-%H%M%S"))))
    path.mkdir(parents=True, exist_ok=True)
    return path


def wait_for_state(page, expression, timeout=30000):
    """不用页面内 eval 等待状态，保留应用严格的 CSP。"""
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"等待页面状态超时：{expression}")
