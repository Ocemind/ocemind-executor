"""能力注册表。

能力清单的唯一事实源：API 从这里返回，前端从这里渲染，Agent 从这里发现能力。
文档里写"支持"而代码里没有，就是这一份表的问题——改表，不要改文档。

这里登记的是"能不能做"，不是"做没做成功"。某一次执行的结果在任务表里。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    kind: str                     # download / transcribe / media / document / image / audio / publish
    backend: str                  # local / cloud / browser
    requires_gpu: bool = False
    paid: bool = False
    automation: str = "auto"      # auto / assisted / manual
    platforms: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)
    notes: str = ""


_DOMESTIC = ("bilibili", "douyin", "xiaohongshu", "weibo")


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="download.platform",
        name="平台视频下载",
        kind="download",
        backend="local",
        platforms=_DOMESTIC + ("youtube", "x"),
        params={"url": "str", "dir": "str?", "height": "int?"},
        notes="yt-dlp 驱动；视频号与快手无 extractor，需手动存文件",
    ),
    Capability(
        id="transcribe.asr",
        name="音视频转文本",
        kind="transcribe",
        backend="cloud",
        paid=True,
        params={"path": "str", "provider": "siliconflow|groq|openai?",
                "language": "str?", "srt": "bool?", "name": "str?"},
        notes="本地优先（faster-whisper）；没装才走云端 API，可切 Groq / OpenAI",
    ),
    Capability(
        id="media.transcode",
        name="转码 / 压缩 / 合并",
        kind="media",
        backend="local",
        params={"paths": "list|path", "mode": "compress|transcode|merge?",
                "crf": "int?", "preset": "str?", "height": "int?", "format": "str?"},
        notes="本地 ffmpeg；合并走 concat 分离器，失败自动回退重编码",
    ),
    Capability(
        id="media.subtitle",
        name="字幕烧制 / 翻译 / 对齐",
        kind="media",
        backend="local",
        paid=True,
        params={"video": "str", "srt": "str?", "op": "burn|translate|shift?",
                "target": "str?", "offset": "float?"},
        notes="烧制用本地 ffmpeg；翻译走对话模型（服务商可换），按条编号回填防错位",
    ),
    Capability(
        id="media.audio",
        name="音频后期",
        kind="media",
        backend="local",
        params={"path": "str", "op": "normalize|denoise|speed|extract?",
                "speed": "float?", "format": "str?"},
        notes="响度归一 loudnorm / 降噪 afftdn / 变速 atempo",
    ),
    Capability(
        id="media.thumbnail",
        name="封面 / 缩略图",
        kind="media",
        backend="local",
        params={"path": "str", "count": "int?", "at": "float?", "width": "int?"},
        notes="均匀抽帧；指定 at 则只出该时间点的一帧",
    ),
    Capability(
        id="doc.parse",
        name="文档解析",
        kind="document",
        backend="local",
        params={"path": "str", "dir": "str?"},
        notes="PDF/Word/Excel/CSV/JSON → Markdown，纯本地，不联网",
    ),
    Capability(
        id="image.ocr",
        name="图片 OCR",
        kind="image",
        backend="cloud",
        paid=True,
        params={"path": "str", "prompt": "str?", "name": "str?"},
        notes="本地优先（RapidOCR / PaddleOCR / Tesseract）；没装才用视觉模型",
    ),
    Capability(
        id="image.edit",
        name="图像处理",
        kind="image",
        backend="local",
        params={"path": "str", "op": "resize|crop|watermark|convert|thumb",
                "width": "int?", "height": "int?", "text": "str?", "format": "str?"},
        notes="Pillow；不改变画质的操作才走这里，重编码请用转码",
    ),
    Capability(
        id="image.generate",
        name="AI 生图",
        kind="image",
        backend="cloud",
        paid=True,
        params={"prompt": "str", "size": "str?", "name": "str?"},
        notes="本地跑不动扩散模型，只能走 API；服务商可换",
    ),
    Capability(
        id="audio.tts",
        name="TTS 配音",
        kind="audio",
        backend="cloud",
        paid=True,
        params={"text": "str|path", "voice": "str?", "speed": "float?",
                "format": "str?"},
        notes="只能走 API（本地 TTS 模型体积不划算）；服务商可换",
    ),
    Capability(
        id="audio.voiceclone",
        name="声音克隆",
        kind="audio",
        backend="cloud",
        paid=True,
        params={"sample": "str", "text": "str", "name": "str?"},
        notes="先上传样本建立音色，再用该音色合成；样本需 10 秒以上干净人声；目前只有 SiliconFlow",
    ),
    Capability(
        id="publish.browser",
        name="浏览器发布",
        kind="publish",
        backend="browser",
        automation="assisted",
        platforms=_DOMESTIC + ("shipinhao", "kuaishou"),
        params={"account_id": "str", "files": "list", "meta": "dict"},
        notes="真实 Chrome 操作创作后台；发布后必须回读确认才算成功",
    ),
)


def all_capabilities() -> list[dict]:
    return [asdict(c) for c in CAPABILITIES]


def get(capability_id: str) -> Capability | None:
    return next((c for c in CAPABILITIES if c.id == capability_id), None)


def for_kind(kind: str) -> list[Capability]:
    return [c for c in CAPABILITIES if c.kind == kind]
