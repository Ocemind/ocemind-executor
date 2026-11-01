# -*- coding: utf-8 -*-
"""桌面壳：本地 FastAPI 服务 + pywebview 原生窗口。

由更早的桌面版 app.py 迁入。三件事和纯 Web 模式不同：

1. 高分屏下必须开 DPI 感知，否则窗口按物理像素建、网页按 2x 渲染，内容被裁；
2. 只允许一个实例，两个进程同时写 tasks.json 会把任务队列写坏；
3. pywebview 起不来（没装 WebView2、或者干脆没装 pywebview）时自动退回
   系统浏览器，别让用户双击了图标却什么都没看见。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests
import uvicorn

from ocemind.downloader.api import create_app
from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager

APP_NAME = "Ocemind 内容执行器"
MUTEX_NAME = "Local\\OcemindExecutor_SingleInstance"

# 网页的底线：1024×680。跟 style.css 里 #app 的 min-width / min-height、
# Tauri 壳的 min_inner_size 是同一个数，三处得一起改（有测试钉着）。
MIN_CLIENT_W, MIN_CLIENT_H = 1024, 680


def _window_min_size(client_w: int = MIN_CLIENT_W, client_h: int = MIN_CLIENT_H):
    """把「客户区不小于多少」换算成「窗口外框不小于多少」。

    pywebview 的 min_size 量的是窗口外框（Form.MinimumSize），网页拿到的是
    扣掉标题栏和边框之后的客户区。按外框设 1024×680 的话，客户区只剩
    1011×644，窗口拖到底反而冒出滚动条。这里按 96 DPI 的边框补一圈，
    窗口拖到头时网页那边正好还剩 1024×680。
    """
    if sys.platform != "win32":
        return (client_w, client_h)
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT(0, 0, client_w, client_h)
        ctypes.windll.user32.AdjustWindowRectExForDpi(
            ctypes.byref(rect), 0x00CF0000, False, 0, 96)  # WS_OVERLAPPEDWINDOW
        return (rect.right - rect.left, rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        return (client_w, client_h)


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _redirect_stdout(log_path: Path) -> None:
    """pythonw 下没有控制台，stdout/stderr 是 None，任何 print 都会抛异常。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


class JsApi:
    """暴露给网页的本地能力（pywebview）。"""

    def select_folder(self, current: str = "") -> str:
        import webview

        window = webview.windows[0] if webview.windows else None
        if not window:
            return ""
        result = window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=current if current and Path(current).is_dir() else "",
        )
        return result[0] if result else ""

    def select_file(self, current: str = "") -> str:
        """选一个待处理的文件（视频 / 音频 / 文档 / 图片）。"""
        import webview

        window = webview.windows[0] if webview.windows else None
        if not window:
            return ""
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=str(Path(current).parent) if current else "",
        )
        return result[0] if result else ""

    def select_cookie_file(self, current: str = "") -> str:
        import webview

        window = webview.windows[0] if webview.windows else None
        if not window:
            return ""
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=str(Path(current).parent) if current else "",
            file_types=("Cookies (*.txt)", "所有文件 (*.*)"),
        )
        return result[0] if result else ""

    def open_path(self, path: str = "") -> bool:
        if path and Path(path).exists():
            os.startfile(path)  # noqa: S606
            return True
        return False


_MUTEX = None


def _already_running() -> bool:
    global _MUTEX
    if sys.platform != "win32":
        return False
    import ctypes

    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _message(text: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    print(text)


def _wait_ready(base: str) -> None:
    for _ in range(100):
        try:
            requests.get(f"{base}api/system", timeout=1)
            return
        except requests.RequestException:
            time.sleep(0.1)


def _run_browser(base: str) -> None:
    webbrowser.open(base)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _run_window(base: str) -> None:
    import webview

    # 打开 1280×820；最小客户区 1024×680——再小布局就挤散了，窗口直接拖不动。
    webview.create_window(
        APP_NAME, base, width=1280, height=820, min_size=_window_min_size(),
        background_color="#f6f7f9", js_api=JsApi(),
    )
    webview.start(debug=False)


def main(force_browser: bool = False) -> None:
    """启动桌面壳。`force_browser` 或环境变量可跳过原生窗口。"""
    _enable_dpi_awareness()
    if _already_running():
        _message(f"{APP_NAME} 已经在运行了。")
        return

    cfg = Config()
    _redirect_stdout(cfg.log_path)
    manager = TaskManager(cfg)
    port = _free_port()
    app = create_app(cfg, manager)

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    base = f"http://127.0.0.1:{port}/"
    _wait_ready(base)

    try:
        if force_browser or os.environ.get("OCEMIND_FORCE_BROWSER") == "1":
            _run_browser(base)
        else:
            try:
                _run_window(base)
            except Exception:  # noqa: BLE001
                # 没装 pywebview / 没装 WebView2：退回浏览器，至少能继续用
                _run_browser(base)
    finally:
        executor = getattr(app.state, "executor", None)
        if executor is not None:
            try:
                executor.close()
            except Exception:  # noqa: BLE001
                pass
        manager.shutdown()
        server.should_exit = True
