# -*- coding: utf-8 -*-
"""执行后端的选择规则：本地优先、云端按能力选、显式选择不被偷偷替换。"""
from __future__ import annotations

from pathlib import Path

import pytest

from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager
from ocemind.executor import backends


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    home = tmp_path / "home"
    return Config(home, legacy_home=home)


@pytest.fixture()
def engines(monkeypatch):
    """把「装没装」变成可控的，不依赖这台机器上真的装了什幺。"""
    def install(*available: str):
        def fake(kind: str):
            return [
                {"id": "faster-whisper", "name": "faster-whisper",
                 "module": "faster_whisper", "package": "faster-whisper",
                 "size": "", "note": "", "available": "faster-whisper" in available},
                {"id": "whisper", "name": "openai-whisper",
                 "module": "whisper", "package": "openai-whisper",
                 "size": "", "note": "", "available": "whisper" in available},
            ]
        monkeypatch.setattr(backends, "local_engines", fake)
    return install


def test_auto_prefers_installed_local_engine(cfg, engines):
    engines("faster-whisper")
    target = backends.resolve(cfg, "transcribe.asr")
    assert target["backend"] == "local"
    assert target["engine"] == "faster-whisper"
    assert target["ready"] is True
    assert target["key"] == ""


def test_auto_falls_back_to_cloud_without_local(cfg, engines):
    engines()
    target = backends.resolve(cfg, "transcribe.asr")
    assert target["backend"] == "cloud"
    assert target["provider"] == "siliconflow"
    # 没填密钥就不算就绪，界面要能把原因说清楚
    assert target["ready"] is False
    assert "密钥" in target["problem"]


def test_cloud_ready_once_key_is_present(cfg, engines):
    engines()
    cfg.update({"siliconflowKey": "sk-test"})
    target = backends.resolve(cfg, "transcribe.asr")
    assert target["backend"] == "cloud"
    assert target["ready"] is True
    assert target["base"] == "https://api.siliconflow.cn/v1"
    assert target["model"] == "FunAudioLLM/SenseVoiceSmall"


def test_per_capability_provider_switching(cfg, engines):
    engines()
    cfg.update({"groqKey": "g-test", "deepseekKey": "d-test"})
    backends.set_choice(cfg, "transcribe.asr", {"backend": "cloud", "provider": "groq"})
    backends.set_choice(cfg, "media.subtitle", {"backend": "cloud", "provider": "deepseek"})
    asr = backends.resolve(cfg, "transcribe.asr")
    sub = backends.resolve(cfg, "media.subtitle")
    assert asr["provider"] == "groq" and asr["model"] == "whisper-large-v3"
    assert sub["provider"] == "deepseek" and sub["model"] == "deepseek-chat"


def test_explicit_local_without_engine_reports_problem(cfg, engines):
    engines()
    backends.set_choice(cfg, "image.ocr", {"backend": "local"})
    target = backends.resolve(cfg, "image.ocr")
    assert target["backend"] == "local"
    assert target["ready"] is False
    assert "本地引擎" in target["problem"]


def test_explicit_local_engine_is_not_silently_swapped(cfg, engines):
    engines("whisper")          # 只装了 openai-whisper
    backends.set_choice(cfg, "transcribe.asr",
                        {"backend": "local", "localEngine": "faster-whisper"})
    target = backends.resolve(cfg, "transcribe.asr")
    # 用户点名要 faster-whisper，没装就报错，不能改跑 whisper，更不能走云端
    assert target["ready"] is False
    assert target["engine"] == ""
    assert target["backend"] == "local"


def test_cloud_only_capability_rejects_local(cfg, engines):
    engines("faster-whisper")
    backends.set_choice(cfg, "image.generate", {"backend": "local"})
    target = backends.resolve(cfg, "image.generate")
    assert target["ready"] is False
    assert "只能走 API" in target["problem"]


def test_set_choice_ignores_provider_not_allowed(cfg):
    backends.set_choice(cfg, "transcribe.asr",
                        {"backend": "cloud", "provider": "dashscope"})
    # 转写不支持百炼，落库时丢掉，别让界面显示出无效选项
    assert "provider" not in (cfg.get("models") or {})["transcribe.asr"]


def test_local_run_skips_budget_gate(cfg, engines):
    engines("faster-whisper")
    cfg.update({"monthlyBudget": 1.0})
    backends.set_choice(cfg, "transcribe.asr", {"backend": "local"})
    manager = TaskManager(cfg, start_scheduler=False)
    try:
        from ocemind.executor.store import Store
        manager.store = Store(Path(cfg.home) / "executor.db")
        manager.store.add_usage("siliconflow", "transcribe.asr", 10, 5.0)
        assert manager._budget_block("transcribe.asr") == ""
    finally:
        if manager.store:
            manager.store.close()
        manager.shutdown()
