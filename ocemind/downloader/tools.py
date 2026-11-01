"""外部工具定位：yt-dlp / ffmpeg。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ocemind import toolchain

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_ytdlp(explicit: str = "") -> list[str]:
    """返回可直接拼接参数的命令前缀。

    本机装了（PATH 上有 `yt-dlp`）就用它；没装就用编进包里的那份，由
    sidecar 自己的解释器跑（`-m yt_dlp` / `--run-ytdlp`）——`pip install
    yt-dlp` 生成的 `yt-dlp.exe` 是没有签名的，会被智能应用控制拦掉，
    走 python 解释器没事。
    """
    return toolchain.ytdlp_command(explicit)


def find_ffmpeg(explicit: str = "") -> str:
    """返回 ffmpeg 所在目录，交给 yt-dlp 的 --ffmpeg-location。"""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p.parent)
        if p.is_dir():
            return str(p)
    exe = toolchain.ffmpeg_exe()
    return str(Path(exe).parent) if exe else ""


def tool_versions(ytdlp_path: str = "", ffmpeg_path: str = "") -> dict:
    versions = {"ytdlp": "", "ffmpeg": "", "ytdlp_cmd": "", "ffmpeg_dir": "",
                "ytdlp_source": "", "ffmpeg_source": ""}
    cmd = find_ytdlp(ytdlp_path)
    versions["ytdlp_cmd"] = " ".join(cmd)
    versions["ytdlp_source"] = toolchain.resolve("ytdlp", ytdlp_path)["source"]
    try:
        out = subprocess.run(
            cmd + ["--version"],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        versions["ytdlp"] = (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "不可用"
    except Exception as exc:  # noqa: BLE001
        versions["ytdlp"] = f"不可用: {exc}"

    found = toolchain.resolve("ffmpeg", ffmpeg_path)
    versions["ffmpeg_source"] = found["source"]
    versions["ffmpeg_dir"] = str(Path(found["path"]).parent) if found["path"] else ""
    if found["path"]:
        try:
            out = subprocess.run(
                [found["path"], "-version"],
                capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
            )
            first = (out.stdout or "").splitlines()[0]
            versions["ffmpeg"] = first.replace("ffmpeg version ", "").split()[0]
        except Exception as exc:  # noqa: BLE001
            versions["ffmpeg"] = f"不可用: {exc}"
    else:
        versions["ffmpeg"] = "未找到"
    return versions
