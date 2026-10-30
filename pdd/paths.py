import sys
from pathlib import Path

def program_dir():
    """用户看到的软件目录。数据和登录文件都放在这里。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_dir():
    """页面和图标所在目录。打包后在程序内部，开发时就是项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]
