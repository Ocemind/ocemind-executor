"""媒体处理：转码、合并、字幕、音频后期、封面。

全部本地跑 ffmpeg，不花钱、不联网。唯一的例外是字幕翻译——那个必须调模型。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ocemind.paths import ffmpeg_exe as find_ffmpeg
from .base import Engine, cancelled, fail, ok


# ---------------------------------------------------------------- ffmpeg

def _ff() -> str:
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg")
    return ff


def run_ffmpeg(args: list[str], total_seconds: float = 0,
               on_line=None, ctx=None) -> None:
    cmd = [_ff(), "-hide_banner", "-nostdin", "-y"] + args
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    assert proc.stderr is not None
    for line in proc.stderr:
        if ctx is not None and ctx.should_stop():
            proc.terminate()
            raise KeyboardInterrupt
        if on_line:
            on_line(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 退出码 {proc.returncode}")


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.run([_ff(), "-i", str(path)],
                             capture_output=True, text=True, timeout=30)
        text = out.stderr or ""
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
        if not match:
            return 0.0
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:  # noqa: BLE001
        return 0.0


def _progress_from_line(line: str) -> float | None:
    match = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", line)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


# ---------------------------------------------------------------- 转码

class Transcode(Engine):
    id = "media.transcode"
    kinds = ("media",)

    def run(self, ctx) -> dict:
        src = Path(ctx.inputs.get("path") or "")
        if not src.exists():
            return fail("源文件不存在")
        out_dir = Path(ctx.params.get("dir") or src.parent)
        out_dir.mkdir(parents=True, exist_ok=True)

        segments = ctx.params.get("merge") or []      # 合并模式
        if segments:
            return self._merge(ctx, [Path(p) for p in segments], out_dir)

        out = out_dir / (ctx.params.get("name") or f"{src.stem}_out.mp4")
        height = int(ctx.params.get("height") or 0)
        crf = int(ctx.params.get("crf") or 23)
        audio_only = bool(ctx.params.get("audio_only"))
        total = probe_duration(src)

        with_total = {"total": total}

        def on_line(line: str) -> None:
            t = _progress_from_line(line)
            if t is not None and with_total["total"]:
                ctx.progress(round(min(99, t * 100 / with_total["total"]), 1),
                             "转码中")

        args = ["-i", str(src)]
        if audio_only:
            args += ["-vn", "-c:a", "aac", "-b:a", "128k", str(out.with_suffix(".m4a"))]
            target = out.with_suffix(".m4a")
        else:
            if height:
                args += ["-vf", f"scale=-2:{height}"]
            args += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
                     "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                     str(out)]
            target = out

        ctx.progress(0, "转码中")
        try:
            run_ffmpeg(args, total, on_line, ctx)
        except KeyboardInterrupt:
            return cancelled()
        except Exception as exc:  # noqa: BLE001
            return fail(str(exc))
        return ok(target)

    def _merge(self, ctx, parts: list[Path], out_dir: Path) -> dict:
        missing = [str(p) for p in parts if not p.exists()]
        if missing:
            return fail(f"缺少文件: {missing[:3]}")
        out = out_dir / (ctx.params.get("name") or "merged.mp4")
        list_file = ctx.workdir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
        args = ["-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(out)]
        try:
            run_ffmpeg(args, 0, None, ctx)
        except KeyboardInterrupt:
            return cancelled()
        except Exception as exc:  # noqa: BLE001
            # 参数不一致时会失败，退回到重编码
            try:
                args = ["-f", "concat", "-safe", "0", "-i", str(list_file),
                        "-c:v", "libx264", "-crf", "23", "-c:a", "aac", str(out)]
                run_ffmpeg(args, 0, None, ctx)
            except Exception:  # noqa: BLE001
                return fail(str(exc))
        return ok(out)


# ---------------------------------------------------------------- 字幕

SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str) -> list[dict]:
    blocks = re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n"))
    out = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        match = SRT_TIME.search(block)
        if not match:
            continue
        g = [int(x) for x in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        idx = next((i for i, ln in enumerate(lines) if SRT_TIME.search(ln)), 0)
        body = "\n".join(lines[idx + 1:]).strip()
        out.append({"start": start, "end": end, "text": body})
    return out


def format_srt(items: list[dict]) -> str:
    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i, item in enumerate(items, 1):
        blocks.append(f"{i}\n{ts(item['start'])} --> {ts(item['end'])}\n"
                      f"{item['text'].strip()}\n")
    return "\n".join(blocks)


class Subtitle(Engine):
    id = "media.subtitle"
    kinds = ("media",)

    def run(self, ctx) -> dict:
        op = ctx.params.get("op") or "burn"
        if op == "burn":
            return self._burn(ctx)
        if op == "translate":
            return self._translate(ctx)
        if op == "shift":
            return self._shift(ctx)
        return fail(f"未知字幕操作: {op}")

    def _burn(self, ctx) -> dict:
        video = Path(ctx.inputs.get("path") or "")
        srt = Path(ctx.params.get("srt") or "")
        if not video.exists():
            return fail("视频不存在")
        if not srt.exists():
            return fail("字幕文件不存在")
        out = Path(ctx.params.get("dir") or video.parent) / \
            (ctx.params.get("name") or f"{video.stem}_sub.mp4")
        # subtitles 滤镜里的路径要转义，Windows 下冒号必须处理
        esc = str(srt).replace("\\", "/").replace(":", r"\:")
        args = ["-i", str(video), "-vf", f"subtitles='{esc}'",
                "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(out)]
        try:
            run_ffmpeg(args, probe_duration(video), None, ctx)
        except KeyboardInterrupt:
            return cancelled()
        except Exception as exc:  # noqa: BLE001
            return fail(str(exc))
        return ok(out)

    def _translate(self, ctx) -> dict:
        srt = Path(ctx.params.get("srt") or ctx.inputs.get("path") or "")
        if not srt.exists():
            return fail("字幕文件不存在")
        target = ctx.params.get("target") or "en"
        items = parse_srt(srt.read_text(encoding="utf-8", errors="ignore"))
        if not items:
            return fail("没解析出字幕条目")

        ctx.progress(None, "翻译中")
        translated = _translate_batch(ctx, items, target)
        out = srt.with_name(f"{srt.stem}.{target}.srt")
        out.write_text(format_srt(translated), encoding="utf-8")
        return ok(out)

    def _shift(self, ctx) -> dict:
        srt = Path(ctx.params.get("srt") or ctx.inputs.get("path") or "")
        if not srt.exists():
            return fail("字幕文件不存在")
        offset = float(ctx.params.get("offset") or 0)
        items = parse_srt(srt.read_text(encoding="utf-8", errors="ignore"))
        for item in items:
            item["start"] = max(0.0, item["start"] + offset)
            item["end"] = max(0.0, item["end"] + offset)
        out = srt.with_name(f"{srt.stem}_shifted.srt")
        out.write_text(format_srt(items), encoding="utf-8")
        return ok(out)


def _translate_batch(ctx, items: list[dict], target: str) -> list[dict]:
    """一次翻 20 条，保留序号与时间轴。用任意 OpenAI 兼容接口。"""
    from .. import backends

    chosen = backends.resolve(ctx.config, "media.subtitle", ctx.params)
    if not chosen["ready"]:
        raise RuntimeError(chosen["problem"] or "翻译服务不可用")
    base, key, model = chosen["base"].rstrip("/"), chosen["key"], chosen["model"]

    result: list[dict] = []
    chunk = 20
    for start in range(0, len(items), chunk):
        if ctx.should_stop():
            raise KeyboardInterrupt
        group = items[start:start + chunk]
        numbered = "\n".join(f"{i + 1}. {g['text']}" for i, g in enumerate(group))
        prompt = (f"把下面每条字幕翻译成 {target}，保持编号和条数不变，"
                  f"只输出编号和译文，不要解释：\n{numbered}")
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }).encode("utf-8")
        req = urllib.request.Request(
            base + "/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"翻译失败: {exc}") from exc

        lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
        mapping: dict[int, str] = {}
        for line in lines:
            match = re.match(r"^(\d+)[.、)]\s*(.+)$", line)
            if match:
                mapping[int(match.group(1))] = match.group(2).strip()
        for i, item in enumerate(group, 1):
            result.append({"start": item["start"], "end": item["end"],
                           "text": mapping.get(i, item["text"])})
        ctx.progress(round(len(result) * 100 / len(items), 1), "翻译中")
    return result


# ---------------------------------------------------------------- 音频后期

#: 输出容器 → (编码器, 是否吃码率)。wav/flac 是无损，给 -b:a 没意义。
AUDIO_OUTPUTS = {
    "mp3": ("libmp3lame", True),
    "m4a": ("aac", True),
    "aac": ("aac", True),
    "ogg": ("libvorbis", True),
    "wav": ("pcm_s16le", False),
    "flac": ("flac", False),
}


def _audio_output(out_dir: Path, src: Path, op: str,
                  name: str, fmt: str) -> tuple[Path, str]:
    """决定输出文件名和容器。

    `format` 是注册表里公开的参数，之前完全没读；`name` 不带扩展名时
    ffmpeg 也推断不出容器，会直接退出。这里统一补成合法扩展名。
    """
    fmt = (fmt or "").lower().lstrip(".")
    if fmt not in AUDIO_OUTPUTS:
        fmt = "m4a"
    if name:
        out = Path(name)
        if not out.is_absolute():
            out = out_dir / out
        suffix = out.suffix.lower().lstrip(".")
        if suffix in AUDIO_OUTPUTS:
            fmt = suffix
        else:
            out = out.with_suffix("." + fmt)
    else:
        out = out_dir / f"{src.stem}_{op}.{fmt}"
    return out, fmt


class AudioPost(Engine):
    id = "media.audio"
    kinds = ("media",)

    def run(self, ctx) -> dict:
        src = Path(ctx.inputs.get("path") or "")
        if not src.exists():
            return fail("源文件不存在")
        op = ctx.params.get("op") or "normalize"
        out_dir = Path(ctx.params.get("dir") or src.parent)
        out_dir.mkdir(parents=True, exist_ok=True)

        filters = {
            "normalize": "loudnorm=I=-16:TP=-1.5:LRA=11",
            "denoise": "afftdn=nf=-25",
            "speed": f"atempo={float(ctx.params.get('speed') or 1.25):.2f}",
            "extract": "",
        }
        if op not in filters:
            return fail(f"未知音频操作: {op}")

        out, fmt = _audio_output(out_dir, src, op,
                                 ctx.params.get("name") or "",
                                 ctx.params.get("format") or "")
        codec, lossy = AUDIO_OUTPUTS[fmt]
        args = ["-i", str(src), "-vn"]
        if filters[op]:
            args += ["-af", filters[op]]
        args += ["-c:a", codec]
        if lossy:
            args += ["-b:a", "192k"]
        args.append(str(out))
        try:
            run_ffmpeg(args, probe_duration(src), None, ctx)
        except KeyboardInterrupt:
            return cancelled()
        except Exception as exc:  # noqa: BLE001
            return fail(str(exc))
        return ok(out)


# ---------------------------------------------------------------- 封面

def _frame_times(duration: float, count: int, at) -> list[float]:
    """`at` 指定时只抽那一帧，否则按 count 均匀取。

    `at` 是注册表里公开的参数，之前完全没读，抽帧数永远等于 count。
    """
    if at is not None and str(at).strip() != "":
        try:
            second = float(at)
        except (TypeError, ValueError):
            second = None
        if second is not None and second >= 0:
            # 超过片长时 ffmpeg 抽不到画面，夹到结尾前一点
            return [min(second, max(duration - 0.05, 0.0))]
    count = max(1, int(count or 3))
    return [duration * (i + 1) / (count + 1) for i in range(count)]


class Thumbnail(Engine):
    id = "media.thumbnail"
    kinds = ("media",)

    def run(self, ctx) -> dict:
        src = Path(ctx.inputs.get("path") or "")
        if not src.exists():
            return fail("源文件不存在")
        out_dir = Path(ctx.params.get("dir") or src.parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        count = int(ctx.params.get("count") or 3)
        duration = probe_duration(src) or 10
        times = _frame_times(duration, count, ctx.params.get("at"))
        outputs = []
        for i, second in enumerate(times, 1):
            out = out_dir / f"{src.stem}_cover{i}.jpg"
            args = ["-ss", f"{second:.2f}", "-i", str(src), "-frames:v", "1",
                    "-q:v", "2", str(out)]
            try:
                run_ffmpeg(args, 0, None, ctx)
                outputs.append(str(out))
            except Exception:  # noqa: BLE001
                pass
            ctx.progress(round(i * 100 / len(times), 1), "抽帧中")
        if not outputs:
            return fail("没有抽出任何画面")
        return ok(*outputs)
