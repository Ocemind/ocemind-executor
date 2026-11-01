# -*- coding: utf-8 -*-
"""从一段分享文案里找出真正的链接。

抖音、小红书这类 App 的「复制链接」给的不是一个干净的 URL，而是
「7.61 复制打开抖音，看看…… https://v.douyin.com/xxxx/ 复制此链接…」。
用户粘过来时不该被一句「看不出这是什么」挡回去。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import paths

#: 末尾常见的标点不属于链接，粘进来会把 url 弄坏
_TRAILING = "，。,.!！?？、;；:：)）]】》>\"'`"

#: 分享文案里的中文标点紧挨着 URL 时也要截断，否则会把「，好看」吃进去
_PUNCT = "，。！？；：、（）【】《》「」“”‘’"

_URL_RE = re.compile(rf"https?://[^\s<>\"'`{_PUNCT}]+", re.I)
_BARE_RE = re.compile(
    rf"(?:[a-z0-9-]+\.)+(?:com|cn|net|org|tv|me|io|cc|xyz)"
    rf"/[^\s<>\"'`{_PUNCT}]*", re.I)


def first_url(text: str) -> str:
    """返回文案里的第一个链接；没有就返回空串。

    只有裸域名（如 v.douyin.com/xxx/）时补上 https://。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    match = _URL_RE.search(raw)
    if match:
        return match.group(0).rstrip(_TRAILING)
    match = _BARE_RE.search(raw)
    if match:
        return "https://" + match.group(0).rstrip(_TRAILING)
    return ""


def looks_like_url(text: str) -> bool:
    return bool(first_url(text))


# ------------------------------------------------------------ 短链还原
#
# 抖音/小红书/快手/B站/微博的「分享」给的是短链，真正的地址藏在一次 302 里。
# yt-dlp 对其中一部分能自己跟过去，但我们得先知道它到底指向哪个站——不然
# 选不了适配器，也没法在界面上告诉用户「这条其实是 B 站视频」。
#
# 域名表放在 short_links.json：数据目录里同名的文件优先级更高，
# 站点换域名时改文件就行，不用发版。

_DOMAINS_FILE = Path(__file__).with_name("short_links.json")
_TTL = 6 * 60 * 60           # 同一条短链 6 小时内不重复跟

_lock = threading.Lock()
_domains_cache: tuple[float, set[str]] | None = None
_expand_cache: dict[str, tuple[float, str]] = {}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def short_domains() -> set[str]:
    """短链域名集合。包内一份 + 数据目录覆盖，读出来缓存 5 分钟。"""
    global _domains_cache
    with _lock:
        if _domains_cache and time.time() - _domains_cache[0] < 300:
            return set(_domains_cache[1])

    found: set[str] = set()
    for path in (_DOMAINS_FILE, Path(paths.app_dir()) / "short_links.json"):
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = loaded.get("hosts") if isinstance(loaded, dict) else loaded
        if isinstance(items, list):
            found |= {str(item).strip().lower().lstrip(".")
                      for item in items if str(item).strip()}
    with _lock:
        _domains_cache = (time.time(), found)
    return set(found)


def is_short(url: str) -> bool:
    """这个地址是不是已知的分享短链。"""
    host = (urlsplit((url or "").strip()).hostname or "").lower()
    return bool(host) and host in short_domains()


def expand(url: str, timeout: float = 4.0) -> str:
    """跟着跳转拿到真实地址。不是短链、跟不过去、超时都返回空串。

    返回值只用来补信息，调用方拿不到就继续用原链接——跟不动不该让任务失败。
    """
    url = (url or "").strip()
    if not is_short(url):
        return ""
    with _lock:
        hit = _expand_cache.get(url)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]

    final = _follow(url, timeout)
    if final and final != url:
        with _lock:
            _expand_cache[url] = (time.time(), final)
    return final


def _follow(url: str, timeout: float) -> str:
    """HEAD 优先，站点头拒绝就退回只取第一个字节的 GET。"""
    for method in ("HEAD", "GET"):
        headers = {"User-Agent": _UA, "Accept": "*/*"}
        if method == "GET":
            headers["Range"] = "bytes=0-0"
        try:
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.geturl()
        except urllib.error.HTTPError as exc:
            # 403/404 也常常是在跳转之后才发生的，地址本身是有效的
            final = getattr(exc, "url", "") or ""
            if final and final != url:
                return final
        except Exception:  # noqa: BLE001 - 网络问题一律视为「跟不动」
            continue
    return ""
