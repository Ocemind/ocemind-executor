# -*- coding: utf-8 -*-
"""引擎参数的回归：注册表里写了的参数，代码必须真的读。

这些用例对应一次全面测试里暴露的问题——`at` / `format` / 不带扩展名的
`name` 都被静默忽略，接口却报成功。纯离线，不联网、不花钱。
"""
from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from xiaoe_dl.downloader.config import Config
from xiaoe_dl.executor import backends
from xiaoe_dl.executor.artifacts import file_kind
from xiaoe_dl.executor.engines import audio_gen, image, media
from xiaoe_dl.executor.engines.base import Ctx
from xiaoe_dl.paths import ffmpeg_exe as find_ffmpeg


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
    """合成一段静音 WAV，用来测时长探测，不依赖任何外部样本。"""
    data = b"\x00\x00" * int(rate * seconds)
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
              + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate,
                                      rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(data)))
    return header + data


# ---------------------------------------------------------------- 抽帧

def test_thumbnail_at_takes_a_single_frame():
    assert media._frame_times(30.0, 3, 12.5) == [12.5]


def test_thumbnail_at_is_clamped_to_duration():
    # 超过片长时 ffmpeg 抽不到画面，夹到结尾前
    assert media._frame_times(10.0, 3, 99) == [pytest.approx(9.95)]


def test_thumbnail_at_empty_falls_back_to_count():
    times = media._frame_times(30.0, 3, "")
    assert len(times) == 3
    assert times[0] < times[1] < times[2]


def test_thumbnail_invalid_at_falls_back_to_count():
    assert len(media._frame_times(30.0, 2, "开头")) == 2


# ---------------------------------------------------------------- 音频输出

def test_audio_name_without_suffix_gets_format(tmp_path: Path):
    out, fmt = media._audio_output(tmp_path, tmp_path / "clip.mp4",
                                   "extract", "配音", "wav")
    assert out.name == "配音.wav" and fmt == "wav"


def test_audio_name_suffix_overrides_format(tmp_path: Path):
    out, fmt = media._audio_output(tmp_path, tmp_path / "clip.mp4",
                                   "extract", "配音.flac", "mp3")
    assert out.suffix == ".flac" and fmt == "flac"


def test_audio_default_format_is_m4a(tmp_path: Path):
    out, fmt = media._audio_output(tmp_path, tmp_path / "clip.mp4",
                                   "normalize", "", "")
    assert out.name == "clip_normalize.m4a" and fmt == "m4a"


def test_audio_unknown_format_falls_back(tmp_path: Path):
    _, fmt = media._audio_output(tmp_path, tmp_path / "clip.mp4",
                                 "extract", "", "exe")
    assert fmt == "m4a"


# ---------------------------------------------------------------- 图像

def test_image_edit_accepts_extensionless_name(tmp_path: Path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "photo.png"
    Image.new("RGB", (8, 8), "red").save(src)
    ctx = Ctx(job={"inputs": {"path": str(src)},
                   "params": {"op": "resize", "width": 4,
                              "name": "cover_small"}},
              config=SimpleNamespace(home=tmp_path), workdir=tmp_path)

    result = image.ImageEdit().run(ctx)
    assert result["ok"], result["error"]
    out = Path(result["outputs"][0])
    assert out.name == "cover_small.png"
    assert Image.open(out).width == 4


# ---------------------------------------------------------------- OCR 适配

def test_paddleocr_requires_paddlepaddle(monkeypatch):
    present = {"paddleocr"}
    monkeypatch.setattr(backends, "_module_present", lambda name: name in present)
    engine = {"id": "paddleocr", "module": "paddleocr", "requires": ("paddle",)}
    # 只有壳没有推理后端，不算装了
    assert backends._engine_available(engine) is False
    present.add("paddle")
    assert backends._engine_available(engine) is True


def test_tesseract_needs_system_binary(monkeypatch):
    monkeypatch.setattr(backends, "_module_present", lambda name: True)
    monkeypatch.setattr(backends.shutil, "which", lambda name: "")
    assert backends._engine_available({"id": "tesseract",
                                       "module": "pytesseract"}) is False
    monkeypatch.setattr(backends.shutil, "which", lambda name: "tesseract.exe")
    assert backends._engine_available({"id": "tesseract",
                                       "module": "pytesseract"}) is True


def test_paddle_lines_parses_2x_shape():
    # 2.x 是 pages → items → [box, (text, score)]
    result = [[
        [[[0, 0], [1, 1]], ("你好", 0.99)],
        [[[0, 2], [1, 3]], ("世界", 0.98)],
    ]]
    assert image._paddle_lines(result) == ["你好", "世界"]


def test_paddle_lines_parses_3x_dict():
    assert image._paddle_lines([{"rec_texts": ["第一行", "第二行"]}]) == [
        "第一行", "第二行"]


def test_paddle_lines_parses_3x_result_object():
    page = SimpleNamespace(res={"res": {"rec_texts": ["行一"]}})
    assert image._paddle_lines([page]) == ["行一"]


# ---------------------------------------------------------------- 音频时长

@pytest.mark.skipif(not find_ffmpeg(), reason="本机没有 ffmpeg")
def test_audio_seconds_measures_wav():
    assert audio_gen._audio_seconds(wav_bytes(1.0), ".wav") == pytest.approx(1.0, abs=0.1)


@pytest.mark.skipif(not find_ffmpeg(), reason="本机没有 ffmpeg")
def test_tts_rejects_empty_clip(tmp_path: Path, monkeypatch):
    cfg = Config(tmp_path / "home", legacy_home=tmp_path / "home")
    monkeypatch.setattr(audio_gen.backends, "resolve",
                        lambda *a, **k: {"ready": True, "key": "k",
                                         "base": "https://example.invalid/v1",
                                         "model": "m", "provider": "siliconflow",
                                         "provider_name": "假服务商",
                                         "engine": "siliconflow"})
    monkeypatch.setattr(audio_gen, "_post_audio",
                        lambda *a, **k: wav_bytes(0.05))
    ctx = Ctx(job={"inputs": {}, "params": {"text": "测试"}},
              config=cfg, workdir=tmp_path)

    result = audio_gen.Tts().run(ctx)
    assert result["ok"] is False
    assert "秒" in result["error"]


@pytest.mark.skipif(not find_ffmpeg(), reason="本机没有 ffmpeg")
def test_tts_accepts_normal_clip(tmp_path: Path, monkeypatch):
    cfg = Config(tmp_path / "home", legacy_home=tmp_path / "home")
    monkeypatch.setattr(audio_gen.backends, "resolve",
                        lambda *a, **k: {"ready": True, "key": "k",
                                         "base": "https://example.invalid/v1",
                                         "model": "m", "provider": "siliconflow",
                                         "provider_name": "假服务商",
                                         "engine": "siliconflow"})
    monkeypatch.setattr(audio_gen, "_post_audio",
                        lambda *a, **k: wav_bytes(1.0))
    ctx = Ctx(job={"inputs": {}, "params": {"text": "测试",
                                            "dir": str(tmp_path)}},
              config=cfg, workdir=tmp_path)

    result = audio_gen.Tts().run(ctx)
    assert result["ok"], result["error"]


# ---------------------------------------------------------------- 产出归类

def test_file_kind_sniffs_extensionless_audio(tmp_path: Path):
    path = tmp_path / "douyin_audio"
    path.write_bytes(b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00")
    assert file_kind(str(path)) == "audio"


def test_file_kind_sniffs_extensionless_video(tmp_path: Path):
    path = tmp_path / "douyin_video"
    path.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00")
    assert file_kind(str(path)) == "video"


def test_file_kind_sniffs_wav(tmp_path: Path):
    path = tmp_path / "clip"
    path.write_bytes(wav_bytes(0.1))
    assert file_kind(str(path)) == "audio"


def test_file_kind_unknown_stays_other(tmp_path: Path):
    path = tmp_path / "mystery"
    path.write_bytes(b"not a media file at all")
    assert file_kind(str(path)) == "other"
