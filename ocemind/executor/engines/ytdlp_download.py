"""yt-dlp 封装：平台视频下载。

移植自 ocemind 的 `downloader/ytdlp_engine.py`，去掉 Windows 专用的
CREATE_NO_WINDOW，保留可中断与进度 JSON 解析。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from ocemind import douyin
from ocemind.links import first_url

from .base import Engine, cancelled, fail, ok

HEIGHT_LABEL = {
    4320: "8K", 2160: "4K", 1440: "2K", 1080: "1080P", 720: "720P",
    576: "576P", 480: "480P", 360: "360P", 240: "240P", 144: "144P",
}
# 0 = 最佳
DEFAULT_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]


def _ytdlp_cmd(cfg) -> list[str]:
    configured = (cfg.get("ytdlpPath") or "").strip()
    if configured and Path(configured).exists():
        return [configured]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return ["python3", "-m", "yt_dlp"]


def _base_cmd(cfg, *, need_ffmpeg: bool = False) -> list[str]:
    cmd = _ytdlp_cmd(cfg)
    proxies = cfg.proxies()
    if proxies:
        cmd += ["--proxy", proxies["https"]]
    if need_ffmpeg:
        from ocemind.paths import ffmpeg_exe as find_ffmpeg

        ff = cfg.get("ffmpegPath") or find_ffmpeg()
        if ff and Path(ff).exists():
            cmd += ["--ffmpeg-location", str(Path(ff).parent)]
    browser = cfg.get("cookieBrowser") or ""
    if browser:
        cmd += ["--cookies-from-browser", browser]
    return cmd


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def probe(url: str, cfg) -> dict:
    if douyin.is_douyin_url(url):
        return douyin.probe_url(url)
    cmd = _base_cmd(cfg) + [
        "-J", "--flat-playlist", "--playlist-items", "1", "--no-warnings", url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=90,
                              env=_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "解析超时"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0 or not proc.stdout.strip():
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"ok": False, "error": lines[-1] if lines else "解析失败"}
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        return {"ok": False, "error": "解析结果不是有效 JSON"}

    heights: set[int] = set()
    for fmt in info.get("formats") or []:
        if (fmt.get("vcodec") or "none") != "none" and fmt.get("height"):
            heights.add(int(fmt["height"]))

    return {
        "ok": True,
        "title": info.get("title") or "",
        "id": info.get("id") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail") or "",
        "extractor": info.get("extractor_key") or "",
        "webpage_url": info.get("webpage_url") or url,
        "is_playlist": info.get("_type") == "playlist",
        "heights": sorted(heights, reverse=True) or DEFAULT_HEIGHTS,
    }


def format_selector(height: int) -> str:
    if not height or height <= 0:
        return "bv*+ba/b"
    h = int(height)
    return (f"bv*[height<={h}]+ba/b[height<={h}]/"
            f"bv*[height>={h}]+ba/bv*+ba/b")


class YtdlpDownload(Engine):
    id = "download.platform"
    kinds = ("download",)

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._stopped = False
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self, ctx) -> dict:
        raw = ctx.inputs.get("url", "")
        if not raw:
            return fail("缺少链接")
        url = first_url(raw) or raw
        out_dir = Path(ctx.params.get("dir") or ctx.config.get("defaultDir")
                       or (ctx.workdir / "media"))
        out_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._stopped = False
        if douyin.is_douyin_url(url):
            return self._run_douyin(ctx, url, out_dir)
        height = int(ctx.params.get("height") or 0)

        cmd = _base_cmd(ctx.config, need_ffmpeg=True) + [
            "-f", format_selector(height),
            "--newline", "--no-color", "--no-mtime", "--no-warnings",
            "--trim-filenames", "150",
            "--concurrent-fragments", "4",
            "--progress", "--progress-delta", "0.3",
            # "download:" 是 yt-dlp 的类型前缀，会被吃掉；真正输出的是 "DL:"
            "--progress-template", "download:DL:%(progress)j",
            "--print", "after_move:filepath",
            "-P", str(out_dir),
            "-o", "%(title).80s [%(id)s].%(ext)s",
        ]
        rate = int(ctx.config.get("maxRateKB") or 0)
        if rate > 0:
            cmd += ["--limit-rate", f"{rate}K"]
        cmd.append(url)

        ctx.progress(None, "解析中")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace",
                                    bufsize=1, env=_env())
        except Exception as exc:  # noqa: BLE001
            return fail(f"启动 yt-dlp 失败: {exc}")

        with self._lock:
            self._proc = proc

        final_path = ""
        error = ""
        assert proc.stdout is not None
        for raw in proc.stdout:
            if ctx.should_stop():
                proc.terminate()
                break
            line = raw.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("DL:"):
                try:
                    prog = json.loads(line[3:])
                except ValueError:
                    continue
                total = prog.get("total_bytes") or prog.get("total_bytes_estimate") or 0
                done = prog.get("downloaded_bytes") or 0
                pct = round(min(100.0, done * 100.0 / total), 2) if total else None
                ctx.progress(pct, "下载中")
            elif "[Merger] Merging formats into" in line:
                ctx.progress(None, "合并中")
            elif "ERROR:" in line:
                error = line.replace("ERROR:", "", 1).strip()
            elif not line.startswith("[") and line.strip():
                cand = line.strip()
                if cand.startswith("/") or cand[1:3] == ":\\":
                    final_path = cand

        code = proc.wait()
        with self._lock:
            self._proc = None

        if ctx.should_stop():
            return cancelled()
        if code != 0:
            return fail(error or f"yt-dlp 退出码 {code}")
        if not final_path or not Path(final_path).exists():
            files = [p for p in out_dir.iterdir() if p.is_file()]
            final_path = str(max(files, key=lambda p: p.stat().st_mtime)) if files else ""
        if not final_path:
            return fail("yt-dlp 没有产出文件")
        return ok(final_path)

    def _run_douyin(self, ctx, url: str, out_dir: Path) -> dict:
        """抖音走浏览器解析 + 双流合并；画质固定为播放器拉到的最高流。"""
        def on_progress(event: dict) -> None:
            if event.get("status") == "merging":
                ctx.progress(None, "合并中")
                return
            total = event.get("total") or 0
            done = event.get("downloaded") or 0
            pct = round(min(100.0, done * 100.0 / total), 2) if total else None
            ctx.progress(pct, "下载中")

        rate = int(ctx.config.get("maxRateKB") or 0)
        result = douyin.download_url(
            url, out_dir,
            connections=int(ctx.params.get("connections")
                            or ctx.config.get("defaultConnections") or 8),
            rate_bytes=rate * 1024 if rate > 0 else 0,
            proxies=ctx.config.proxies(),
            on_progress=on_progress,
            should_stop=lambda: self._stopped or ctx.should_stop(),
        )
        if result.get("cancelled"):
            return cancelled()
        if not result.get("ok"):
            return fail(result.get("error") or "抖音下载失败")
        return ok(result["path"])
