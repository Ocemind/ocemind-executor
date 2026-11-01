# -*- coding: utf-8 -*-
"""云端转写的降级与重试。

起因：硅基流动的 `Qwen/Qwen3-ASR-1.7B` 只认 `response_format=json`，
传 `verbose_json` 会被空响应的 400 拒绝；大文件上传途中服务端还会直接
掐连接，界面上表现为 `WinError 10054`。这些用例钉住：

- 400 空响应 / 连接被掐 → 自动降级成 json，任务不该失败
- 降级过的模型记住选择，下次直接发 json
- 参数是真的错（模型不存在）→ 立刻报出原文，不重试、不吞掉
- `srt=false` 时不问时间轴

纯离线，全部用假的 urlopen。
"""
from __future__ import annotations

import io
import json
import re
import urllib.error

import pytest

from ocemind.executor.engines import transcribe


class FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/audio/transcriptions", code,
                                  "err", {}, io.BytesIO(body))


def reset_error() -> urllib.error.URLError:
    return urllib.error.URLError(ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。"))


class FakeUrlopen:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


def field(req, name: str) -> str:
    match = re.search(rb'name="' + name.encode() + rb'"\r\n\r\n([^\r]*)',
                      req.data)
    return match.group(1).decode() if match else ""


@pytest.fixture(autouse=True)
def clean_memo(monkeypatch):
    transcribe._FORMAT_MEMO.clear()
    monkeypatch.setattr(transcribe.time, "sleep", lambda _s: None)
    yield
    transcribe._FORMAT_MEMO.clear()


def test_verbose_json_rejected_falls_back_to_json(tmp_path, monkeypatch):
    fake = FakeUrlopen([
        http_error(400),                                    # 空响应：格式不支持
        json.dumps({"text": "你好"}).encode(),
    ])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    raw = transcribe._post_transcription("https://api.siliconflow.cn/v1",
                                         "k", "Qwen/Qwen3-ASR-1.7B",
                                         audio, "zh", want_segments=True)

    assert raw["text"] == "你好"
    assert [field(r, "response_format") for r in fake.requests] == [
        "verbose_json", "json"]
    assert transcribe._FORMAT_MEMO[("https://api.siliconflow.cn/v1/audio/transcriptions",
                                    "Qwen/Qwen3-ASR-1.7B")] == "json"


def test_connection_reset_mid_upload_falls_back_to_json(tmp_path, monkeypatch):
    fake = FakeUrlopen([
        reset_error(),
        json.dumps({"text": "能转"}).encode(),
    ])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    raw = transcribe._post_transcription("https://api.siliconflow.cn/v1",
                                         "k", "Qwen/Qwen3-ASR-1.7B",
                                         audio, "zh", want_segments=True)

    assert raw["text"] == "能转"
    assert field(fake.requests[1], "response_format") == "json"


def test_remembered_format_skips_verbose_json(tmp_path, monkeypatch):
    transcribe._remember_format("https://api.siliconflow.cn/v1/audio/transcriptions",
                                "Qwen/Qwen3-ASR-1.7B", "json")
    fake = FakeUrlopen([json.dumps({"text": "再来一次"}).encode()])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    transcribe._post_transcription("https://api.siliconflow.cn/v1", "k",
                                   "Qwen/Qwen3-ASR-1.7B", audio, "zh",
                                   want_segments=True)

    assert len(fake.requests) == 1
    assert field(fake.requests[0], "response_format") == "json"


def test_invalid_model_error_is_not_swallowed(tmp_path, monkeypatch):
    body = json.dumps({"code": 20012,
                       "message": "Model does not exist. Please check it carefully."}).encode()
    fake = FakeUrlopen([http_error(400, body)])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    with pytest.raises(RuntimeError) as excinfo:
        transcribe._post_transcription("https://api.siliconflow.cn/v1", "k",
                                       "No/SuchModel", audio, "zh",
                                       want_segments=True)

    message = str(excinfo.value)
    assert "400" in message and "Model does not exist" in message
    assert len(fake.requests) == 1


def test_server_error_is_retried(tmp_path, monkeypatch):
    fake = FakeUrlopen([
        http_error(502, b"bad gateway"),
        json.dumps({"text": "恢复了"}).encode(),
    ])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    raw = transcribe._post_transcription("https://api.siliconflow.cn/v1", "k",
                                         "whisper-1", audio, "zh",
                                         want_segments=False)

    assert raw["text"] == "恢复了"
    assert len(fake.requests) == 2
    assert field(fake.requests[0], "response_format") == "json"


def test_repeated_reset_gives_clear_error(tmp_path, monkeypatch):
    fake = FakeUrlopen([reset_error(), reset_error(), reset_error()])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    with pytest.raises(RuntimeError) as excinfo:
        transcribe._post_transcription("https://api.siliconflow.cn/v1", "k",
                                       "FunAudioLLM/SenseVoiceSmall", audio,
                                       "zh", want_segments=False)

    assert "网络错误" in str(excinfo.value)
    assert "已重试 3 次" in str(excinfo.value)


def test_verbose_json_without_segments_remembers_text_only(tmp_path, monkeypatch):
    """SenseVoice 接受 verbose_json 但只回 text —— 下次别再问。"""
    fake = FakeUrlopen([
        json.dumps({"text": "只有文本", "language": "Chinese"}).encode(),
    ])
    monkeypatch.setattr(transcribe.urllib.request, "urlopen", fake)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fake")

    raw = transcribe._post_transcription("https://api.siliconflow.cn/v1", "k",
                                         "FunAudioLLM/SenseVoiceSmall", audio,
                                         "zh", want_segments=True)

    assert raw["text"] == "只有文本"
    assert transcribe._FORMAT_MEMO[("https://api.siliconflow.cn/v1/audio/transcriptions",
                                    "FunAudioLLM/SenseVoiceSmall")] == "json"


@pytest.mark.parametrize("value, expected", [
    (None, True), (True, True), (False, False), ("", False),
    ("false", False), ("0", False), ("true", True), (1, True),
])
def test_srt_flag_parsing(value, expected):
    assert transcribe._as_bool(value) is expected
