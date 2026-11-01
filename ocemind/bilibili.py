# -*- coding: utf-8 -*-
"""B 站：作品页打不开就走接口。

2026-11 实测：`www.bilibili.com/video/...` 这个作品页对本机线路直接回
HTTP 412，yt-dlp 在「打开网页」那一步就死了——连解析都没开始。手机页和
接口都正常。CR TubeGet 这类工具本来就不抓网页：直接调
`x/web-interface/view` 拿 cid，再调 `x/player/playurl` 拿流地址，这里照
这条路做。

接口不用签名，但要先访问一次首页拿到 `buvid3`，否则会被当成机器人回 412
或空响应。登录态决定画质上限：没登录一般到 720P（给的是自带音轨的单文件
mp4），登录后 DASH 才给 1080P 以上；两种都能下，DASH 那两条流用 ffmpeg
无损合流。播放地址有时效，解析结果缓存 10 分钟。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from .downloader.http_downloader import SegmentedDownload, sanitize_filename
from .downloader.streams import merge_av, probe_duration, run_stream

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

_HOST_ROOTS = ("bilibili.com", "b23.tv", "bili2233.cn")
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"/video/av(\d+)", re.I)

#: 期望高度 → 接口的 qn（画质档位）。没登录时接口会自己降档。
_QN_BY_HEIGHT = {2160: 120, 1440: 116, 1080: 80, 720: 64, 480: 32, 360: 16, 240: 6}

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 600.0

#: 短链还原结果短缓存，探测框每输入一下就请求一次
_RESOLVE_CACHE: dict[str, tuple[float, dict]] = {}
_RESOLVE_TTL = 120.0


# ------------------------------------------------------------------ 识别

def is_bilibili_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return any(host == root or host.endswith("." + root) for root in _HOST_ROOTS)


def parse_video_id(text: str) -> tuple[str, str]:
    """从链接或页面文本里认出 (bvid|aid, 编号)。"""
    match = _BV_RE.search(text or "")
    if match:
        return "bvid", match.group(1)
    match = _AV_RE.search(text or "")
    if match:
        return "aid", match.group(1)
    return "", ""


def _page_index(url: str) -> int:
    query = parse_qs(urlparse(url or "").query)
    try:
        return max(1, int((query.get("p") or ["1"])[0]))
    except (TypeError, ValueError):
        return 1


# ------------------------------------------------------------------ 会话

def _session(proxies: dict | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": REFERER})
    if proxies:
        session.proxies.update(proxies)
    # 先拿 buvid3：没有这条 cookie，接口会被风控挡在门外
    try:
        session.get("https://www.bilibili.com/", timeout=10)
    except requests.RequestException:
        pass
    return session


def _get_json(session: requests.Session, url: str, params: dict,
              timeout: float = 15) -> dict:
    last = ""
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            ctype = (resp.headers.get("content-type") or "").lower()
            if resp.status_code == 200 and "json" in ctype:
                try:
                    payload = resp.json()
                except ValueError:
                    last = "接口返回的不是 JSON"
                else:
                    if payload.get("code") == 0:
                        return payload.get("data") or {}
                    last = (f"接口返回 code={payload.get('code')}: "
                            f"{payload.get('message') or ''}").strip()
            elif resp.status_code in (403, 412):
                last = "接口被风控挡住了（HTTP 412），稍后再试或换个网络"
            else:
                last = f"HTTP {resp.status_code}"
        if attempt < 2:
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(last or "接口没有返回")


# ------------------------------------------------------------------ 解析链接

def resolve_share(url: str, timeout: float = 20) -> dict:
    """b23.tv 短链跟到作品页；普通链接直接认编号，不发请求。"""
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    kind, vid = parse_video_id(raw)
    if vid:
        return {"ok": True, "kind": kind, "id": vid, "final_url": raw}

    now = time.time()
    with _CACHE_LOCK:
        hit = _RESOLVE_CACHE.get(raw)
        if hit and now - hit[0] <= _RESOLVE_TTL:
            return dict(hit[1])

    last = ""
    for attempt in range(3):
        try:
            resp = requests.get(raw, headers={"User-Agent": UA, "Referer": REFERER},
                                timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            kind, vid = parse_video_id(resp.url)
            if not vid:
                kind, vid = parse_video_id(resp.text[:20000])
            if vid:
                result = {"ok": True, "kind": kind, "id": vid,
                          "final_url": resp.url or raw}
                with _CACHE_LOCK:
                    _RESOLVE_CACHE[raw] = (time.time(), dict(result))
                    if len(_RESOLVE_CACHE) > 128:
                        _RESOLVE_CACHE.clear()
                return result
            last = f"HTTP {resp.status_code}，链接里没有作品编号"
        if attempt < 2:
            time.sleep(1.0 * (attempt + 1))
    return {"ok": False, "error": f"打开 B 站链接失败: {last}"}


# ------------------------------------------------------------------ 接口解析

def _qn_for(height: int) -> int:
    """要 900P 这种不存在的档位时，往相邻的上一档要，别降档。"""
    if not height or height <= 0:
        return 127
    for known in sorted(_QN_BY_HEIGHT):
        if height <= known:
            return _QN_BY_HEIGHT[known]
    return _QN_BY_HEIGHT[2160]


def _codec_rank(stream: dict) -> int:
    """同高度优先 avc（兼容性好），其次 hevc，最后 av1。"""
    codec = stream.get("codecid")
    return {7: 3, 12: 2, 13: 1}.get(codec, 0)


def _pick_dash(data: dict) -> dict | None:
    dash = data.get("dash") or {}
    videos = dash.get("video") or []
    if not videos:
        return None
    video = max(videos, key=lambda s: (
        s.get("height") or 0, _codec_rank(s), s.get("bandwidth") or 0))
    audios = list(dash.get("audio") or [])
    dolby = (dash.get("dolby") or {}).get("audio")
    if dolby:
        audios.append(dolby)
    audio = max(audios, key=lambda s: s.get("bandwidth") or 0) if audios else None
    audio_url = (audio or {}).get("baseUrl") or (audio or {}).get("base_url") or ""
    return {
        "video_url": video.get("baseUrl") or video.get("base_url") or "",
        "audio_url": audio_url,
        "merged": not audio_url,
        "height": int(video.get("height") or 0),
    }


def _pick_single(data: dict) -> dict | None:
    """低画质时接口给的是一条自带音轨的 mp4（durl 只有一段）。"""
    segments = data.get("durl") or []
    if len(segments) != 1:
        return None
    url = segments[0].get("url") or ""
    if not url:
        return None
    height = 0
    # 接口的格式名是 mp4720 / flv720 这种：前缀是容器名，末尾三位才是高度
    match = re.search(r"(\d{3})$", str(data.get("format") or ""))
    if match:
        height = int(match.group(1))
    return {"video_url": url, "audio_url": "", "merged": True, "height": height}


def extract(url: str, height: int = 0, proxies: dict | None = None) -> dict:
    resolved = resolve_share(url)
    if not resolved.get("ok"):
        return resolved
    kind, vid = resolved["kind"], resolved["id"]
    final_url = resolved.get("final_url") or url
    page = _page_index(final_url)
    if page == 1:
        # 短链跳转后可能把 ?p= 丢了，再看一眼用户粘的原文
        page = _page_index(url)

    cache_key = f"{vid}:{page}:{height or 0}"
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(cache_key)
        if hit and now - hit[0] <= _CACHE_TTL:
            return dict(hit[1])

    session = _session(proxies)
    params = {kind: vid}
    try:
        data = _get_json(session, VIEW_API, params)
    except RuntimeError as exc:
        return {"ok": False, "error": f"取作品信息失败: {exc}"}

    pages = data.get("pages") or []
    if pages and not 1 <= page <= len(pages):
        page = 1
    cid = pages[page - 1]["cid"] if pages else data.get("cid")
    if not cid:
        return {"ok": False, "error": "接口没给 cid，这条作品可能已失效"}

    title = data.get("title") or f"bilibili_{vid}"
    if len(pages) > 1:
        part = pages[page - 1].get("part") or ""
        title = f"{title} P{page} {part}".strip()
    duration = float((pages[page - 1].get("duration") if pages
                      else data.get("duration")) or 0)
    pic = data.get("pic") or ""
    if pic.startswith("//"):
        pic = "https:" + pic
    elif pic.startswith("http://"):
        pic = "https://" + pic[len("http://"):]

    common = {kind: vid, "cid": cid, "qn": _qn_for(height), "fnver": 0, "fourk": 1}
    dash = single = None
    try:
        dash = _pick_dash(_get_json(session, PLAYURL_API, {**common, "fnval": 4048}))
    except RuntimeError:
        pass
    try:
        single = _pick_single(_get_json(session, PLAYURL_API, {**common, "fnval": 1}))
    except RuntimeError:
        pass

    if not dash and not single:
        return {"ok": False, "error": "接口没有给出可用的播放地址（可能要登录，或作品已失效）"}
    # 单文件不比 DASH 差就用单文件：少一次合流，成片更稳
    chosen = single if (single and (not dash or single["height"] >= dash["height"])) else dash
    heights = sorted({h["height"] for h in (dash, single) if h and h["height"]},
                     reverse=True)
    info = {
        "ok": True,
        "bvid": vid if kind == "bvid" else "",
        "aid": data.get("aid") or (vid if kind == "aid" else ""),
        "page": page,
        "title": title,
        "uploader": (data.get("owner") or {}).get("name") or "",
        "duration": duration,
        "thumbnail": pic,
        "video_url": chosen["video_url"],
        "audio_url": chosen.get("audio_url") or "",
        "merged": bool(chosen.get("merged")),
        "height": chosen["height"],
        "heights": heights,
        "webpage_url": final_url,
    }
    with _CACHE_LOCK:
        _CACHE[cache_key] = (time.time(), dict(info))
        if len(_CACHE) > 64:
            oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])
            for key, _ in oldest[:16]:
                _CACHE.pop(key, None)
    return info


# ------------------------------------------------------------------ 探测

def probe_url(url: str) -> dict:
    """给 /api/probe 用：只认编号，靠接口拿标题和画质，不动网页。"""
    info = extract(url, height=0)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error") or "解析失败"}
    return {
        "ok": True,
        "kind": "video",
        "title": info.get("title") or "",
        "id": info.get("bvid") or str(info.get("aid") or ""),
        "uploader": info.get("uploader") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail") or "",
        "extractor": "哔哩哔哩",
        "webpage_url": info.get("webpage_url") or url,
        "is_playlist": False,
        "entry_count": 1,
        "heights": info.get("heights") or [],
    }


# ------------------------------------------------------------------ 下载

def download_url(url: str, out_dir, height: int = 0, connections: int = 8,
                 rate_bytes: int = 0, proxies: dict | None = None,
                 on_progress=None, should_stop=None) -> dict:
    if on_progress:
        on_progress({"status": "parsing"})
    info = extract(url, height=height, proxies=proxies)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error") or "解析失败"}
    return download_media(info, out_dir, connections=connections,
                          rate_bytes=rate_bytes, proxies=proxies,
                          on_progress=on_progress, should_stop=should_stop)


def download_media(info: dict, out_dir, connections: int = 8,
                   rate_bytes: int = 0, proxies: dict | None = None,
                   on_progress=None, should_stop=None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = info.get("bvid") or str(info.get("aid") or "bilibili")
    title = info.get("title") or f"bilibili_{key}"
    stem = sanitize_filename(f"{title} [{key}]")[:120]
    dest = _unique_path(out_dir / f"{stem}.mp4")

    work = out_dir / f".bilibili_{key}"
    work.mkdir(parents=True, exist_ok=True)
    video_path = work / "video.mp4"
    audio_path = work / "audio.m4a"

    headers = {"Referer": REFERER, "Origin": "https://www.bilibili.com",
               "User-Agent": UA}
    audio_url = "" if info.get("merged") else (info.get("audio_url") or "")
    streams = [("video", info["video_url"], video_path)]
    if audio_url:
        streams.append(("audio", audio_url, audio_path))

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

        result = run_stream(engine, report, should_stop)
        if result.get("cancelled"):
            return {"ok": False, "cancelled": True, "error": ""}
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or "下载失败"}
        base += engine.total
        report(engine.downloaded())

    if audio_url:
        if on_progress:
            on_progress({"status": "merging"})
        merged_ok = merge_av(video_path, audio_path, dest)
        if not merged_ok.get("ok"):
            return merged_ok
    else:
        try:
            video_path.replace(dest)
        except OSError as exc:
            return {"ok": False, "error": f"写入成片失败: {exc}"}

    for leftover in (video_path, audio_path):
        try:
            leftover.unlink()
        except OSError:
            pass
    try:
        work.rmdir()
    except OSError:
        pass

    try:
        size = dest.stat().st_size
    except OSError:
        size = 0
    if on_progress:
        on_progress({"status": "downloading", "downloaded": size,
                     "total": size, "filename": dest.name})
    return {"ok": True, "path": str(dest), "title": title,
            "duration": probe_duration(dest) or (info.get("duration") or 0),
            "thumbnail": info.get("thumbnail") or "", "size": size}


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
