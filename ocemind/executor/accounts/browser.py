"""账号浏览器：一个账号一个 Chrome 用户目录，靠 CDP 驱动。

隔离方式是 Chromium 自己的 `--user-data-dir`，cookie / 缓存 / localStorage
各自独立。不做浏览器指纹伪造——那是矩阵号防关联用的，个人运维不需要，
而且平台的规则也不允许。

登录一次，会话由浏览器自己维持；无操作超时后自动关闭进程，下次点开再拉起。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from ocemind.paths import find_chrome
from .cdp import CDPError, ChromeProcess

# CDP 的调用是同步阻塞的，用一把可重入锁把同一账号的操作串起来
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(account_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(account_id, threading.RLock())


class BrowserSession:
    def __init__(self, account: dict, config) -> None:
        self.account = account
        self.config = config
        self.chrome: ChromeProcess | None = None
        self.started_at = 0.0
        self.last_used = 0.0
        self.last_error = ""

    @property
    def alive(self) -> bool:
        return self.chrome is not None and self.chrome.session is not None

    # ------------------------------------------------------------ 生命周期

    def ensure_started(self, url: str = "") -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            if self.alive:
                self.last_used = time.time()
                return
            chrome_path = self.config.get("chromePath") or find_chrome()
            self.chrome = ChromeProcess(
                chrome_path=chrome_path,
                profile_dir=self.account["profile_dir"],
                headless=bool(self.config.get("headless")),
                window=(int(self.config.get("screencastMaxWidth") or 1280), 800),
            )
            try:
                self.chrome.start(url or self.home_url)
            except Exception as exc:  # noqa: BLE001
                self.chrome = None
                self.last_error = str(exc)
                raise
            try:
                self.chrome.session.call("Page.enable", {})
            except CDPError:
                pass
            self.started_at = time.time()
            self.last_used = time.time()

    @property
    def home_url(self) -> str:
        from .platforms import platform_info

        return platform_info(self.account["platform"]).get("home", "about:blank")

    def stop(self) -> None:
        chrome, self.chrome = self.chrome, None
        if chrome:
            chrome.stop()

    def idle_seconds(self) -> float:
        return time.time() - (self.last_used or 0)

    # ------------------------------------------------------------ 操作

    def goto(self, url: str) -> dict:
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started(url)
            try:
                self.chrome.session.call("Page.navigate", {"url": url})
            except CDPError as exc:
                self.last_error = str(exc)
            self.last_used = time.time()
        time.sleep(0.8)
        return {"ok": True, "url": self.current_url()}

    def screenshot(self) -> bytes:
        lock = _lock_for(self.account["id"])
        quality = int(self.config.get("screencastQuality") or 70)
        with lock:
            self.ensure_started()
            try:
                result = self.chrome.session.call(
                    "Page.captureScreenshot",
                    {"format": "jpeg", "quality": quality,
                     "captureBeyondViewport": False},
                    timeout=20,
                )
                import base64

                self.last_used = time.time()
                return base64.b64decode(result.get("data", ""))
            except CDPError as exc:
                self.last_error = str(exc)
                return b""

    def click(self, x: float, y: float) -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started()
            sess = self.chrome.session
            for kind in ("mousePressed", "mouseReleased"):
                sess.call("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y,
                    "button": "left", "clickCount": 1,
                })
            self.last_used = time.time()

    def type_text(self, text: str) -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started()
            self.chrome.session.call("Input.insertText", {"text": text})
            self.last_used = time.time()

    def press(self, key: str) -> None:
        """支持 Enter / Backspace / Tab / Escape 等常用键。"""
        codes = {
            "Enter": (13, "\r"), "Backspace": (8, ""), "Tab": (9, "\t"),
            "Escape": (27, ""), "ArrowDown": (40, ""), "ArrowUp": (38, ""),
            "ArrowLeft": (37, ""), "ArrowRight": (39, ""), " ": (32, " "),
        }
        vk, text = codes.get(key, (0, key if len(key) == 1 else ""))
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started()
            sess = self.chrome.session
            sess.call("Input.dispatchKeyEvent", {
                "type": "rawKeyDown", "windowsVirtualKeyCode": vk, "key": key,
            })
            if text:
                sess.call("Input.dispatchKeyEvent", {
                    "type": "char", "text": text, "key": key,
                })
            sess.call("Input.dispatchKeyEvent", {
                "type": "keyUp", "windowsVirtualKeyCode": vk, "key": key,
            })
            self.last_used = time.time()

    def scroll(self, delta_y: int, x: int = 640, y: int = 400) -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started()
            self.chrome.session.call("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": x, "y": y,
                "deltaX": 0, "deltaY": delta_y,
            })
            self.last_used = time.time()

    def set_file_input(self, path: str, selector: str = "input[type=file]") -> bool:
        """把本地文件塞进页面的 <input type=file>。

        这是上传的关键一步：页面里没有真的系统文件选择框弹出，
        平台收到的是普通的 change 事件，和手动选文件在它看来没区别。
        """
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started()
            sess = self.chrome.session
            try:
                sess.call("DOM.enable", {})
                doc = sess.call("DOM.getDocument", {"depth": 1})
                root = doc.get("root", {}).get("nodeId")
                node = sess.call("DOM.querySelector",
                                 {"nodeId": root, "selector": selector})
                node_id = node.get("nodeId")
                if not node_id:
                    return False
                sess.call("DOM.setFileInputFiles",
                          {"nodeId": node_id, "files": [path]})
                self.last_used = time.time()
                return True
            except CDPError as exc:
                self.last_error = str(exc)
                return False

    def evaluate(self, expression: str):
        lock = _lock_for(self.account["id"])
        with lock:
            self.ensure_started()
            result = self.chrome.session.call("Runtime.evaluate", {
                "expression": expression, "returnByValue": True,
                "awaitPromise": True,
            })
            self.last_used = time.time()
            return result.get("result", {}).get("value")

    def current_url(self) -> str:
        try:
            return str(self.evaluate("location.href") or "")
        except Exception:  # noqa: BLE001
            return ""

    def cookie_expiry(self) -> float | None:
        """读 profile 里 Cookies 表的最晚过期时间（不解密，只用明文时间戳）。"""
        return read_cookie_expiry(Path(self.account["profile_dir"]))


def read_cookie_expiry(profile_dir: Path) -> float | None:
    """Chromium 的 expires_utc 是"自 1601-01-01 起的微秒数"。"""
    import sqlite3

    candidates = [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
        profile_dir / "Network" / "Cookies",
    ]
    db = next((p for p in candidates if p.exists()), None)
    if db is None:
        return None
    try:
        # 浏览器在跑时表会锁住，用只读 + immutable 打开
        uri = f"file:{db}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            row = conn.execute(
                "SELECT MAX(expires_utc) FROM cookies WHERE expires_utc > 0"
            ).fetchone()
        finally:
            conn.close()
        value = row[0] if row else None
        if not value:
            return None
        return float(value) / 1_000_000 - 11644473600
    except Exception:  # noqa: BLE001
        return None


class BrowserPool:
    def __init__(self, config) -> None:
        self.config = config
        self.sessions: dict[str, BrowserSession] = {}
        self._lock = threading.RLock()
        self._running = True
        threading.Thread(target=self._reaper, daemon=True).start()

    def get(self, account: dict) -> BrowserSession:
        with self._lock:
            sess = self.sessions.get(account["id"])
            if sess is None:
                sess = BrowserSession(account, self.config)
                self.sessions[account["id"]] = sess
            return sess

    def close(self, account_id: str) -> None:
        with self._lock:
            sess = self.sessions.pop(account_id, None)
        if sess:
            sess.stop()

    def running(self) -> list[str]:
        with self._lock:
            return [k for k, v in self.sessions.items() if v.alive]

    def _reaper(self) -> None:
        while self._running:
            time.sleep(30)
            timeout = int(self.config.get("keepBrowserAliveSeconds") or 900)
            with self._lock:
                stale = [k for k, v in self.sessions.items()
                         if v.alive and v.idle_seconds() > timeout]
            for account_id in stale:
                self.close(account_id)

    def shutdown(self) -> None:
        self._running = False
        with self._lock:
            sessions = list(self.sessions.values())
        for sess in sessions:
            sess.stop()
