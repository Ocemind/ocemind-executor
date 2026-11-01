# -*- coding: utf-8 -*-
"""下载打包要用的 ffmpeg 到 vendor/tools/。

    python tools/fetch_vendor.py

只需要在打包机器上跑一次，文件会留在 vendor/（已在 .gitignore 里）。
之后再打包不用重下；想换新版加 `--force`。

Windows 用**共享版**（78 MB）：静态版 167 MB，打进安装包太夸张，而且
两版编出来的 ffmpeg.exe 功能一样，只是共享版把 30 MB 的 dll 摊在旁边。

yt-dlp 不用这里下：它是 Python 包，打包时由构建环境的 pip 装好的那份
直接编进 sidecar（`pip install yt-dlp`）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "tools"

UA = "ocemind-vendor/1"

#: 平台键 -> 上游地址
URLS = {
    "windows": "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
               "ffmpeg-master-latest-win64-lgpl-shared.zip",
    "linux": "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
             "ffmpeg-master-latest-linux64-lgpl.tar.xz",
}


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done * 100 // total:3d}%  {done / 1048576:.1f} MiB",
                      end="")
    print()
    tmp.replace(dest)


def _extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        return
    import tarfile

    with tarfile.open(archive) as tf:
        tf.extractall(target)


def _prune(found: Path) -> None:
    """只留 bin/：ffplay 用不到，doc/include/lib/presets 是几十 MB 的死重量。"""
    for extra in list(found.parent.parent.rglob("*")):
        if extra.is_dir() and extra.name in ("doc", "include", "lib", "presets"):
            shutil.rmtree(extra, ignore_errors=True)
    for player in found.parent.glob("ffplay*"):
        try:
            player.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="下载随包分发的 ffmpeg")
    ap.add_argument("--force", action="store_true", help="已有也重新下载")
    args = ap.parse_args(argv)

    key = platform_key()
    target = VENDOR / "ffmpeg"
    exe = "ffmpeg.exe" if key == "windows" else "ffmpeg"
    existing = next((p for p in target.rglob(exe) if p.is_file()), None) \
        if target.is_dir() else None
    if existing and not args.force:
        _prune(existing)
        print(f"[=] 已有 {existing}，换新版加 --force")
        return 0

    url = URLS.get(key)
    if not url:
        print(f"[!] 不支持的平台：{key}")
        return 1

    print(f"[*] {url}")
    suffix = ".zip" if key == "windows" else ".tar.xz"
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ("ffmpeg" + suffix)
        _download(url, archive)
        if target.exists():
            shutil.rmtree(target)
        _extract(archive, target)

    found = next((p for p in target.rglob(exe) if p.is_file()), None)
    if not found:
        print(f"[!] 解压后没找到 {exe}")
        return 1
    _prune(found)
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"[✓] {found}  （整个目录 {size / 1048576:.1f} MiB）")
    print("[*] 现在可以打包了：python tools/build_sidecar.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
