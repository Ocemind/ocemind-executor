# -*- coding: utf-8 -*-
"""产出登记：任务完成后的文件要能按项目查、反复登记不重复。"""
from __future__ import annotations

from pathlib import Path

import pytest

from ocemind.executor import artifacts
from ocemind.executor.store import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "executor.db")
    yield s
    s.close()


def make_task(root: Path, *names: str, **extra) -> dict:
    paths = []
    for name in names:
        path = root / name
        path.write_text("内容", encoding="utf-8")
        paths.append(str(path))
    task = {
        "id": "task1", "kind": "capability", "status": "done",
        "capability": "transcribe.asr", "project": "读书会",
        "sequence": "第 2 期", "url": "https://example.com/a",
        "outputs": paths, "path": paths[0] if paths else "",
        "created_at": 1_700_000_000,
    }
    task.update(extra)
    return task


def test_indexes_one_row_per_output(store, tmp_path):
    task = make_task(tmp_path, "a.mp4", "a.srt")
    rows = artifacts.index_task(store, task)
    assert len(rows) == 2
    assert {r["title"] for r in rows} == {"a.mp4", "a.srt"}
    assert {r["file_kind"] for r in rows} == {"video", "text"}
    assert all(r["project"] == "读书会" for r in rows)


def test_reindex_is_idempotent(store, tmp_path):
    task = make_task(tmp_path, "a.mp4")
    artifacts.index_task(store, task)
    artifacts.index_task(store, task)
    assert len(store.artifacts_for_task("task1")) == 1


def test_missing_file_is_not_indexed(store, tmp_path):
    task = make_task(tmp_path, "a.mp4")
    task["outputs"].append(str(tmp_path / "gone.mp4"))
    rows = artifacts.index_task(store, task)
    assert [r["title"] for r in rows] == ["a.mp4"]


def test_project_filter_and_listing(store, tmp_path):
    artifacts.index_task(store, make_task(tmp_path, "a.mp4", project="A"))
    other = make_task(tmp_path, "b.mp4", project="B",
                      id="task2", created_at=1_700_000_100)
    artifacts.index_task(store, other)

    assert len(store.list_artifacts()) == 2
    assert [r["title"] for r in store.list_artifacts(project="A")] == ["a.mp4"]
    assert store.projects() == ["B", "A"]  # 按最近创建倒序


def test_history_backfill_only_done_tasks(store, tmp_path):
    done = make_task(tmp_path, "a.mp4")
    running = dict(done, id="task2", status="running", outputs=[])
    assert artifacts.index_history(store, [done, running, done]) == 1
    assert len(store.list_artifacts()) == 1
    assert artifacts.index_history(store, [done]) == 0


def test_sync_state_can_be_updated(store, tmp_path):
    row = artifacts.index_task(store, make_task(tmp_path, "a.txt"))[0]
    store.update_artifact(row["id"], status="synced",
                          remote_url="https://x.feishu.cn/docx/abc")
    got = store.get_artifact(row["id"])
    assert got["status"] == "synced"
    assert got["remote_url"].endswith("/docx/abc")
    assert store.list_artifacts(status="unsynced") == []


def test_text_detection():
    assert artifacts.is_text("a.SRT")
    assert not artifacts.is_text("a.mp4")
    assert artifacts.file_kind("a.docx") == "doc"
