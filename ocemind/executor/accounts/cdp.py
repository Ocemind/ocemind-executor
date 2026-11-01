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
        out = self._rest
        while len(out) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("连接被对端关闭")
            out += chunk
        self._rest = out[n:]
        return out[:n]

    def recv(self) -> str | None:
        """返回一条文本消息；控制帧（ping/close）就地处理。"""
        while True:
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
        self._lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                raw = self.ws.recv()
            except Exception:  # noqa: BLE001
                break
            if raw is None:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            msg_id = msg.get("id")
            if msg_id is None:
                continue          # 事件，暂不订阅
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
                 headless: bool = True, window: tuple[int, int] = (1280, 800)) -> None:
        self.chrome_path = chrome_path
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.window = window
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self.session: CDPSession | None = None

    def start(self, url: str = "about:blank", timeout: float = 30.0) -> CDPSession:
        if not self.chrome_path or not Path(self.chrome_path).exists():
            raise CDPError("找不到 Chrome，可在设置里手动指定路径")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()

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
        if self.headless:
            args += ["--headless=new", "--disable-gpu"]
        args.append(url)

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
