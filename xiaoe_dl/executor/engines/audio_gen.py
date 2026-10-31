"""语音生成：TTS 配音与声音克隆。

都走 API，本机不加载任何声音模型权重（这类模型体积和显存都不适合
随程序分发）。服务商在「模型与接口」里为这两项能力分别选择。

克隆出来的音色用平台返回的 `uri` 作为 voice 参数复用，不需要每次重传样本。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from xiaoe_dl.paths import ffmpeg_exe as find_ffmpeg

from .. import backends
from .base import Engine, fail, ok

DEFAULT_VOICE = "FunAudioLLM/CosyVoice2-0.5B:alex"


def _audio_seconds(data: bytes, suffix: str = ".mp3") -> float:
    """量一下接口返回的音频有多长；ffmpeg 不可用就不判断（返回 0）。

    有的平台会返回 200 + 几百毫秒的音频，任务却报成功。这个函数就是
    用来把这种「空壳产物」挡掉的。
    """
    ff = find_ffmpeg()
    if not ff or not data:
        return 0.0
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="ocemind_tts_", suffix=suffix,
                                         delete=False) as fh:
            fh.write(data)
            tmp = fh.name
        proc = subprocess.run([ff, "-hide_banner", "-i", tmp],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr or "")
        if not match:
            return 0.0
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:  # noqa: BLE001
        return 0.0
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _read_text(ctx) -> str:
    """text 可以直接给，也可以给一个 .txt 路径——长稿子没必要塞进 JSON。"""
    raw = ctx.params.get("text") or ""
    if raw and os.path.isfile(str(raw)):
        return Path(str(raw)).read_text(encoding="utf-8", errors="replace").strip()
    return str(raw).strip()


def _out_dir(ctx) -> Path:
    path = Path(ctx.params.get("dir") or Path(ctx.config.home) / "audio")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _post_audio(url: str, key: str, payload: dict, timeout: float = 180) -> bytes:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", "replace")[:400]
        raise RuntimeError(f"{exc.code} {detail}") from exc


class Tts(Engine):
    id = "audio.tts"
    kinds = ("audio",)

    def run(self, ctx) -> dict:
        text = _read_text(ctx)
        if not text:
            return fail("没有可朗读的文本")

        target = backends.resolve(ctx.config, self.id, ctx.params)
        if not target["ready"]:
            return fail(target["problem"] or "配音服务不可用")
        key, base, model = target["key"], target["base"], target["model"]
        voice = ctx.params.get("voice") or ctx.config.get("ttsVoice") or DEFAULT_VOICE
        fmt = (ctx.params.get("format") or "mp3").lower()

        ctx.progress(None, "合成中")
        payload = {
            "model": model,
            "input": text[:4000],
            "voice": voice,
            "response_format": fmt,
            "speed": float(ctx.params.get("speed") or 1.0),
        }
        try:
            audio = _post_audio(base + "/audio/speech", key, payload)
        except Exception as exc:  # noqa: BLE001
            return fail(f"{target['provider_name']}: {type(exc).__name__}: {exc}")
        if not audio:
            return fail("接口返回了空音频")
        # 接口偶尔会 200 + 一两百毫秒的音频，报成功等于给用户一个空壳文件
        seconds = _audio_seconds(audio, f".{fmt}")
        if 0 < seconds < 0.3:
            return fail(f"接口返回的音频只有 {seconds:.2f} 秒，不像有效的合成结果")

        out = _out_dir(ctx) / (ctx.params.get("name")
                               or f"tts_{int(time.time())}.{fmt}")
        out.write_bytes(audio)

        # 配音按字符计价，单价在设置里可调（默认 0，表示走免费额度）
        price = float(ctx.config.get("ttsPricePerKChar") or 0.0)
        try:
            ctx.record_usage(target["engine"], self.id, len(text) / 1000,
                             price * len(text) / 1000, detail=voice)
        except Exception:  # noqa: BLE001
            pass
        return ok(out)


class VoiceClone(Engine):
    id = "audio.voiceclone"
    kinds = ("audio",)

    def run(self, ctx) -> dict:
        sample = Path(ctx.inputs.get("path") or ctx.params.get("sample") or "")
        if not sample.exists():
            return fail("克隆样本不存在")
        text = _read_text(ctx)
        if not text:
            return fail("没有要合成的文本")

        target = backends.resolve(ctx.config, self.id, ctx.params)
        if not target["ready"]:
            return fail(target["problem"] or "声音克隆服务不可用")
        if target["provider"] != "siliconflow":
            return fail("声音克隆目前只有 SiliconFlow 提供样本上传接口，"
                        "请在「模型与接口」里把声音克隆切到 SiliconFlow")
        key, base, model = target["key"], target["base"], target["model"]

        ctx.progress(None, "上传样本")
        try:
            voice_uri = upload_voice(sample, key, base, model,
                                    ctx.params.get("name") or sample.stem)
        except Exception as exc:  # noqa: BLE001
            return fail(f"建立音色失败: {type(exc).__name__}: {exc}")

        ctx.progress(None, "合成中")
        fmt = (ctx.params.get("format") or "mp3").lower()
        payload = {"model": model, "input": text[:4000], "voice": voice_uri,
                   "response_format": fmt,
                   "speed": float(ctx.params.get("speed") or 1.0)}
        try:
            audio = _post_audio(base + "/audio/speech", key, payload)
        except Exception as exc:  # noqa: BLE001
            return fail(f"{target['provider_name']}: {type(exc).__name__}: {exc}")
        if not audio:
            return fail("接口返回了空音频")
        # 克隆音色和 TTS 一样会返回空壳，0.216 秒的「成功」其实没有声音
        seconds = _audio_seconds(audio, f".{fmt}")
        if 0 < seconds < 0.3:
            return fail(f"接口返回的音频只有 {seconds:.2f} 秒，不像有效的克隆结果")

        out = _out_dir(ctx) / (ctx.params.get("output")
                               or f"clone_{int(time.time())}.{fmt}")
        out.write_bytes(audio)
        # 音色 uri 写进任务输出，下次可以直接拿它当 voice 用
        ctx.job.setdefault("meta", {})["voice_uri"] = voice_uri
        return ok(out)


def upload_voice(sample: Path, key: str, base: str, model: str,
                 label: str) -> str:
    """把样本传上去换一个可复用的 voice uri。"""
    boundary = "----ocemind" + uuid.uuid4().hex
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8"))

    field("model", model)
    field("customName", label[:32] or "voice")
    field("text", "这是一段用于校验音色的示例文本。")
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{sample.name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
    parts.append(sample.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    req = urllib.request.Request(
        base + "/uploads/audio/voice", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", "replace")[:400]
        raise RuntimeError(f"{exc.code} {detail}") from exc
    uri = data.get("uri") or data.get("voice") or ""
    if not uri:
        raise RuntimeError(f"接口没返回音色 uri: {str(data)[:200]}")
    return uri
