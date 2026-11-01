"""能力 ID → 引擎类。

注册表（registry.py）说"能做什么"，这张表说"谁来做"。两边不一致时，
`verify_answer()` 会当场报出来，而不是等到用户点了才 500。
"""
from __future__ import annotations

from .audio_gen import Tts, VoiceClone
from .document import DocParse
from .image import ImageEdit, ImageGen, Ocr
from .media import AudioPost, Subtitle, Thumbnail, Transcode
from .registry import CAPABILITIES
from .transcribe import Transcribe
from .ytdlp_download import YtdlpDownload

_CLASSES = (
    YtdlpDownload, Transcribe,
    Transcode, Subtitle, AudioPost, Thumbnail,
    DocParse, Ocr, ImageEdit, ImageGen,
    Tts, VoiceClone,
)

ENGINES: dict[str, type] = {cls.id: cls for cls in _CLASSES}


def build(capability_id: str, **deps):
    """实例化引擎。发布类引擎需要传入浏览器池，其余无参。"""
    if capability_id == "publish.browser":
        from ocemind.executor.publish.engine import PublishBrowser

        return PublishBrowser(deps.get("pool"))
    cls = ENGINES.get(capability_id)
    if cls is None:
        raise KeyError(f"没有实现这个能力: {capability_id}")
    return cls()


def implemented() -> set[str]:
    ids = set(ENGINES)
    # 发布引擎在 publish 包里，单独算
    ids.add("publish.browser")
    return ids


def verify_answer() -> list[str]:
    """注册表里有、但引擎表里没有的能力 ID。空列表才是一致的。"""
    return sorted({c.id for c in CAPABILITIES} - implemented())
