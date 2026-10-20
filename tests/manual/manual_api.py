"""端到端：真实起服务，走 API 添加/暂停/续传/视频下载。"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="tdmlite-home-"))
os.environ["XIAOE_DL_HOME"] = str(HOME)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402
import uvicorn  # noqa: E402

from xiaoe_dl.downloader.api import create_app  # noqa: E402
from xiaoe_dl.downloader.config import Config  # noqa: E402
from xiaoe_dl.downloader.manager import TaskManager  # noqa: E402

FILE_URL = "https://registry.npmmirror.com/-/binary/node/v20.11.0/node-v20.11.0-win-x64.zip"
VIDEO_URL = "https://www.w3schools.com/html/mov_bbb.mp4"
OUT = Path(tempfile.mkdtemp(prefix="tdmlite-out-"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_status(base, task_id, statuses, timeout=180):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        tasks = requests.get(f"{base}/api/tasks", timeout=5).json()["tasks"]
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task:
            last = task
            if task["status"] in statuses:
                return task
        time.sleep(0.4)
    raise AssertionError(f"超时，最后状态: {last}")


def main() -> int:
    cfg = Config()
    cfg.update({"defaultDir": str(OUT), "maxConcurrent": 3, "defaultConnections": 8})
    manager = TaskManager(cfg)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(
        create_app(cfg, manager), host="127.0.0.1", port=port,
        log_level="warning", access_log=False,
    ))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(f"{base}/api/system", timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.1)

    # 1. 探测
    probe = requests.post(f"{base}/api/probe", json={"url": VIDEO_URL}, timeout=120).json()
    print("probe:", probe.get("ok"), probe.get("kind"), probe.get("title"))
    assert probe.get("ok") and probe.get("kind") == "video"

    # 2. 直链多线程下载
    t = requests.post(f"{base}/api/tasks", json={
        "url": FILE_URL, "kind": "file", "dir": str(OUT), "connections": 8,
    }, timeout=10).json()["task"]
    print("file task:", t["id"], t["status"])
    done = wait_status(base, t["id"], ("done", "error"))
    print("file done:", done["status"], done["filename"], done["size"])
    assert done["status"] == "done", done
    assert (OUT / done["filename"]).stat().st_size == done["size"] == 29701389

    # 3. 视频下载
    t = requests.post(f"{base}/api/tasks", json={
        "url": VIDEO_URL, "kind": "video", "quality": 720, "dir": str(OUT),
    }, timeout=10).json()["task"]
    done = wait_status(base, t["id"], ("done", "error"), timeout=180)
    print("video done:", done["status"], done["filename"])
    assert done["status"] == "done", done
    assert Path(done["path"]).exists()

    # 4. 暂停 → 续传
    t = requests.post(f"{base}/api/tasks", json={
        "url": FILE_URL, "kind": "file", "dir": str(OUT), "connections": 4,
    }, timeout=10).json()["task"]
    deadline = time.time() + 60
    while time.time() < deadline:
        cur = next(x for x in requests.get(f"{base}/api/tasks", timeout=5).json()["tasks"]
                   if x["id"] == t["id"])
        if cur["progress"] > 5:
            break
        time.sleep(0.2)
    print("pausing at:", cur["progress"], "%")
    requests.post(f"{base}/api/tasks/{t['id']}/pause", timeout=10)
    paused = wait_status(base, t["id"], ("paused", "done", "error"), timeout=30)
    print("paused:", paused["status"], paused["progress"], "%")
    assert paused["status"] == "paused", paused

    requests.post(f"{base}/api/tasks/{t['id']}/resume", timeout=10)
    done = wait_status(base, t["id"], ("done", "error"), timeout=180)
    print("resumed done:", done["status"], done["filename"])
    assert done["status"] == "done", done
    assert (OUT / done["filename"]).stat().st_size == 29701389

    # 5. 取消 + 移除
    requests.post(f"{base}/api/tasks/{t['id']}/cancel", timeout=10)
    requests.post(f"{base}/api/tasks/{t['id']}/remove", timeout=10)
    tasks = requests.get(f"{base}/api/tasks", timeout=5).json()["tasks"]
    assert all(x["id"] != t["id"] for x in tasks)

    manager.shutdown()
    server.should_exit = True
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
