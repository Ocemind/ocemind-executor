# -*- coding: utf-8 -*-
"""飞书桥：不真的调 lark-cli，只验证命令拼得对、失败不炸任务。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocemind.downloader.config import Config
from ocemind.executor import feishu
from ocemind.executor.store import Store


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    home = tmp_path / "home"
    cfg = Config(home)
    cfg.update({
        "feishuEnabled": True,
        "feishuBaseToken": "apptok",
        "feishuTableId": "tblidx",
        "feishuFolderToken": "foldtok",
        "feishuDocMode": "text",
    })
    feishu.invalidate_cache()
    return cfg


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "executor.db")
    yield s
    s.close()


@pytest.fixture()
def calls(monkeypatch):
    """假冒 lark-cli，把每次调用的参数记下来。"""
    seen: list[dict] = []
    state = {"doc_url": "https://x.feishu.cn/docx/abc123",
             "batch_code": 0, "doc_code": 0}

    def fake(cfg, args, timeout=60):
        seen.append({"args": list(args), "timeout": timeout})
        if args[:2] == ["base", "describe"]:
            return 0, json.dumps({"data": {"fields": [
                {"field_name": name} for name in feishu.REQUIRED_FIELDS]}}), ""
        if args[:2] == ["base", "+record-batch-create"]:
            if state["batch_code"]:
                return state["batch_code"], "", "field not found"
            return 0, '{"ok": true}', ""
        if args[:2] == ["docs", "+create"]:
            if state["doc_code"]:
                return state["doc_code"], "", "文档创建失败"
            return 0, json.dumps({"data": {"url": state["doc_url"]}}), ""
        if args[:1] == ["--version"]:
            return 0, "lark-cli 1.2.3", ""
        if args[:2] == ["auth", "status"]:
            return 0, json.dumps({"data": {"logged_in": True}}), ""
        return 1, "", "unexpected"

    monkeypatch.setattr(feishu, "_run", fake)
    return seen, state


def add_artifact(store: Store, tmp_path: Path, name: str, project: str = "项目") -> dict:
    path = tmp_path / name
    path.write_text("这是测试内容", encoding="utf-8")
    return store.add_artifact({
        "task_id": "t1", "project": project, "sequence": "S1",
        "capability": "transcribe.asr", "title": name,
        "file_kind": "text" if feishu.artifact_mod.is_text(name) else "video",
        "path": str(path), "size": path.stat().st_size,
        "source": "https://example.com/v", "created_at": 1_700_000_000,
    })


def test_parse_base_input_accepts_table_url():
    base, table = feishu.parse_base_input(
        "https://x.feishu.cn/base/apptok123?table=tblabc&view=vew", "")
    assert (base, table) == ("apptok123", "tblabc")


def test_table_check_reports_missing_columns(cfg, calls):
    seen, _ = calls
    result = feishu.check_table(cfg)
    assert result["ok"] is True
    assert result["missing"] == []
    assert any(c["args"][:2] == ["base", "describe"] for c in seen)


def test_create_doc_writes_markdown_and_reads_url(cfg, calls):
    seen, _ = calls
    url = feishu.create_doc(cfg, "转写稿", "# 标题\n正文", "foldtok")
    assert url == "https://x.feishu.cn/docx/abc123"
    args = next(c["args"] for c in seen if c["args"][:2] == ["docs", "+create"])
    assert "--doc-format" in args and "markdown" in args
    assert "--folder-token" in args


def test_create_doc_falls_back_without_folder(cfg, monkeypatch):
    attempts: list[list[str]] = []

    def fake(cfg, args, timeout=60):
        attempts.append(list(args))
        if len(attempts) == 1:
            return 1, "", "unknown flag: --folder-token"
        return 0, '{"url": "https://x.feishu.cn/docx/fallback"}', ""

    monkeypatch.setattr(feishu, "_run", fake)
    url = feishu.create_doc(cfg, "t", "x", "foldtok")
    assert url.endswith("/docx/fallback")
    assert "--folder-token" not in attempts[1]


def test_sync_creates_docs_only_for_text(cfg, store, tmp_path, calls):
    seen, _ = calls
    text = add_artifact(store, tmp_path, "a.srt")
    video = add_artifact(store, tmp_path, "a.mp4")
    result = feishu.sync_artifacts(cfg, store, [text, video])

    assert result["synced"] == 2
    assert result["failed"] == 0
    doc_calls = [c for c in seen if c["args"][:2] == ["docs", "+create"]]
    assert len(doc_calls) == 1

    batch = next(c for c in seen
                 if c["args"][:2] == ["base", "+record-batch-create"])
    payload = json.loads(batch["args"][batch["args"].index("--json") + 1])
    rows = payload["create_records"]
    assert len(rows) == 2
    assert {r["标题"] for r in rows} == {"a.srt", "a.mp4"}
    assert rows[0]["项目"] == "项目"
    assert any(r["云文档"].endswith("/docx/abc123") for r in rows)

    saved = {r["title"]: r for r in store.artifacts_for_task("t1")}
    assert saved["a.srt"]["status"] == "synced"
    assert saved["a.srt"]["remote_url"].endswith("/docx/abc123")
    assert saved["a.mp4"]["status"] == "synced"


def test_batch_failure_is_recorded_not_raised(cfg, store, tmp_path, calls):
    _, state = calls
    state["batch_code"] = 1
    text = add_artifact(store, tmp_path, "a.srt")
    video = add_artifact(store, tmp_path, "a.mp4")
    result = feishu.sync_artifacts(cfg, store, [text, video])

    assert result["partial"] == 1
    assert result["failed"] == 1
    assert result["errors"]
    saved = {r["title"]: r for r in store.artifacts_for_task("t1")}
    # 文档已经建好、只是没写进索引 → 部分同步，不能谎报全失败
    assert saved["a.srt"]["status"] == "partial"
    assert saved["a.srt"]["remote_url"].endswith("/docx/abc123")
    assert saved["a.mp4"]["status"] == "failed"


def test_sync_without_table_keeps_doc_and_reports_partial(cfg, store, tmp_path, calls):
    cfg.update({"feishuBaseToken": "", "feishuTableId": ""})
    feishu.invalidate_cache()
    text = add_artifact(store, tmp_path, "a.srt")
    result = feishu.sync_artifacts(cfg, store, [text])
    saved = store.artifacts_for_task("t1")[0]
    assert result["partial"] == 1
    assert saved["status"] == "partial"
    assert saved["remote_url"]


def test_retry_after_doc_failure_does_not_duplicate_index_row(cfg, store, tmp_path, calls):
    seen, state = calls
    state["doc_code"] = 1
    art = add_artifact(store, tmp_path, "a.srt")
    first = feishu.sync_artifacts(cfg, store, [art])
    assert first["partial"] == 1
    batches = [c for c in seen if c["args"][:2] == ["base", "+record-batch-create"]]
    assert len(batches) == 1

    state["doc_code"] = 0
    retry = store.artifacts_for_task("t1")[0]
    second = feishu.sync_artifacts(cfg, store, [retry])
    assert second["synced"] == 1
    batches = [c for c in seen if c["args"][:2] == ["base", "+record-batch-create"]]
    # 索引行已经写过了，补文档时不能再插一行
    assert len(batches) == 1
    saved = store.artifacts_for_task("t1")[0]
    assert saved["status"] == "synced"
    assert saved["remote_url"]


def test_status_says_not_installed(cfg, monkeypatch):
    monkeypatch.setattr(feishu, "find_cli", lambda cfg: "")
    info = feishu.status(cfg)
    assert info["installed"] is False
    assert "lark-cli" in info["message"]
