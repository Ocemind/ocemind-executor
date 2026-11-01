"""视频任务暂停/续传：用限速把下载拉长，确保能停在中间。"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="ocemind-home-"))
os.environ["OCEMIND_HOME"] = str(HOME)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ocemind.downloader.config import Config  # noqa: E402
from ocemind.downloader.ytdlp_engine import VideoDownloader  # noqa: E402

URL = "https://www.w3schools.com/html/mov_bbb.mp4"
SIZE = 788493


def main() -> int:
    cfg = Config()
    cfg.update({"maxRateKB": 120})  # 约 6.5 秒，足够中途停

    out = Path(tempfile.mkdtemp(prefix="ocemind-vp-"))
    dl = VideoDownloader(cfg)
    box = {}
    th = threading.Thread(target=lambda: box.update(dl.run(URL, out, 720)), daemon=True)
    th.start()

    deadline = time.time() + 20
    parts = []
    while time.time() < deadline:
        parts = list(out.glob("*.part"))
        if parts and parts[0].stat().st_size > 120 * 1024:
            break
        time.sleep(0.2)
    print("part size at stop:", parts[0].stat().st_size if parts else 0)
    dl.stop()
    th.join(timeout=20)
    print("first run:", box)
    assert box.get("cancelled"), f"应当被标记为取消/暂停: {box}"

    part_size = parts[0].stat().st_size
    assert part_size < SIZE, "停得太晚，已经下完了"

    dl2 = VideoDownloader(cfg)
    cfg.update({"maxRateKB": 0})
    result = dl2.run(URL, out, 720)
    print("resume:", result)
    assert result["ok"], result
    path = Path(result["path"])
    assert path.exists()
    print("final size:", path.stat().st_size)
    assert path.stat().st_size == SIZE
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
