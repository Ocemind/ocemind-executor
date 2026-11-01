"""yt-dlp 引擎验证：探测 + 下载 + 进度回调。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ocemind.downloader.config import Config  # noqa: E402
from ocemind.downloader.ytdlp_engine import VideoDownloader, probe  # noqa: E402

URL = "https://www.w3schools.com/html/mov_bbb.mp4"


def main() -> int:
    cfg = Config()
    info = probe(URL, cfg)
    print("probe:", {k: info.get(k) for k in ("ok", "title", "extractor", "heights", "duration")})
    assert info["ok"], info

    tmp = Path(tempfile.mkdtemp(prefix="ocemind-video-"))
    events = []

    def on_progress(p):
        events.append(p)

    dl = VideoDownloader(cfg)
    result = dl.run(URL, tmp, 720, on_progress)
    print("result:", result)
    print("progress events:", len(events))
    if events:
        print("first:", events[0])
        print("last :", events[-1])
    assert result["ok"], result
    assert result["path"] and Path(result["path"]).exists(), "未拿到最终文件路径"
    print("file:", Path(result["path"]).name, Path(result["path"]).stat().st_size, "bytes")
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
