# -*- coding: utf-8 -*-
"""发布状态机：审核中 / 需人工 不能被当成失败。

报「失败」的代价很具体：人以为没发出去，又点一次，平台上就多一条。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager
from ocemind.executor.facade import Executor
from ocemind.executor.publish import states


# ------------------------------------------------------------- 状态推导

@pytest.mark.parametrize("job_status,engine,want", [
    ("queued", "", states.APPROVED),
    ("running", "", states.RUNNING),
    ("done", "", states.VERIFIED),
    ("done", states.REVIEWING, states.REVIEWING),
    # 引擎说审核中，任务这边记成 error —— 仍然是审核中，不是失败
    ("error", states.REVIEWING, states.REVIEWING),
    ("error", states.MANUAL, states.MANUAL),
    ("error", "", states.FAILED),
    ("cancelled", "", states.FAILED),
    # 软件重启打断的任务：文件传到一半，得重来
    ("paused", "", states.FAILED),
])
def test_derive(job_status, engine, want):
    assert states.derive(job_status, engine) == want


def test_labels_cover_every_state():
    for state in (states.DRAFT, states.APPROVED, states.RUNNING, states.VERIFIED,
                  states.REVIEWING, states.MANUAL, states.SCHEDULED, states.FAILED):
        assert states.label(state) not in ("", state)


def test_reviewing_is_not_retryable():
    """审核中的记录再点一次批准，就是同一条发两次。"""
    assert states.REVIEWING not in states.RETRYABLE
    assert states.MANUAL in states.RETRYABLE
    assert states.FAILED in states.RETRYABLE


def test_tone_colors():
    assert states.tone(states.VERIFIED) == "ok"
    assert states.tone(states.REVIEWING) == "warn"
    assert states.tone(states.FAILED) == "bad"


def test_summary_counts():
    recs = [{"state": "reviewing"}, {"state": "reviewing"}, {"state": "manual"}]
    assert states.summary(recs) == {"reviewing": 2, "manual": 1}


# ------------------------------------------------------------- 门面同步

@pytest.fixture()
def ex(tmp_path: Path):
    cfg = Config(tmp_path / "home")
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        yield executor
    finally:
        executor.close()
        manager.shutdown()


def _record(executor, job_id: str = "", state: str = "approved") -> dict:
    account = executor.add_account("bilibili", "主号")
    rec = {
        "id": "r1", "account_id": account["id"], "job_id": job_id,
        "platform": "bilibili", "snapshot_hash": "deadbeef",
        "state": state, "remote_ref": "",
        "result": {"files": ["C:/x/demo.mp4"], "meta": {"title": "演示"}},
        "created_at": time.time(),
    }
    executor.store.upsert_publish(rec)
    return rec


def _fake_job(executor, job_id: str, status: str, engine_state: str = "",
              error: str = "") -> None:
    with executor.manager.lock:
        executor.manager.tasks[job_id] = {
            "id": job_id, "status": status, "error": error, "stage": "",
            "meta": {"publish_state": engine_state} if engine_state else {},
        }


def test_list_shows_reviewing_not_failed(ex):
    _record(ex, job_id="j1")
    _fake_job(ex, "j1", "error", states.REVIEWING, "正在审核")
    got = ex.publish_list()
    rec = got["records"][0]
    assert rec["state"] == states.REVIEWING
    assert rec["state_text"] == "审核中"
    assert rec["tone"] == "warn"
    assert rec["can_verify"] is True
    assert rec["can_retry"] is False
    assert rec["error"] == "正在审核"


def test_list_shows_manual_when_stopped_at_preview(ex):
    _record(ex, job_id="j2")
    _fake_job(ex, "j2", "done", states.MANUAL)
    rec = ex.publish_list()["records"][0]
    assert rec["state"] == states.MANUAL
    assert rec["can_verify"] is True


def test_list_syncs_running_and_done(ex):
    _record(ex, job_id="j3")
    _fake_job(ex, "j3", "running")
    assert ex.publish_list()["records"][0]["state"] == states.RUNNING
    _fake_job(ex, "j3", "done", states.VERIFIED)
    assert ex.publish_list()["records"][0]["state"] == states.VERIFIED


def test_state_is_persisted(ex):
    _record(ex, job_id="j4")
    _fake_job(ex, "j4", "error", states.REVIEWING)
    ex.publish_list()
    # 重新从库里读，状态已经写进去了（不用每次靠任务还在内存里）
    assert ex.store.get_publish("r1")["state"] == states.REVIEWING


def test_approve_blocks_verified_and_allows_manual(ex):
    _record(ex, job_id="j5")
    _fake_job(ex, "j5", "done", states.VERIFIED)
    ex.publish_list()
    with pytest.raises(ValueError):
        ex.publish_approve("r1", "deadbeef")

    rec = ex.store.get_publish("r1")
    rec["state"] = states.MANUAL
    ex.store.upsert_publish(rec)
    got = ex.publish_approve("r1", "deadbeef")
    assert got["record"]["state"] == states.APPROVED
    assert got["job"]["capability"] == "publish.browser"
    # 任务号必须真的落库，否则状态机跟不到任务，记录会永远停在「已批准」
    assert ex.store.get_publish("r1")["job_id"] == got["job"]["id"]


def test_verify_only_allowed_for_reviewing(ex):
    _record(ex, job_id="j6")
    _fake_job(ex, "j6", "error", states.REVIEWING)
    ex.publish_list()
    got = ex.publish_approve("r1", "deadbeef", verify_only=True)
    assert got["job"]["title"].startswith("复查")
    assert got["job"]["params"]["verify_only"] is True
    assert got["job"]["params"]["submit"] is False


def test_verify_only_sets_no_idem(ex):
    """复查每次都要真去后台看，不能被上一次的任务命中。"""
    _record(ex, job_id="j7")
    _fake_job(ex, "j7", "error", states.REVIEWING)
    ex.publish_list()
    got = ex.publish_approve("r1", "deadbeef", verify_only=True)
    assert got["job"]["idem"] == ""


def test_verify_only_rejected_when_verified(ex):
    _record(ex, job_id="j8")
    _fake_job(ex, "j8", "done", states.VERIFIED)
    ex.publish_list()
    with pytest.raises(ValueError):
        ex.publish_approve("r1", "deadbeef", verify_only=True)
