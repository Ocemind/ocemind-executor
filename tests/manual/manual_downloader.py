"""分片引擎验证：完整性 / 多线程与单线程一致 / 暂停续传。"""
import hashlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xiaoe_dl.downloader.http_downloader import SegmentedDownload  # noqa: E402

URL = "https://registry.npmmirror.com/-/binary/node/v20.11.0/node-v20.11.0-win-x64.zip"
SIZE = 29701389


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tdmlite-test-"))
    print("tmp:", tmp)

    t0 = time.time()
    single = SegmentedDownload(URL, tmp / "single.zip", connections=1).run()
    print(f"single: {single['ok']} {time.time() - t0:.1f}s")
    assert single["ok"], single

    t0 = time.time()
    multi = SegmentedDownload(URL, tmp / "multi.zip", connections=8).run()
    print(f"multi : {multi['ok']} {time.time() - t0:.1f}s")
    assert multi["ok"], multi

    h1, h2 = md5(tmp / "single.zip"), md5(tmp / "multi.zip")
    print("md5 equal:", h1 == h2, h1)
    assert h1 == h2, "多线程结果与单线程不一致"

    # ---- 暂停 → 续传 ----
    dest = tmp / "paused.zip"
    sidecar = tmp / "paused.zip.tdmlite.json"
    dl = SegmentedDownload(URL, dest, connections=4)
    th = threading.Thread(target=dl.run, daemon=True)
    th.start()
    deadline = time.time() + 60
    while time.time() < deadline and dl.downloaded() < 4 * 1024 * 1024:
        time.sleep(0.1)
    print(f"stop at {dl.downloaded() / 1048576:.1f} MB")
    dl.stop()
    th.join(timeout=20)
    assert not th.is_alive(), "暂停后线程未退出"

    state = json.loads(sidecar.read_text(encoding="utf-8"))
    done = sum(state["parts"].values())
    print(f"paused: {done} / {SIZE} bytes ({done * 100 / SIZE:.1f}%)")
    assert 0 < done < SIZE, f"暂停后进度异常: {done}"

    t0 = time.time()
    resumed = SegmentedDownload(URL, dest, connections=4).run()
    print(f"resume: {resumed['ok']} {time.time() - t0:.1f}s")
    assert resumed["ok"], resumed
    assert dest.stat().st_size == SIZE
    assert md5(dest) == h1, "续传后的文件损坏"
    assert not sidecar.exists(), "完成后残留 sidecar"

    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
