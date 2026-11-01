"""Chrome DevTools Protocol 客户端 —— 纯标准库。

为什么不装 playwright：
  它带一个 node 驱动的二进制，几十兆，换机器容易失效，而且是被杀软/SAC
  重点关照的对象。CDP 是 Chrome 自带的调试协议，只要有 Chrome 就能用，
  零第三方依赖，打包时一个字节都不用多带。

实现范围（刚好覆盖"看到画面 + 点 + 打字 + 执行 JS"）：
  Page.navigate / Page.captureScreenshot
  Input.dispatchMouseEvent / Input.insertText / Input.dispatchKeyEvent
  Runtime.evaluate
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


class CDPError(RuntimeError):
    pass


# --------------------------------------------------------------- WebSocket

class WSClient:
    """最小可用的 WebSocket 客户端（只处理文本帧）。"""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        head = buf.split(b"\r\n\r\n", 1)[0].decode("latin1")
        if " 101 " not in head.split("\r\n")[0]:
            raise CDPError(f"WebSocket 握手失败: {head.splitlines()[0]}")
        self._rest = buf.split(b"\r\n\r\n", 1)[1]
        self._lock = threading.Lock()
        self._closed = False

    # ---------------------------------------------------------- 发送

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            if self._closed:
                raise CDPError("连接已关闭")
            self.sock.sendall(bytes(header) + masked)

    # ---------------------------------------------------------- 接收

    def _read_exact(self, n: int) -> bytes:
        # 攒在 self._rest 里而不是局部变量：读一半超时了，已经到的字节
        # 还得留着，否则这条消息就永远接不成整条了
        while len(self._rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("连接被对端关闭")
            self._rest += chunk
        out, self._rest = self._rest[:n], self._rest[n:]
        return out

    def recv(self) -> str | None:
        """返回一条文本消息；控制帧（ping/close）就地处理。

        浏览器这一段时间什么都没发就返回 None，让调用方接着等——
        用了 screencast 之后页面静着的时候是不会有任何消息的，
        以前这里一超时就当连接断了，读线程一死，之后所有 CDP 调用全卡住。
        """
        while True:
            if not self._rest:
                # 只在「一条消息都还没开始」的时候把超时当空闲；
                # 已经开始读了就必须读完，否则字节流会错位
                try:
                    self._rest = self.sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    return None
                if not self._rest:
                    raise CDPError("连接被对端关闭")
            b1, b2 = self._read_exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            data = self._read_exact(length)
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

            if opcode == 0x8:          # close
                raise CDPError("对端关闭连接")
            if opcode == 0x9:          # ping -> pong
                try:
                    self.send_control(0xA, data)
                except Exception:  # noqa: BLE001
                    pass
                continue
            if opcode == 0xA:          # pong
                continue
            if opcode in (0x1, 0x0):
                return data.decode("utf-8", "replace")

    def send_control(self, opcode: int, data: bytes = b"") -> None:
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        else:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        try:
            self.send_control(0x8)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- CDP 会话

class CDPSession:
    def __init__(self, ws_url: str, timeout: float = 45.0) -> None:
        self.ws = WSClient(ws_url, timeout=timeout)
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._handlers: dict[str, list] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ---------------------------------------------------------- 事件订阅

    def on(self, method: str, handler) -> None:
        """订阅一条 CDP 事件（比如 Page.screencastFrame）。

        回调在读线程里跑：里面只能发不等回包的消息（notify），
        调 call() 会等自己派发回包，直接卡死。
        """
        with self._lock:
            self._handlers.setdefault(method, []).append(handler)

    def notify(self, method: str, params: dict | None = None) -> None:
        """发一条不等回包的消息，给事件回调用。"""
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
        self.ws.send(json.dumps({"id": msg_id, "method": method,
                                 "params": params or {}}))

    @property
    def alive(self) -> bool:
        """读线程还在不在。它一停，这条会话就再也收不到任何回包，
        对外必须表现为「浏览器没了」，不能让调用方一直等到超时。"""
        return not self._closed and self._reader.is_alive()

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                raw = self.ws.recv()
            except Exception:  # noqa: BLE001
                self._closed = True
                break
            if raw is None:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            msg_id = msg.get("id")
            if msg_id is None:
                method = msg.get("method") or ""
                with self._lock:
                    handlers = list(self._handlers.get(method, ()))
                for handler in handlers:
                    try:
                        handler(msg.get("params") or {})
                    except Exception:  # noqa: BLE001
                        continue   # 一条事件出错不该把读线程带下去
                continue
            with self._lock:
                slot = self._pending.get(msg_id)
            if slot is not None:
                slot["result"] = msg
                slot["event"].set()

    def call(self, method: str, params: dict | None = None,
             timeout: float = 45.0) -> dict:
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            slot = {"event": threading.Event(), "result": None}
            self._pending[msg_id] = slot
        payload = {"id": msg_id, "method": method, "params": params or {}}
        try:
            self.ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._pending.pop(msg_id, None)
            raise CDPError(f"发送 {method} 失败: {exc}") from exc

        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise CDPError(f"{method} 超时")
        with self._lock:
            self._pending.pop(msg_id, None)
        result = slot["result"] or {}
        if "error" in result:
            raise CDPError(f"{method}: {result['error'].get('message')}")
        return result.get("result", {})

    def close(self) -> None:
        self._closed = True
        self.ws.close()


# ---------------------------------------------------------------- Chrome 进程

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ChromeProcess:
    """一个账号一个独占的用户目录，互相看不到 cookie。"""

    def __init__(self, chrome_path: str, profile_dir: str | Path,
                 headless: bool = True, window: tuple[int, int] = (1280, 800),
                 proxy: str = "") -> None:
        self.chrome_path = chrome_path
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.window = window
        self.proxy = proxy
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self.session: CDPSession | None = None

    def args(self, url: str = "about:blank") -> list[str]:
        args = [
            self.chrome_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            f"--window-size={self.window[0]},{self.window[1]}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-notifications",
            "--disable-features=Translate,MediaRouter",
            "--disable-blink-features=AutomationControlled",
            "--remote-allow-origins=*",
        ]
        if self.proxy:
            # 代理在浏览器启动时就定死。Chrome 没有「换代理不重启」的接口，
            # 所以改代理要把这个账号的浏览器关掉重开
            args.append(f"--proxy-server={self.proxy}")
        if self.headless:
            args += ["--headless=new", "--disable-gpu"]
        args.append(url)
        return args

    def _attach_existing(self) -> CDPSession | None:
        """上个进程没关干净时（比如服务被直接杀掉），Chrome 还占着这个用户目录。
        再起一个会立刻退出（退出码 21），登录态也用不上。
        目录里的 DevToolsActivePort 记着它的调试端口，连得上就接回去。"""
        try:
            port = int((self.profile_dir / "DevToolsActivePort")
                       .read_text(encoding="utf-8").splitlines()[0])
        except (OSError, ValueError, IndexError):
            return None
        try:
            _http_json(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
        except Exception:  # noqa: BLE001
            return None
        page_ws = self._pick_page_target(timeout=time.time() + 3)
        if not page_ws:
            return None
        self.port = port
        self.session = CDPSession(page_ws)
        return self.session

    def start(self, url: str = "about:blank", timeout: float = 30.0) -> CDPSession:
        if not self.chrome_path or not Path(self.chrome_path).exists():
            raise CDPError("找不到 Chrome，可在设置里手动指定路径")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        reused = self._attach_existing()
        if reused is not None:
            return reused
        self.port = _free_port()

        args = self.args(url)

        creation = 0
        if os.name == "nt":
            creation = 0x08000000  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation,
        )

        deadline = time.time() + timeout
        ws_url = ""
        while time.time() < deadline:
            try:
                info = _http_json(f"http://127.0.0.1:{self.port}/json/version")
                ws_url = info.get("webSocketDebuggerUrl") or ""
                if ws_url:
                    break
            except Exception:  # noqa: BLE001
                pass
            if self.proc.poll() is not None:
                raise CDPError(f"Chrome 提前退出（退出码 {self.proc.returncode}）")
            time.sleep(0.3)
        if not ws_url:
            raise CDPError("Chrome 调试端口没起来")

        # 优先附着到已有的页面目标，没有就新建
        page_ws = self._pick_page_target(timeout=deadline)
        self.session = CDPSession(page_ws or ws_url)
        if not page_ws:
            self.session.call("Target.createTarget", {"url": url})
        return self.session

    def _pick_page_target(self, timeout: float) -> str:
        while time.time() < timeout:
            try:
                targets = _http_json(f"http://127.0.0.1:{self.port}/json/list")
            except Exception:  # noqa: BLE001
                targets = []
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
            time.sleep(0.3)
        return ""

    def stop(self) -> None:
        if self.session:
            if not self.proc:
                # 接回来的是上一个进程留下的浏览器，没有进程句柄可等，让它自己退
                try:
                    self.session.call("Browser.close", {}, timeout=3)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.session.close()
            except Exception:  # noqa: BLE001
                pass
            self.session = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self.proc = None
