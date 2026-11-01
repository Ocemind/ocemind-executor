# -*- coding: utf-8 -*-
"""本机回环端到端测试：不用外网，也不碰真实配置。

起一个本机 HTTP 服务当下载源（支持 Range、可调慢），然后真起 FastAPI + uvicorn，
完整跑一遍：添加任务 → 下载 → 校验 → 暂停 → 续传 → 重启恢复 → 重复排队保护。

    python tests/manual/manual_local_e2e.py

比 pytest 慢（约 30 秒）但比联网脚本稳，适合改完 manager / http_downloader 后跑。
"""
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FAST = os.urandom(5 * 1024 * 1024)
SLOW = os.urandom(20 * 1024 * 1024)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _data(self):
        return {"/fast.bin": FAST, "/slow.bin": SLOW}.get(self.path.split("?")[0])

    def do_HEAD(self):
        data = self._data()
        if data is None:
            self.send_response(404, ""); self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_GET(self):
        data = self._data()
        if data is None:
            self.send_response(404, ""); self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end, partial = 0, len(data) - 1, False
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start, end, partial = int(a or 0), int(b) if b else len(data) - 1, True
        body = data[start:end + 1]
        self.send_response(206 if partial else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        chunk = 64 * 1024
        delay = 0.15 if self.path.startswith("/slow.bin") else 0
        try:
            for i in range(0, len(body), chunk):
                self.wfile.write(body[i:i + chunk])
                if delay:
                    time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError):
            pass


class QuietServer(ThreadingHTTPServer):
    """暂停时客户端会主动断开，别为此打一堆 traceback。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Harness:
    """一套独立的数据目录 + 本进程内的服务。"""

    def __init__(self):
        self.home = tempfile.mkdtemp(prefix="ocemind_e2e_home_")
        self.out = tempfile.mkdtemp(prefix="ocemind_e2e_out_")
        os.environ["OCEMIND_HOME"] = self.home
        self.www_port = free_port()
        self.www = QuietServer(("127.0.0.1", self.www_port), Handler)
        threading.Thread(target=self.www.serve_forever, daemon=True).start()
        self.port = free_port()
        self.manager = None
        self.server = None

    def start(self):
        import uvicorn

        from ocemind.downloader.api import create_app
        from ocemind.downloader.config import Config
        from ocemind.downloader.manager import TaskManager

        cfg = Config(home=self.home)
        cfg.update({"defaultDir": self.out})
        self.manager = TaskManager(cfg)
        app = create_app(cfg, self.manager)
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            try:
                self.call("/api/system")
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        raise SystemExit("服务没起来")

    def stop_app(self):
        """关掉应用本身（模拟用户退出），下载源继续开着。"""
        if self.server:
            self.server.should_exit = True
        if self.manager:
            self.manager.shutdown()
        self.server = None
        self.manager = None
        time.sleep(0.3)

    def restart_app(self):
        self.port = free_port()
        self.start()

    def stop(self):
        self.stop_app()
        self.www.shutdown()
        time.sleep(0.3)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def call(self, path, payload=None):
        req = urllib.request.Request(self.base + path,
                                     method="POST" if payload is not None else "GET")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(payload).encode()
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    def task(self, task_id):
        return next((t for t in self.call("/api/tasks")["tasks"] if t["id"] == task_id), None)

    def wait(self, task_id, want, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            task = self.task(task_id)
            if task and task["status"] in want:
                return task
            time.sleep(0.15)
        raise SystemExit(f"等待 {want} 超时，最后状态：{self.task(task_id)}")

    def url(self, name):
        return f"http://127.0.0.1:{self.www_port}/{name}"


def main():
    h = Harness()
    h.start()
    results = []

    # 1. 完整下载 + 校验
    t = h.call("/api/tasks", {"url": h.url("fast.bin"), "dir": h.out, "kind": "file"})["task"]
    t = h.wait(t["id"], ("done", "error"))
    path = os.path.join(h.out, t["filename"])
    ok = t["status"] == "done" and sha(path) == hashlib.sha256(FAST).hexdigest()
    print("[1] 完整下载 + sha256", "PASS" if ok else "FAIL", t["status"], t["size"])
    results.append(("完整下载", ok))

    # 2. 暂停真的停、续传接着下、结果对
    t2 = h.call("/api/tasks", {"url": h.url("slow.bin"), "dir": h.out, "kind": "file"})["task"]
    time.sleep(2.0)
    h.call(f"/api/tasks/{t2['id']}/pause", {})
    time.sleep(1.5)
    mid = h.task(t2["id"])
    stalled_from = mid["downloaded"]
    time.sleep(1.0)
    drift = h.task(t2["id"])["downloaded"] - stalled_from
    ok2a = mid["status"] == "paused" and 0 < stalled_from < len(SLOW) and abs(drift) < 8 * 1024 * 1024
    h.call(f"/api/tasks/{t2['id']}/resume", {})
    t2 = h.wait(t2["id"], ("done", "error"))
    p2 = os.path.join(h.out, t2["filename"])
    ok2 = ok2a and t2["status"] == "done" and sha(p2) == hashlib.sha256(SLOW).hexdigest()
    print(f"[2] 暂停/续传  paused@{stalled_from}/{len(SLOW)} 漂移{drift}B", "PASS" if ok2 else "FAIL")
    results.append(("暂停+续传", ok2))

    # 3. 重启恢复：暂停中的任务，退出程序再打开，还在且能接着下
    newest = h.call("/api/tasks", {"url": h.url("slow.bin"), "dir": h.out, "kind": "file"})["task"]
    time.sleep(2.0)
    h.call(f"/api/tasks/{newest['id']}/pause", {})
    time.sleep(1.0)
    paused = h.task(newest["id"])
    h.stop_app()
    h.restart_app()                           # 同一个数据目录，新进程
    restored = h.task(newest["id"])
    ok3a = bool(restored) and restored["status"] == "paused" \
        and restored["downloaded"] >= paused["downloaded"]
    h.call(f"/api/tasks/{newest['id']}/resume", {})
    done = h.wait(newest["id"], ("done", "error"))
    p3 = os.path.join(h.out, done["filename"])
    ok3 = ok3a and done["status"] == "done" and sha(p3) == hashlib.sha256(SLOW).hexdigest()
    print("[3] 重启恢复", "PASS" if ok3 else "FAIL",
          f"暂停时 {paused['downloaded']} → 恢复后 {restored['status'] if restored else None}")
    results.append(("重启恢复", ok3))

    # 4. 同一门课重复排队必须被拦；换目录可以并存
    # 这里用不启动调度线程的 manager，免得真去连课程平台（本脚本要求不依赖外网）
    from ocemind.downloader.config import Config
    from ocemind.downloader.manager import TaskManager

    guard = TaskManager(Config(home=tempfile.mkdtemp(prefix="ocemind_e2e_guard_")),
                        start_scheduler=False)
    try:
        course = ("https://app1.h5.xiaoeknow.com/p/course/video/v_x"
                  "?alive_id=l_abcdef1234567890abcd")
        a = guard.add(course, "course", 0, h.out, 0)
        try:
            guard.add(course, "course", 0, h.out, 0)
            ok4 = False
        except ValueError:
            ok4 = True
        b = guard.add(course, "course", 0, h.out + "_2", 0)
        ok4 = ok4 and b["id"] != a["id"]
    finally:
        guard.shutdown()
    print("[4] 重复课程保护", "PASS" if ok4 else "FAIL")
    results.append(("重复课程保护", ok4))

    # 5. 没有回放 ID 的课程页链接：当场拒绝，不空跑
    guard = TaskManager(Config(home=tempfile.mkdtemp(prefix="ocemind_e2e_guard2_")),
                        start_scheduler=False)
    try:
        guard.add("https://app1.h5.xiaoeknow.com/p/course/video/v_6190abcd",
                  "course", 0, h.out, 0)
        ok5 = False
    except ValueError as exc:
        ok5 = "回放 ID" in str(exc)
    finally:
        guard.shutdown()
    print("[5] 非回放链接拒绝", "PASS" if ok5 else "FAIL")
    results.append(("非回放链接拒绝", ok5))

    h.stop()

    print("\n===== 汇总 =====")
    for name, ok in results:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{sum(1 for _, o in results if o)}/{len(results)} 通过")
    sys.exit(0 if all(o for _, o in results) else 1)


if __name__ == "__main__":
    main()
