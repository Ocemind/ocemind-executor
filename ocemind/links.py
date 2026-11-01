# -*- coding: utf-8 -*-
"""从一段分享文案里找出真正的链接。

抖音、小红书这类 App 的「复制链接」给的不是一个干净的 URL，而是
「7.61 复制打开抖音，看看…… https://v.douyin.com/xxxx/ 复制此链接…」。
用户粘过来时不该被一句「看不出这是什么」挡回去。
"""
from __future__ import annotations

import re

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
