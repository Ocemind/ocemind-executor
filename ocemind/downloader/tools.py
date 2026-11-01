"""外部工具定位：yt-dlp / ffmpeg。"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_ytdlp(explicit: str = "") -> list[str]:
    """返回可直接拼接参数的命令前缀。

    优先 `python -m yt_dlp`：`pip install yt-dlp` 生成的 `yt-dlp.exe` 是重新
    链接出来的、没有签名，在这台机器上会被智能应用控制直接拦掉；python.exe
    是签过名的，跑模块没事。设置里显式填了路径的仍以它为准。
    """
    if explicit and Path(explicit).exists():
        return [explicit]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    scripts = Path(sys.executable).parent / "Scripts" / "yt-dlp.exe"
    if scripts.exists():
        return [str(scripts)]
    return [sys.executable, "-m", "yt_dlp"]


def find_ffmpeg(explicit: str = "") -> str:
    """返回 ffmpeg 所在目录，交给 yt-dlp 的 --ffmpeg-location。"""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p.parent)
        if p.is_dir():
            return str(p)
    exe = shutil.which("ffmpeg")
    if exe:
        return str(Path(exe).parent)
    for base in (r"D:\Program Files", r"C:\Program Files", r"D:\Program Files (x86)"):
        root = Path(base)
        if not root.exists():
            continue
        for p in root.glob("ffmpeg*/bin/ffmpeg.exe"):
            return str(p.parent)
    return ""


def tool_versions(ytdlp_path: str = "", ffmpeg_path: str = "") -> dict:
    versions = {"ytdlp": "", "ffmpeg": "", "ytdlp_cmd": "", "ffmpeg_dir": ""}
    cmd = find_ytdlp(ytdlp_path)
    versions["ytdlp_cmd"] = " ".join(cmd)
    try:
        out = subprocess.run(
            cmd + ["--version"],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        versions["ytdlp"] = (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "不可用"
    except Exception as exc:  # noqa: BLE001
        versions["ytdlp"] = f"不可用: {exc}"

    ffdir = find_ffmpeg(ffmpeg_path)
    versions["ffmpeg_dir"] = ffdir
    if ffdir:
        exe = Path(ffdir) / "ffmpeg.exe"
        try:
            out = subprocess.run(
                [str(exe), "-version"],
                capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
            )
            first = (out.stdout or "").splitlines()[0]
            versions["ffmpeg"] = first.replace("ffmpeg version ", "").split()[0]
        except Exception as exc:  # noqa: BLE001
            versions["ffmpeg"] = f"不可用: {exc}"
    else:
        versions["ffmpeg"] = "未找到"
    return versions
