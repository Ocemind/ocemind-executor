# -*- coding: utf-8 -*-
"""分片下载的公共小件：ffmpeg 合流、时长探测。抖音和 B 站都用。"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

from ocemind.paths import ffmpeg_exe as find_ffmpeg


def run_stream(engine, report, should_stop) -> dict:
    """在后台线程里跑一条流，主线程负责进度上报和中断。"""
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(engine.run()),
                              daemon=True)
    thread.start()
    while thread.is_alive():
        if should_stop and should_stop():
            engine.stop()
        report(engine.downloaded())
        time.sleep(0.4)
    thread.join()
    return result or {"ok": False, "error": "下载线程没有返回结果"}


def merge_av(video: Path, audio: Path, dest: Path) -> dict:
    """把画面流和声音流无损拼成一个 mp4。"""
    ff = find_ffmpeg()
    if not ff:
        return {"ok": False, "error": "没有 ffmpeg，视频和音频合不了"}
    base = [ff, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-i", str(audio),
            "-map", "0:v:0?", "-map", "1:a:0?", "-c", "copy",
            "-movflags", "+faststart", str(dest)]
    try:
        proc = subprocess.run(base, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"合并失败: {exc}"}
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
        return {"ok": True}

    # 音轨不是 aac 时 -c copy 进不了 mp4，重编码一次音频
    retry = [ff, "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(video), "-i", str(audio),
             "-map", "0:v:0?", "-map", "1:a:0?", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(dest)]
    try:
        proc2 = subprocess.run(retry, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"合并失败: {exc}"}
    if proc2.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        detail = (proc2.stderr or proc.stderr or "").strip().splitlines()
        return {"ok": False, "error": f"ffmpeg 合并失败: {detail[-1] if detail else proc2.returncode}"}
    return {"ok": True}


def probe_duration(path: Path) -> float:
    """成片的真实时长。播放器 / 接口读数不可靠时以文件为准。"""
    ff = find_ffmpeg()
    if not ff:
        return 0.0
    try:
        proc = subprocess.run([ff, "-hide_banner", "-i", str(path)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except Exception:  # noqa: BLE001
        return 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 1)
