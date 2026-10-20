"""失败路径：404 必须报 error，不能变 paused，也不能把错误页当文件存下来。"""
import os
import sys
import tempfile
import time
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="tdmlite-home-"))
os.environ["XIAOE_DL_HOME"] = str(HOME)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xiaoe_dl.downloader.config import Config  # noqa: E402
from xiaoe_dl.downloader.http_downloader import SegmentedDownload, probe_http  # noqa: E402
from xiaoe_dl.downloader.manager import TaskManager  # noqa: E402

BAD = "https://www.example.com/this-path-does-not-exist-98765"


def main() -> int:
    cfg = Config()
    out = Path(tempfile.mkdtemp(prefix="tdmlite-err-"))
    cfg.update({"defaultDir": str(out)})

    probe = probe_http(BAD)
    print("probe_http:", probe)
    assert probe["ok"] is False, "404 不应探测成功"

    dl = SegmentedDownload(BAD, out / "bad.html", connections=4)
    result = dl.run()
    print("download:", result)
    assert result["ok"] is False
    assert result.get("cancelled") is not True, "404 被当成了暂停"
    assert not (out / "bad.html").exists(), "错误页被保存成了文件"

    mgr = TaskManager(cfg)
    task = mgr.add(BAD, "file", 0, str(out), 4)
    deadline = time.time() + 30
    while time.time() < deadline:
        cur = mgr.get(task["id"])
        if cur["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    print("manager status:", cur["status"], "|", cur["error"])
    assert cur["status"] == "error", f"应为 error，实际 {cur['status']}"
    mgr.shutdown()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
