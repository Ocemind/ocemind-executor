# -*- coding: utf-8 -*-
"""定时发布：到点自己跑，错过了不能偷偷补发。

错过补发比不发更糟：半夜定好的东西早上九点突然发出去，人不在跟前。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager
from ocemind.executor.facade import Executor
from ocemind.executor.publish import states


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


def _draft(ex: Executor, title: str = "演示") -> dict:
    account = ex.add_account("bilibili", "主号")
    rec = {
        "id": "r1", "account_id": account["id"], "job_id": "",
        "platform": "bilibili", "snapshot_hash": "abc123",
        "state": states.DRAFT, "remote_ref": "",
        "result": {"files": ["C:/x/demo.mp4"], "meta": {"title": title}},
        "created_at": time.time(),
    }
    ex.store.upsert_publish(rec)
    return rec


def test_future_schedule_makes_no_task(ex):
    _draft(ex)
    when = time.time() + 3600
    got = ex.publish_approve("r1", "abc123", scheduled_at=when)
    assert got["job"] is None
    rec = ex.store.get_publish("r1")
    assert rec["state"] == states.SCHEDULED
    assert rec["result"]["scheduled_at"] == when
    # 不能现在就往队列里放任务
    assert not [t for t in ex.manager.list() if t.get("capability") == "publish.browser"]


def test_past_time_fires_immediately(ex):
    _draft(ex)
    got = ex.publish_approve("r1", "abc123", scheduled_at=time.time() - 10)
    assert got["job"] is not None
    assert ex.store.get_publish("r1")["state"] == states.APPROVED


def test_tick_waits_then_fires(ex):
    _draft(ex)
    when = time.time() + 100
    ex.publish_approve("r1", "abc123", scheduled_at=when, submit=True)

    assert ex.publish_tick(now=when - 50) == []          # 没到点
    assert ex.store.get_publish("r1")["state"] == states.SCHEDULED

    fired = ex.publish_tick(now=when + 1)
    assert fired == ["r1"]
    rec = ex.store.get_publish("r1")
    assert rec["state"] == states.APPROVED
    assert rec["job_id"]
    assert "scheduled_at" not in rec["result"]
    assert rec["result"]["fired_at"] == when + 1

    job = ex.manager.get(rec["job_id"])
    assert job["params"]["submit"] is True


def test_tick_is_idempotent(ex):
    _draft(ex)
    when = time.time() + 10
    ex.publish_approve("r1", "abc123", scheduled_at=when)
    assert ex.publish_tick(now=when + 1) == ["r1"]
    assert ex.publish_tick(now=when + 2) == []      # 第二拍不能再派一次


def test_missed_schedule_is_not_posted(ex):
    """软件关着错过了时间，超过宽限就不补发，改成需人工。"""
    _draft(ex)
    when = time.time() - Executor.SCHEDULE_GRACE - 60
    # 模拟「定好时之后软件被关掉，开机时早过了点」：直接写一条到期的定时记录
    rec = ex.store.get_publish("r1")
    rec["state"] = states.SCHEDULED
    rec["result"]["scheduled_at"] = when
    ex.store.upsert_publish(rec)

    fired = ex.publish_tick()
    assert fired == []
    rec = ex.store.get_publish("r1")
    assert rec["state"] == states.MANUAL
    assert rec["result"]["scheduled_missed"] == when
    assert not [t for t in ex.manager.list() if t.get("capability") == "publish.browser"]

    view = ex.publish_list()["records"][0]
    assert "错过" in view["hint"]


def test_cancel_schedule(ex):
    _draft(ex)
    ex.publish_approve("r1", "abc123", scheduled_at=time.time() + 600)
    got = ex.publish_cancel_schedule("r1")
    assert got["state"] == states.DRAFT
    assert got["scheduled_at"] == 0
    assert ex.store.get_publish("r1")["state"] == states.DRAFT


def test_cancel_rejects_non_scheduled(ex):
    _draft(ex)
    with pytest.raises(ValueError):
        ex.publish_cancel_schedule("r1")


def test_scheduled_record_survives_list_sync(ex):
    """列表同步不能把定时待发推成别的状态。"""
    _draft(ex)
    ex.publish_approve("r1", "abc123", scheduled_at=time.time() + 600)
    got = ex.publish_list()
    assert got["records"][0]["state"] == states.SCHEDULED
    assert got["records"][0]["can_cancel_schedule"] is True
    assert got["counts"] == {"scheduled": 1}


def test_tick_hook_is_registered_once(ex):
    hooks = [h for h in ex.manager.tick_hooks]
    assert hooks.count(ex._publish_tick_hook) == 1
    ex.manager.add_tick_hook(ex._publish_tick_hook)
    assert ex.manager.tick_hooks.count(ex._publish_tick_hook) == 1


def test_tick_hook_throttles(ex):
    """0.4 秒一拍，不能每拍都查一次库。"""
    _draft(ex)
    ex.publish_approve("r1", "abc123", scheduled_at=time.time() + 600)
    calls = []
    original = ex.publish_tick
    ex.publish_tick = lambda now=None: calls.append(now) or []
    try:
        ex._publish_tick_hook(1000.0)
        ex._publish_tick_hook(1001.0)
        ex._publish_tick_hook(1006.0)
    finally:
        ex.publish_tick = original
    assert len(calls) == 2
