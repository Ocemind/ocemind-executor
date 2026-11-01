"""yt-dlp 封装：探测视频信息 + 按画质下载。"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from ocemind import bilibili, douyin, ytdlp_updater

from . import tools

CREATE_NO_WINDOW = tools.CREATE_NO_WINDOW

HEIGHT_LABEL = {
    4320: "8K", 2160: "4K", 1440: "2K", 1080: "1080P", 720: "720P",
    576: "576P", 480: "480P", 360: "360P", 240: "240P",
}
DEFAULT_HEIGHTS = [2160, 1440, 1080, 720, 480, 360]


def _base_cmd(cfg, *, need_ffmpeg: bool = False) -> list[str]:
    cmd = tools.find_ytdlp(cfg.get("ytdlpPath") or "")
    proxies = cfg.proxies()
    if proxies:
        cmd += ["--proxy", proxies["https"]]
    if need_ffmpeg:
        ffdir = tools.find_ffmpeg(cfg.get("ffmpegPath") or "")
        if ffdir:
            cmd += ["--ffmpeg-location", ffdir]
    cookie = cfg.get("cookieBrowser") or ""
    if cookie:
        cmd += ["--cookies-from-browser", cookie]
    return cmd


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 用户在设置页更新过 yt-dlp 的话，子进程优先用新的那份
    return ytdlp_updater.apply_env(env)


def probe(url: str, cfg) -> dict:
    if douyin.is_douyin_url(url):
        # yt-dlp 解析抖音必然被签名挡住；这里只跟短链，不动浏览器
        return douyin.probe_url(url)
    if bilibili.is_bilibili_url(url):
        # 作品页现在对普通请求回 412，走接口拿标题和画质
        return bilibili.probe_url(url)
    cmd = _base_cmd(cfg) + [
        "-J", "--flat-playlist", "--playlist-items", "1", "--no-warnings", url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, creationflags=CREATE_NO_WINDOW, env=_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "解析超时"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0 or not proc.stdout.strip():
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"ok": False, "error": err[-1] if err else "解析失败"}

    try:
        info = json.loads(proc.stdout)
    except ValueError:
        return {"ok": False, "error": "解析结果不是有效 JSON"}

    is_playlist = info.get("_type") == "playlist"
    entries = info.get("entries") or []
    heights: set[int] = set()
    for fmt in info.get("formats") or []:
        if (fmt.get("vcodec") or "none") != "none" and fmt.get("height"):
            heights.add(int(fmt["height"]))
    if not heights:
        heights = set(DEFAULT_HEIGHTS)

    return {
        "ok": True,
        "title": info.get("title") or "",
        "id": info.get("id") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail") or "",
        "extractor": info.get("extractor_key") or info.get("extractor") or "",
        "webpage_url": info.get("webpage_url") or url,
        "is_playlist": is_playlist,
        "entry_count": len(entries) if is_playlist else 1,
        "heights": sorted(heights, reverse=True),
    }


def format_selector(height: int) -> str:
    if not height or height <= 0:
        return "bv*+ba/b"
    h = int(height)
    return (
        f"bv*[height<={h}]+ba/b[height<={h}]/"
        f"bv*[height>={h}]+ba/bv*+ba/b"
    )


class VideoDownloader:
    """一次 yt-dlp 下载任务，支持中途停止。"""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self._stopped = False
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self, url: str, out_dir: Path, height: int, on_progress=None) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        if douyin.is_douyin_url(url):
            return self._run_douyin(url, out_dir, on_progress)
        if bilibili.is_bilibili_url(url):
            return self._run_bilibili(url, out_dir, height, on_progress)
        cmd = _base_cmd(self.cfg, need_ffmpeg=True) + [
            "-f", format_selector(height),
            "--newline", "--no-color", "--no-mtime", "--no-warnings",
            "--windows-filenames", "--trim-filenames", "150",
            "--concurrent-fragments", "4",
            "--progress",
            "--progress-delta", "0.3",
            # 注意：开头 "download:" 是 yt-dlp 的类型前缀（会被吃掉），
            # 真正输出到 stdout 的字面前缀是 "DL:"
            "--progress-template", "download:DL:%(progress)j",
            "--print", "after_move:filepath",
            "-P", str(out_dir),
            "-o", "%(title)s [%(id)s].%(ext)s",
        ]
        rate_kb = int(self.cfg.get("maxRateKB") or 0)
        if rate_kb > 0:
            cmd += ["--limit-rate", f"{rate_kb}K"]
        cmd.append(url)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=CREATE_NO_WINDOW, env=_env(),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"启动 yt-dlp 失败: {exc}"}

        with self._lock:
            self.proc = proc

        final_path = ""
        error = ""
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("DL:"):
                payload = line[len("DL:"):]
                try:
                    prog = json.loads(payload)
                except ValueError:
                    continue
                if on_progress:
                    on_progress({
                        "status": prog.get("status"),
                        "downloaded": prog.get("downloaded_bytes") or 0,
                        "total": prog.get("total_bytes") or prog.get("total_bytes_estimate") or 0,
                        "speed": prog.get("speed"),
                        "eta": prog.get("eta"),
                        "fragment_index": prog.get("fragment_index"),
                        "fragment_count": prog.get("fragment_count"),
                        "filename": prog.get("filename"),
                        "tmpfilename": prog.get("tmpfilename"),
                    })
            elif "[Merger] Merging formats into" in line:
                if on_progress:
                    on_progress({"status": "merging"})
            elif line.startswith("ERROR:") or "ERROR:" in line:
                error = line.replace("ERROR:", "", 1).strip()
            elif not line.startswith("[") and line.strip():
                # --print after_move:filepath 的输出就是一个纯路径
                candidate = line.strip()
                if candidate[1:3] == ":\\" or candidate.startswith("/"):
                    final_path = candidate

        code = proc.wait()
        with self._lock:
            self.proc = None

        if self._stopped:
            return {"ok": False, "cancelled": True, "error": error or ""}
        if code != 0:
            return {"ok": False, "error": error or f"yt-dlp 退出码 {code}"}

        if not final_path:
            # 兜底：取输出目录里最新的文件
            try:
                files = [p for p in out_dir.iterdir() if p.is_file()]
                final_path = str(max(files, key=lambda p: p.stat().st_mtime)) if files else ""
            except OSError:
                final_path = ""
        return {"ok": True, "path": final_path}

    def _run_douyin(self, url: str, out_dir: Path, on_progress=None) -> dict:
        """抖音走浏览器解析 + DASH 双流合并；画质只能取最高，height 参数不适用。"""
        def report(event: dict) -> None:
            if not on_progress:
                return
            if event.get("status") == "merging":
                on_progress({"status": "merging"})
                return
            on_progress({
                "status": "downloading",
                "downloaded": event.get("downloaded") or 0,
                "total": event.get("total") or 0,
                "filename": event.get("filename") or "",
            })

        rate_kb = int(self.cfg.get("maxRateKB") or 0)
        return douyin.download_url(
            url, out_dir,
            connections=int(self.cfg.get("defaultConnections") or 8),
            rate_bytes=rate_kb * 1024 if rate_kb > 0 else 0,
            proxies=self.cfg.proxies(),
            on_progress=report,
            should_stop=lambda: self._stopped,
        )

    def _run_bilibili(self, url: str, out_dir: Path, height: int,
                      on_progress=None) -> dict:
        """B 站走接口解析；未登录一般给 720P 单文件，登录后是 DASH 双流。"""
        def report(event: dict) -> None:
            if not on_progress:
                return
            if event.get("status") == "merging":
                on_progress({"status": "merging"})
                return
            on_progress({
                "status": "downloading",
                "downloaded": event.get("downloaded") or 0,
                "total": event.get("total") or 0,
                "filename": event.get("filename") or "",
            })

        rate_kb = int(self.cfg.get("maxRateKB") or 0)
        return bilibili.download_url(
            url, out_dir, height=height,
            connections=int(self.cfg.get("defaultConnections") or 8),
            rate_bytes=rate_kb * 1024 if rate_kb > 0 else 0,
            proxies=self.cfg.proxies(),
            on_progress=report,
            should_stop=lambda: self._stopped,
        )
