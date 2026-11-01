"""账号浏览器：一个账号一个 Chrome 用户目录，靠 CDP 驱动。

隔离方式是 Chromium 自己的 `--user-data-dir`，cookie / 缓存 / localStorage
各自独立。不做浏览器指纹伪造——那是矩阵号防关联用的，个人运维不需要，
而且平台的规则也不允许。

登录一次，会话由浏览器自己维持；无操作超时后自动关闭进程，下次点开再拉起。
"""
from __future__ import annotations

import base64
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
        # 画面走 Page.startScreencast：页面一变 Chrome 自己推一帧过来，
        # 比「每隔几百毫秒掐一张图」快得多，也不用来回问。
        self._frame = b""
        self._frame_seq = 0
        self._frame_lock = threading.Lock()
        self._stream_for: object = None       # 已经开过流的 CDP 会话
        self._viewport = (0, 0, 1.0)

    @property
    def alive(self) -> bool:
        session = self.chrome.session if self.chrome else None
        # 会话断了（读线程停了）也算没了：不然界面上显示「开着」，
        # 点哪儿都没反应，只能干等超时
        return session is not None and getattr(session, "alive", True)

    # ------------------------------------------------------------ 生命周期

    def ensure_started(self, url: str = "") -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            if self.alive:
                self.last_used = time.time()
                return
            if self.chrome is not None:
                # 进程还在但会话已经断了：先收掉旧的，否则用户目录被占着，
                # 新起的浏览器会立刻退出（退出码 21）
                try:
                    self.chrome.stop()
                except Exception:  # noqa: BLE001
                    pass
                self.chrome = None
            chrome_path = self.config.get("chromePath") or find_chrome()
            self.chrome = ChromeProcess(
                chrome_path=chrome_path,
                profile_dir=self.account["profile_dir"],
                # 账号浏览器一律无头：画面用 CDP 截帧送回界面里看。
                # 开成有头会真的弹一个 Chrome 窗口出来，登录也在那个窗口里做，
                # 等于把「在软件里操作」这件事又推回桌面上了。
                headless=True,
                window=(int(self.config.get("screencastMaxWidth") or 1280), 800),
                # 账号自己的代理；没配就走系统直连，不吃设置里的全局代理
                proxy=self.account.get("proxy") or "",
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
            self._ensure_stream()

    # ------------------------------------------------------------ 画面

    def _ensure_stream(self) -> None:
        """开一次 screencast。接了上个进程留下的浏览器时也要重开：
        流是挂在 CDP 会话上的，会话是新的。"""
        if not self.chrome or not self.chrome.session:
            return
        session = self.chrome.session
        if self._stream_for is session:
            return
        session.on("Page.screencastFrame", self._on_frame)
        # 画质别低于 80：窗口拉大以后，70 的 JPEG 放大看字是糊的
        quality = max(80, int(self.config.get("screencastQuality") or 82))
        try:
            session.call("Page.startScreencast", {
                "format": "jpeg",
                "quality": quality,
                "maxWidth": 0,
                "maxHeight": 0,
                "everyNthFrame": 1,
            }, timeout=8)
        except CDPError as exc:
            # 开不起来就退回掐图老路，界面不至于空着
            self.last_error = str(exc)
            self._stream_for = None
            return
        self._stream_for = session
        if self._viewport[0] and self._viewport[1]:
            try:
                self._apply_viewport(*self._viewport)
            except CDPError:
                pass

    def _on_frame(self, params: dict) -> None:
        """Chrome 推来的一帧。存下来，界面来取时直接给，不再问浏览器。"""
        data = params.get("data") or ""
        if data:
            try:
                frame = base64.b64decode(data)
            except Exception:  # noqa: BLE001
                frame = b""
            if frame:
                with self._frame_lock:
                    self._frame = frame
                    self._frame_seq += 1
        session_id = params.get("sessionId")
        if session_id is not None:
            try:
                session = self.chrome.session if self.chrome else None
                if session:
                    # 不回 ack 浏览器就不再推下一帧；这里在读线程里，只能不等回包
                    session.notify("Page.screencastFrameAck",
                                   {"sessionId": session_id})
            except Exception:  # noqa: BLE001
                pass

    def current_frame(self) -> tuple[bytes, int]:
        """(最新一帧 JPEG, 序号)。序号供界面判断「这一帧是不是刚推的」。"""
        with self._frame_lock:
            return self._frame, self._frame_seq

    def _apply_viewport(self, width: int, height: int, scale: float = 1.0) -> None:
        self.chrome.session.call("Emulation.setDeviceMetricsOverride", {
            "width": max(320, int(width)),
            "height": max(240, int(height)),
            # 高分屏按 scale 倍出帧，界面再缩回 CSS 像素显示，字才不发虚
            "deviceScaleFactor": max(1.0, min(2.0, float(scale or 1.0))),
            "mobile": False,
        }, timeout=8)

    def resize(self, width: int, height: int, scale: float = 1.0) -> None:
        """让页面的视口跟界面里那块画布一样大。

        不然是 1280×800 的页面缩进一个更小的框里，字发虚、滚动条乱飘，
        看着就不像浏览器。scale 是设备的 devicePixelRatio。
        """
        self._viewport = (int(width), int(height), float(scale or 1.0))
        lock = _lock_for(self.account["id"])
        with lock:
            # 浏览器已经关了就别因为一个迟到的尺寸请求把它重新拉起来
            if not self.alive:
                return
            try:
                self._apply_viewport(*self._viewport)
            except CDPError as exc:
                self.last_error = str(exc)
            self.last_used = time.time()

    @property
    def home_url(self) -> str:
        from .platforms import platform_info

        return platform_info(self.account["platform"]).get("home", "about:blank")

    def stop(self) -> None:
        chrome, self.chrome = self.chrome, None
        self._stream_for = None
        with self._frame_lock:
            self._frame = b""
            self._frame_seq = 0
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
                    timeout=8,
                )
                self.last_used = time.time()
                return base64.b64decode(result.get("data", ""))
            except Exception as exc:  # noqa: BLE001
                # 截帧失败不该把这一帧的请求挂住，界面退回去显示占位就行
                self.last_error = str(exc)
                return b""

    def click(self, x: float, y: float, count: int = 1,
              button: str = "left") -> None:
        """点一下。count=2 就是双击——列表里双击打开、输入框双击选词都靠它。"""
        lock = _lock_for(self.account["id"])
        with lock:
            if not self.alive:
                return
            sess = self.chrome.session
            for kind in ("mousePressed", "mouseReleased"):
                sess.call("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y,
                    "button": button, "clickCount": max(1, int(count)),
                })
            self.last_used = time.time()

    def mouse_move(self, x: float, y: float) -> None:
        """把鼠标挪过去。悬停才展开的菜单、tooltip 全靠这个。"""
        lock = _lock_for(self.account["id"])
        with lock:
            if not self.alive:
                return
            self.chrome.session.call("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y,
                "button": "none", "buttons": 0,
            })
            self.last_used = time.time()

    def key(self, event_type: str, key: str, code: str = "", vk: int = 0,
            text: str = "", modifiers: int = 0) -> None:
        """真实按键。event_type 取 keyDown / keyUp。

        带 text 的走 keyDown——Chrome 靠它把字符喂进输入框；
        功能键（方向、退格）没有 text，走 rawKeyDown。
        """
        lock = _lock_for(self.account["id"])
        params = {
            "key": key or "",
            "code": code or "",
            "windowsVirtualKeyCode": int(vk or 0),
            "nativeVirtualKeyCode": int(vk or 0),
            "modifiers": int(modifiers or 0),
        }
        if event_type == "keyUp":
            params["type"] = "keyUp"
        elif text:
            params["type"] = "keyDown"
            params["text"] = text
            params["unmodifiedText"] = text
        else:
            params["type"] = "rawKeyDown"
        with lock:
            if not self.alive:
                return
            self.chrome.session.call("Input.dispatchKeyEvent", params)
            # 浏览器自己会用 keyDown 带出字符，keyUp 只收尾，不重复插入
            self.last_used = time.time()

    def type_text(self, text: str) -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            if not self.alive:
                return
            self.chrome.session.call("Input.insertText", {"text": text})
            self.last_used = time.time()

    # 常用功能键：key / code / 虚拟键码 / 要喂的字符
    KEYS = {
        "Enter": ("Enter", 13, "\r"),
        "Backspace": ("Backspace", 8, ""),
        "Tab": ("Tab", 9, ""),
        "Escape": ("Escape", 27, ""),
        "Delete": ("Delete", 46, ""),
        "ArrowDown": ("ArrowDown", 40, ""),
        "ArrowUp": ("ArrowUp", 38, ""),
        "ArrowLeft": ("ArrowLeft", 37, ""),
        "ArrowRight": ("ArrowRight", 39, ""),
        "Home": ("Home", 36, ""),
        "End": ("End", 35, ""),
        "PageUp": ("PageUp", 33, ""),
        "PageDown": ("PageDown", 34, ""),
        " ": ("Space", 32, " "),
    }

    def press(self, key: str) -> None:
        """按一下功能键（Enter / Backspace / Tab / 方向键……）。"""
        name, vk, text = self.KEYS.get(key, (key, 0, key if len(key) == 1 else ""))
        self.key("keyDown", name, code=name, vk=vk, text=text)
        self.key("keyUp", name, code=name, vk=vk)

    def wheel(self, delta_y: int, delta_x: int = 0,
              x: int = 640, y: int = 400) -> None:
        lock = _lock_for(self.account["id"])
        with lock:
            if not self.alive:
                return
            self.chrome.session.call("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": x, "y": y,
                "deltaX": int(delta_x), "deltaY": int(delta_y),
                "button": "none",
            })
            self.last_used = time.time()

    def scroll(self, delta_y: int, x: int = 640, y: int = 400) -> None:
        self.wheel(delta_y, 0, x, y)

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

    def refresh(self, account: dict) -> None:
        """账号改了（比如换了代理），让已经建好的会话下次启动用新值。"""
        with self._lock:
            sess = self.sessions.get(account["id"])
            if sess is not None:
                sess.account = account

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
