"""登录会话：网页上点「登录一次」之后在后台跑 Playwright。

队列里的课程任务**不会**自己弹浏览器（见 course_engine）。要登录就得有人
明确点一下，这就是那一下。浏览器窗口由 playwright 弹出，用户登完关掉窗口
或通过接口验证后自动收下 cookie 并落盘。

状态只放内存：这是「一次动作」，不是配置。程序重启后没登完的会话就作废，
重新点一次即可。
"""
from __future__ import annotations

import threading
import time

from ocemind import auth


class LoginSession:
    """同一时间只允许一个登录会话。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.state = "idle"          # idle / running / done / error
        self.error = ""
        self.started_at = 0.0
        self.cookies_path = ""
        self._log: list[str] = []

    # ------------------------------------------------------------ 查询
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "error": self.error,
                "log": list(self._log[-8:]),
                "started_at": self.started_at,
                "cookies_path": self.cookies_path,
                "playwright": auth.playwright_available(),
            }

    # ------------------------------------------------------------ 动作
    def start(self, app_id: str, alive_id: str = "", timeout_s: int = 300) -> dict:
        if not app_id:
            raise ValueError("缺少 app_id：请先粘贴课程链接")
        if not auth.playwright_available():
            raise RuntimeError(
                "当前环境没有 playwright，自动登录用不了。\n"
                "    源码运行：pip install playwright && playwright install chromium\n"
                "    也可以直接导入导出的 cookies.txt")
        if self.running():
            raise RuntimeError("已经有一个登录窗口在等了，先完成它")

        with self._lock:
            self._thread = threading.Thread(
                target=self._run, args=(app_id, alive_id, timeout_s), daemon=True)
            self.state = "running"
            self.error = ""
            self.started_at = time.time()
            self.cookies_path = ""
            self._log = []
            self._thread.start()
        return self.status()

    def _say(self, message: str) -> None:
        with self._lock:
            self._log.append(message)

    def _run(self, app_id: str, alive_id: str, timeout_s: int) -> None:
        verifier = auth.api_verifier(app_id, alive_id) if alive_id else None
        try:
            cks = auth.login_interactive(
                app_id, timeout_s=timeout_s, verifier=verifier,
                on_status=self._say)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.state = "error"
                self.error = f"{type(exc).__name__}: {exc}"
            return

        if not cks:
            with self._lock:
                self.state = "error"
                self.error = "没拿到登录态（窗口已关或超时）"
            return

        try:
            path = auth.save_cookies(cks)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.state = "error"
                self.error = f"保存 cookies 失败：{exc}"
            return

        with self._lock:
            self.state = "done"
            self.cookies_path = path
            self._log.append(f"已保存到 {path}")
