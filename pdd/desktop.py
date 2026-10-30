"""桌面窗口和本地服务的生命周期。"""
import threading
from http.server import ThreadingHTTPServer
from pdd.paths import program_dir
from pdd.services import jobs
from pdd.server.http import AppHandler

HOST = "127.0.0.1"
PORT = 8765

def serve():
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def open_window():
    url = f"http://{HOST}:{PORT}"
    try:
        import webview
        webview.create_window(
            "拼多多数据挖掘",
            url,
            width=1280,
            height=820,
            min_size=(880, 560),
            text_select=True,
        )
        # 保留本地界面的偏好；与抓取浏览器的登录环境分开存储。
        ui_storage = program_dir() / "data" / "ui-profile"
        ui_storage.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(ui_storage))
        return
    except Exception:
        import webbrowser
        webbrowser.open(url)
        print(f"拼多多数据挖掘已打开：{url}")
        print("关闭这个窗口会退出程序。")
        threading.Event().wait()


def main():
    server = serve()
    try:
        open_window()
    finally:
        with jobs.job_lock:
            if jobs.job_cancel:
                jobs.job_cancel.set()
            if jobs.job_capture_view:
                jobs.job_capture_view.close()
        server.shutdown()
        server.server_close()
