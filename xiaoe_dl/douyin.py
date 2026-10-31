# -*- coding: utf-8 -*-
"""抖音：分享链接 → 成片。

为什么不用 yt-dlp：抖音网页接口要 `s_v_web_id` 和 `a_bogus` 签名，
拿不到新 cookie 时必然 403 或者空列表。这里换一条路——让本机 Chrome
打开作品页，页面自己完成签名、自己拉流；我们只从网络请求里捡出视频流
和音频流的地址，再用 Range 分段下载，最后 ffmpeg 无损合流。

播放地址带签名和时效，所以解析结果只缓存 10 分钟；下载失败重试会重新解析。
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from .downloader.http_downloader import SegmentedDownload, sanitize_filename
from .paths import ffmpeg_exe as find_ffmpeg
from .paths import find_chrome

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
REFERER = "https://www.douyin.com/"

#: 分享短链、网页、移动页都算，用户从哪个端复制来的都能接
_HOST_ROOTS = ("douyin.com", "iesdouyin.com")
_ID_RE = re.compile(r"/(?:share/)?(?:video|note|slides)/(\d{6,})")

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 600.0

#: 短链解析结果短缓存：探测框每输入一下就请求一次，别把抖音当压力测试
_RESOLVE_CACHE: dict[str, tuple[float, dict]] = {}
_RESOLVE_TTL = 120.0


# ------------------------------------------------------------------ 识别

def is_douyin_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return any(host == root or host.endswith("." + root) for root in _HOST_ROOTS)


def parse_aweme_id(url: str) -> str:
    match = _ID_RE.search(urlparse(url or "").path)
    return match.group(1) if match else ""


# ------------------------------------------------------------------ 解析链接

def resolve_share(url: str, timeout: float = 20) -> dict:
    """把短链跟到作品页，取出 aweme_id。

    抖音偶尔会直接掐连接（ConnectionReset），这是常态，重试三次足够。
    """
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    aweme_id = parse_aweme_id(raw)
    if aweme_id:
        return {"ok": True, "aweme_id": aweme_id, "final_url": raw}

    now = time.time()
    with _CACHE_LOCK:
        hit = _RESOLVE_CACHE.get(raw)
        if hit and now - hit[0] <= _RESOLVE_TTL:
            return dict(hit[1])

    last = ""
    for attempt in range(3):
        try:
            resp = requests.get(
                raw, headers={"User-Agent": UA, "Accept": "text/html,*/*"},
                timeout=timeout, allow_redirects=True)
            aweme_id = parse_aweme_id(resp.url) or parse_aweme_id(resp.text[:8000])
            if aweme_id:
                result = {"ok": True, "aweme_id": aweme_id, "final_url": resp.url}
                with _CACHE_LOCK:
                    _RESOLVE_CACHE[raw] = (time.time(), dict(result))
                    if len(_RESOLVE_CACHE) > 128:
                        _RESOLVE_CACHE.clear()
                return result
            last = f"HTTP {resp.status_code}，链接里没有作品 ID"
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": f"打开分享链接失败: {last}"}


# ------------------------------------------------------------------ 浏览器解析

def playwright_available() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def _launch(pw, proxies: dict | None = None):
    args = [
        "--disable-blink-features=AutomationControlled",
        "--autoplay-policy=no-user-gesture-required",
        "--mute-audio",
    ]
    proxy = None
    if proxies:
        server = proxies.get("https") or proxies.get("http")
        if server:
            proxy = {"server": server}

    chrome = find_chrome()
    attempts: list[dict] = []
    if chrome:
        attempts.append({"channel": "chrome"})
        attempts.append({"executable_path": chrome})
    attempts.append({})

    last: Exception | None = None
    for kwargs in attempts:
        try:
            return pw.chromium.launch(headless=True, args=args, proxy=proxy, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"启动浏览器失败: {last}")


class _Collector:
    """从播放器的网络请求里捡视频流 / 音频流地址。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._urls: dict[str, dict[str, str]] = {"video": {}, "audio": {}}

    def on_request(self, request) -> None:
        url = request.url or ""
        if "/video/tos/" not in url:
            return
        path = urlparse(url).path.lower()
        if "media-audio" in path:
            kind = "audio"
        elif "media-video" in path:
            kind = "video"
        else:
            return
        with self._lock:
            # 同一个流会带不同的临时签名参数，按 path 去重，保留最后一条
            self._urls[kind][path] = url

    def has(self, kind: str) -> bool:
        with self._lock:
            return bool(self._urls[kind])

    def pick(self, kind: str) -> str:
        with self._lock:
            urls = list(self._urls[kind].values())
        if not urls:
            return ""
        # 优先 avc1（兼容性最好），同编码里挑码率最高的
        return max(urls, key=_score)


def _bitrate(url: str) -> int:
    query = parse_qs(urlparse(url).query)
    try:
        return int((query.get("br") or ["0"])[0] or 0)
    except (TypeError, ValueError):
        return 0


def _score(url: str) -> tuple[int, int]:
    path = urlparse(url).path.lower()
    return (1 if "avc1" in path else 0, _bitrate(url))


_META_JS = """
() => {
  const v = document.querySelector('video');
  const meta = (n) => (document.querySelector(`meta[property="${n}"]`)
                       || document.querySelector(`meta[name="${n}"]`) || {}).content || '';
  let duration = 0;
  try { if (v && Number.isFinite(v.duration)) duration = v.duration; } catch (e) {}
  return {title: document.title || '', desc: meta('description'),
          image: meta('og:image'), poster: (v && v.poster) || '',
          duration: duration};
}
"""


def _clean_title(raw: str, aweme_id: str) -> str:
    title = re.sub(r"\s*-\s*抖音.*$", "", (raw or "").strip()).strip()
    title = re.sub(r"^抖音\s*[-—]\s*", "", title).strip()
    return title or f"douyin_{aweme_id}"


def extract(aweme_id: str, page_url: str = "", timeout: float = 30,
            proxies: dict | None = None, should_stop=None) -> dict:
    """用本机 Chrome 打开作品页，拿标题、时长和两条流的地址。"""
    if not playwright_available():
        return {"ok": False,
                "error": "抖音解析需要 playwright：pip install playwright"}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"加载 playwright 失败: {exc}"}

    target = page_url or f"https://www.douyin.com/video/{aweme_id}"
    collector = _Collector()
    started = time.time()
    final_url = target
    try:
        with sync_playwright() as pw:
            browser = _launch(pw, proxies)
            try:
                ctx = browser.new_context(locale="zh-CN")
                page = ctx.new_page()
                page.on("request", collector.on_request)
                page.goto(target, wait_until="domcontentloaded", timeout=45000)
                deadline = started + timeout
                nudged = False
                while time.time() < deadline:
                    if should_stop and should_stop():
                        return {"ok": False, "cancelled": True, "error": ""}
                    page.wait_for_timeout(600)
                    if not nudged and time.time() - started > 2:
                        nudged = True
                        # 静音自动播放通常被允许；万一页面没自动拉流，手动推一下
                        try:
                            page.evaluate(
                                "() => { const v = document.querySelector('video');"
                                " if (v) { v.muted = true; v.play && v.play().catch(() => {}); } }")
                        except Exception:  # noqa: BLE001
                            pass
                    if collector.has("video") and (
                            collector.has("audio") or time.time() - started > 12):
                        break
                final_url = page.url or target
                meta = page.evaluate(_META_JS)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False,
                "error": f"打开抖音页面失败: {type(exc).__name__}: {exc}"}

    video_url = collector.pick("video")
    audio_url = collector.pick("audio")
    if not video_url:
        hint = "（页面停在验证页）" if "verify" in final_url else ""
        return {"ok": False,
                "error": f"没从播放器里拿到视频流{hint}，可能是图文作品或页面没加载完"}

    title = _clean_title((meta or {}).get("title") or "", aweme_id)
    if title == f"douyin_{aweme_id}":
        desc = ((meta or {}).get("desc") or "").split("\n")[0]
        title = _clean_title(desc, aweme_id)

    info = {
        "ok": True,
        "aweme_id": aweme_id,
        "page_url": final_url,
        "title": title,
        "duration": float((meta or {}).get("duration") or 0.0),
        "thumbnail": (meta or {}).get("image") or (meta or {}).get("poster") or "",
        "video_url": video_url,
        "audio_url": audio_url,
        "has_audio": bool(audio_url),
    }
    _remember(aweme_id, info)
    return info


def _remember(aweme_id: str, info: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[aweme_id] = (time.time(), dict(info))
        # 别让缓存无限长；本来也就几十条
        if len(_CACHE) > 64:
            oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])
            for key, _ in oldest[:16]:
                _CACHE.pop(key, None)


def _recall(aweme_id: str) -> dict | None:
    with _CACHE_LOCK:
        item = _CACHE.get(aweme_id)
        if item and time.time() - item[0] <= _CACHE_TTL:
            return dict(item[1])
    return None


def extract_cached(aweme_id: str, **kwargs) -> dict:
    cached = _recall(aweme_id)
    if cached:
        return cached
    return extract(aweme_id, **kwargs)


# ------------------------------------------------------------------ 探测

def probe_url(url: str) -> dict:
    """给 /api/probe 用的轻量探测：只跟短链，不启动浏览器。

    标题和时长留空，下载开始时由 extract() 补上——探测框每输入一下就
    触发，不能在这里等十几秒的浏览器启动。
    """
    resolved = resolve_share(url)
    if not resolved.get("ok"):
        return {"ok": False, "error": resolved.get("error") or "解析失败"}
    aweme_id = resolved["aweme_id"]
    cached = _recall(aweme_id) or {}
    return {
        "ok": True,
        "kind": "video",
        "title": cached.get("title") or "",
        "id": aweme_id,
        "uploader": "",
        "duration": cached.get("duration"),
        "thumbnail": cached.get("thumbnail") or "",
        "extractor": "抖音",
        "webpage_url": resolved["final_url"],
        "is_playlist": False,
        "entry_count": 1,
        # 不提供画质选项：只取播放器实际拉到的最高流，列出来也选不了
        "heights": [],
    }


# ------------------------------------------------------------------ 下载

def download_url(url: str, out_dir, connections: int = 8, rate_bytes: int = 0,
                 proxies: dict | None = None, on_progress=None,
                 should_stop=None) -> dict:
    """分享链接 → 成片。返回值与 yt-dlp 引擎对齐（ok/path/error/title）。"""
    resolved = resolve_share(url)
    if not resolved.get("ok"):
        return {"ok": False, "error": resolved.get("error") or "解析失败"}
    aweme_id = resolved["aweme_id"]

    info = _recall(aweme_id)
    if not info:
        if on_progress:
            on_progress({"status": "parsing"})
        info = extract(aweme_id, page_url="", proxies=proxies,
                       should_stop=should_stop)
    if info.get("cancelled"):
        return {"ok": False, "cancelled": True, "error": ""}
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error") or "解析失败"}
    return download_media(
        info, out_dir, connections=connections, rate_bytes=rate_bytes,
        proxies=proxies, on_progress=on_progress, should_stop=should_stop)


def download_media(info: dict, out_dir, connections: int = 8,
                   rate_bytes: int = 0, proxies: dict | None = None,
                   on_progress=None, should_stop=None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    aweme_id = str(info.get("aweme_id") or "douyin")
    title = info.get("title") or f"douyin_{aweme_id}"
    stem = sanitize_filename(f"{title} [{aweme_id}]")[:120]
    dest = _unique_path(out_dir / f"{stem}.mp4")

    work = out_dir / f".douyin_{aweme_id}"
    work.mkdir(parents=True, exist_ok=True)
    video_path = work / "video.mp4"
    audio_path = work / "audio.m4a"

    headers = {"Referer": REFERER, "Origin": "https://www.douyin.com"}
    streams = [("video", info["video_url"], video_path)]
    if info.get("audio_url"):
        streams.append(("audio", info["audio_url"], audio_path))

    engines: list[SegmentedDownload] = []
    for label, stream_url, path in streams:
        engine = SegmentedDownload(
            url=stream_url, dest=path, connections=connections,
            rate_bytes=rate_bytes, headers=headers, proxies=proxies,
        )
        try:
            engine.prepare()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{label} 流探测失败: {exc}"}
        engines.append(engine)

    total = sum(engine.total for engine in engines) or 0
    base = 0
    for engine in engines:
        offset = base

        def report(done: int, offset: int = offset) -> None:
            if on_progress:
                on_progress({
                    "status": "downloading",
                    "downloaded": offset + done,
                    "total": total,
                    "filename": dest.name,
                })

        result = _run_stream(engine, report, should_stop)
        if result.get("cancelled"):
            return {"ok": False, "cancelled": True, "error": ""}
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "下载失败"}
        base += engine.total
        report(engine.downloaded())

    if info.get("audio_url"):
        if on_progress:
            on_progress({"status": "merging"})
        merged = _merge(video_path, audio_path, dest)
        if not merged.get("ok"):
            return merged
    else:
        try:
            os.replace(video_path, dest)
        except OSError as exc:
            return {"ok": False, "error": f"写入成片失败: {exc}"}

    shutil.rmtree(work, ignore_errors=True)
    try:
        size = dest.stat().st_size
    except OSError:
        size = 0
    if on_progress:
        on_progress({"status": "downloading", "downloaded": size,
                     "total": size, "filename": dest.name})
    return {"ok": True, "path": str(dest), "title": title,
            "duration": info.get("duration") or 0,
            "thumbnail": info.get("thumbnail") or "", "size": size}


def _run_stream(engine: SegmentedDownload, report, should_stop) -> dict:
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


def _merge(video: Path, audio: Path, dest: Path) -> dict:
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


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
