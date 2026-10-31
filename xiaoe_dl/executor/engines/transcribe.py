"""音视频转文本。

两种后端，按设置选（默认"本地装了就用本地"）：

  本地   faster-whisper（推荐，CPU 可跑）/ openai-whisper
  云端   任意 OpenAI 兼容的 /audio/transcriptions：
         SiliconFlow、Groq、OpenAI，或自己填地址和密钥

本地引擎不联网、不上传素材；云端引擎只发抽出来的音轨。
接口返回带 segments 时出 SRT，只有纯文本时只出 txt —— 这个区别会在
任务结果里标出来，不假装有字幕。
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from xiaoe_dl.paths import ffmpeg_exe as find_ffmpeg

from .. import backends
from .base import Engine, cancelled, fail, ok

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".ts", ".m4v", ".wmv"}

#: 各服务商的大致单价（元/小时），用来记账。填 0 表示走免费额度。
PRICES = {
    "siliconflow": 0.0,
    "groq": 0.29,
    "openai": 0.43,
}


def _ffmpeg() -> str:
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg：pip install imageio-ffmpeg，或把 ffmpeg 放进 PATH")
    return ff


def extract_audio(src: Path, dst: Path, ctx, bitrate: str = "48k") -> None:
    """统一转 16k 单声道 mp3。比特率低但语音识别足够，体积小上传快。"""
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "libmp3lame", "-b:a", bitrate, str(dst)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while proc.poll() is None:
        if ctx.should_stop():
            proc.terminate()
            raise KeyboardInterrupt
        time.sleep(0.2)
    if proc.returncode != 0:
        err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"抽音频失败: {err[-400:]}")


# ------------------------------------------------------------------ SRT

def _fmt_ts(seconds: float) -> str:
    ms = int(round(float(seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_outputs(base: Path, segments: list[dict], text: str) -> list[str]:
    outputs: list[str] = []
    body = (text or "").strip()
    if not body and segments:
        body = "\n".join(s["text"].strip() for s in segments)
    txt = base.with_suffix(".txt")
    txt.write_text(body + "\n", encoding="utf-8")
    outputs.append(str(txt))

    if segments:
        srt = base.with_suffix(".srt")
        blocks = []
        for i, seg in enumerate(segments, 1):
            blocks.append(f"{i}\n{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}\n"
                          f"{seg['text'].strip()}\n")
        srt.write_text("\n".join(blocks), encoding="utf-8")
        outputs.append(str(srt))
    return outputs


# -------------------------------------------------------------- 本地引擎

def _local_faster_whisper(ctx, audio: Path, model_name: str,
                          language: str, model_dir: str) -> list[dict]:
    from faster_whisper import WhisperModel

    ctx.progress(None, "加载本地模型（首次会下载）")
    kwargs = {"device": "cpu", "compute_type": "int8"}
    if model_dir:
        kwargs["download_root"] = model_dir
    model = WhisperModel(model_name or "small", **kwargs)
    if ctx.should_stop():
        raise KeyboardInterrupt

    lang = None if language in ("", "auto") else language
    segments_iter, info = model.transcribe(
        str(audio), language=lang, vad_filter=True, beam_size=5,
        condition_on_previous_text=False)
    total = float(getattr(info, "duration", 0) or 0)

    segments: list[dict] = []
    for seg in segments_iter:
        if ctx.should_stop():
            raise KeyboardInterrupt
        text = (seg.text or "").strip()
        if text:
            segments.append({"start": float(seg.start), "end": float(seg.end),
                             "text": text})
        # 有总时长就报真实进度，没有就保持"不确定"
        pct = min(99.0, float(seg.end) * 100.0 / total) if total else None
        ctx.progress(pct, "转写中（本地）")
    return segments


def _local_openai_whisper(ctx, audio: Path, model_name: str,
                          language: str, model_dir: str) -> list[dict]:
    import whisper

    ctx.progress(None, "加载本地模型（首次会下载，需要一段时间）")
    model = whisper.load_model(model_name or "small",
                               download_root=model_dir or None)
    if ctx.should_stop():
        raise KeyboardInterrupt
    lang = None if language in ("", "auto") else language
    ctx.progress(None, "转写中（本地）")
    result = model.transcribe(str(audio), language=lang, verbose=False)
    segments = []
    for seg in result.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        if text:
            segments.append({"start": float(seg.get("start") or 0),
                             "end": float(seg.get("end") or 0), "text": text})
    return segments


def transcribe_local(ctx, audio: Path, engine_id: str, model: str,
                     language: str) -> list[dict]:
    model_dir = (ctx.config.get("localModelDir") or "").strip()
    if engine_id == "faster-whisper":
        return _local_faster_whisper(ctx, audio, model, language, model_dir)
    if engine_id == "whisper":
        return _local_openai_whisper(ctx, audio, model, language, model_dir)
    raise RuntimeError(f"未知的本地引擎: {engine_id}")


# -------------------------------------------------------------- 云端调用

def _multipart(fields: dict, file_field: str, filename: str,
               data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = "----ocemind" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"))
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


#: (base, model) → "json" / "verbose_json"。硅基流动的 Qwen3-ASR 只认
#: json/text，传 verbose_json 会返回空的 400，大文件上传时服务端还会
#: 直接掐连接（WinError 10054）。第一次踩到就记住，后面不再试。
_FORMAT_MEMO: dict[tuple[str, str], str] = {}
_FORMAT_LOCK = threading.Lock()


def _format_for(base: str, model: str, want_segments: bool) -> str:
    if not want_segments:
        return "json"
    with _FORMAT_LOCK:
        return _FORMAT_MEMO.get((base, model), "verbose_json")


def _remember_format(base: str, model: str, fmt: str) -> None:
    with _FORMAT_LOCK:
        _FORMAT_MEMO[(base, model)] = fmt


def _unsupported_segments(code: int, detail: str) -> bool:
    """400 + 空响应体，就是「不认识这个 response_format」。"""
    if code != 400:
        return False
    body = (detail or "").strip().lower()
    if not body:
        return True
    return "response_format" in body or "verbose_json" in body


def _connection_reset(exc: urllib.error.URLError) -> bool:
    reason = exc.reason
    text = str(reason).lower()
    return isinstance(reason, ConnectionError) or "10054" in text or "reset" in text


def _http_message(code: int, detail: str) -> str:
    body = (detail or "").strip()
    if body:
        return f"{code} {body}"
    if code == 400:
        return ("400 服务端拒绝了请求且没有返回原因"
                "（常见于该模型不支持 response_format=verbose_json）")
    return f"{code}（服务端未返回内容）"


def _post_transcription(base: str, key: str, model: str, audio: Path,
                        language: str, want_segments: bool,
                        timeout: float = 900) -> dict:
    """POST /audio/transcriptions，按能力降级，不把「格式不支持」当网络故障。

    服务端有两种拒绝方式：老老实实回 400（响应体为空），或者在大文件
    还在上传时直接关连接（WinError 10054）。两者都说明这个模型不产出
    时间轴，换 json 重发一次即可。真正的网络抖动则最多重试三次。
    """
    url = base.rstrip("/") + "/audio/transcriptions"
    fields = {"model": model}
    if language:
        fields["language"] = language
    data = audio.read_bytes()
    fmt = _format_for(url, model, want_segments)
    attempt = 0

    while True:
        attempt += 1
        body_fields = dict(fields, response_format=fmt)
        payload, ctype = _multipart(body_fields, "file", audio.name,
                                    data, "audio/mpeg")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": ctype},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = (exc.read() or b"").decode("utf-8", "replace")[:400]
            if (fmt == "verbose_json" and want_segments
                    and _unsupported_segments(exc.code, detail)):
                _remember_format(url, model, "json")
                fmt = "json"
                continue
            if exc.code >= 500 and attempt < 3:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(_http_message(exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            reset = _connection_reset(exc)
            if attempt < 3:
                # 上传途中被掐，多半还是 verbose_json 被拒；先降级再重试
                if reset and fmt == "verbose_json" and want_segments:
                    _remember_format(url, model, "json")
                    fmt = "json"
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(
                f"网络错误: {exc.reason}（已重试 {attempt} 次）") from exc

        # 200 但没有 segments（SenseVoice 就是这样）：记下来，下次别问了
        if (want_segments and fmt == "verbose_json"
                and not (raw.get("segments") or [])):
            _remember_format(url, model, "json")
        return raw


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


def _normalize_segments(raw: dict) -> list[dict]:
    out = []
    for seg in raw.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        out.append({
            "start": float(seg.get("start") or 0),
            "end": float(seg.get("end") or 0),
            "text": str(seg.get("text") or ""),
        })
    return out


# ------------------------------------------------------------------ 引擎

class Transcribe(Engine):
    id = "transcribe.asr"
    kinds = ("transcribe",)

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self, ctx) -> dict:
        src = ctx.inputs.get("path") or ctx.inputs.get("url") or ""
        if not src:
            return fail("缺少输入文件")
        src_path = Path(src)
        if not src_path.exists():
            return fail(f"文件不存在: {src_path}")

        suffix = src_path.suffix.lower()
        if suffix not in AUDIO_EXTS | VIDEO_EXTS:
            return fail(f"不支持的文件类型: {suffix or '(无扩展名)'}")

        target = backends.resolve(ctx.config, self.id, ctx.params)
        if not target["ready"]:
            return fail(target["problem"] or "转写后端不可用")
        language = ctx.params.get("language", ctx.config.get("asrLanguage") or "zh")
        want_srt = _as_bool(ctx.params.get("srt"), default=True)

        out_dir = Path(ctx.params.get("dir")
                       or Path(ctx.config.home) / "transcripts")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_base = out_dir / (ctx.params.get("name") or src_path.stem)

        ctx.progress(None, "抽取音频")
        audio = ctx.workdir / "audio.mp3"
        try:
            extract_audio(src_path, audio, ctx, bitrate="48k")
            # 超过 20MB 再压一档，避免接口拒收（本地引擎也一样省时间）
            if audio.stat().st_size > 20 * 1024 * 1024:
                ctx.progress(None, "压缩音频")
                extract_audio(src_path, audio, ctx, bitrate="24k")
        except KeyboardInterrupt:
            return cancelled()
        except Exception as exc:  # noqa: BLE001
            return fail(str(exc))

        started = time.time()
        segments: list[dict] = []
        text = ""
        cost = 0.0
        try:
            if target["backend"] == "local":
                segments = transcribe_local(ctx, audio, target["local_engine"],
                                            target["model"], language)
                text = " ".join(s["text"].strip() for s in segments)
            else:
                ctx.progress(None, "转写中")
                raw = _post_transcription(target["base"], target["key"],
                                          target["model"], audio, language,
                                          want_segments=want_srt)
                segments = _normalize_segments(raw)
                text = (raw.get("text") or "").strip()
        except KeyboardInterrupt:
            return cancelled()
        except Exception as exc:  # noqa: BLE001
            who = ("本地 " + target["local_engine"] if target["backend"] == "local"
                   else target["provider_name"])
            return fail(f"{who}: {exc}")
        finally:
            try:
                audio.unlink(missing_ok=True)
            except OSError:
                pass

        if not text and not segments:
            return fail("没有识别出任何文字")

        outputs = write_outputs(out_base, segments, text)
        no_timestamps = want_srt and not segments
        if no_timestamps:
            # 勾了 SRT 却拿不到时间轴：说清楚原因，别让人以為文件丢了
            ctx.progress(100.0, "转写完成（模型只返回纯文本，未生成 SRT）")
        else:
            ctx.progress(100.0, "转写完成")

        elapsed = max(0.1, time.time() - started)
        if target["backend"] == "cloud":
            price = float(PRICES.get(target["provider"], 0.0))
            cost = price * (elapsed / 3600.0) if price else 0.0
        try:
            ctx.record_usage(target["engine"] or "local", self.id, elapsed, cost,
                             detail=target["model"])
        except Exception:  # noqa: BLE001
            pass

        result = ok(*outputs)
        result["meta"] = {
            "backend": target["backend"],
            "provider": target["provider"] or "local",
            "engine": target["engine"],
            "model": target["model"],
            "has_timestamps": bool(segments),
            "elapsed_seconds": round(elapsed, 1),
            "cost": round(cost, 4),
        }
        if no_timestamps:
            result["meta"]["note"] = (
                "该模型只返回纯文本，没有时间轴；要 SRT 请换 Groq/OpenAI "
                "的 whisper，或本地 faster-whisper")
        return result
