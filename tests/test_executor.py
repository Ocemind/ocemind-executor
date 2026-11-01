"""执行器的离线回归：不联网、不启动浏览器。

重点盯三件事：
1. 注册表说支持的能力，代码里真的有
2. 标准校验和幂等键的行为不漂移
3. 能力任务能真的走完队列（用一个纯本地引擎）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager
from ocemind.executor.engines import registry
from ocemind.executor.engines.engine_map import ENGINES, verify_answer
from ocemind.executor.facade import Executor, make_idem
from ocemind.executor.publish import standards
from ocemind.executor.publish.engine import snapshot_hash
from ocemind.executor.store import Store


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture()
def cfg(home: Path) -> Config:
    return Config(home)


# ---------------------------------------------------------------- 注册表

def test_every_capability_has_an_engine():
    """文档里写了支持，代码里没实现——这个测试就是拦这个的。"""
    assert verify_answer() == []


def test_registry_covers_thirteen_capabilities():
    ids = {c.id for c in registry.CAPABILITIES}
    assert len(ids) == 13
    for expected in ("download.platform", "transcribe.asr", "media.transcode",
                     "doc.parse", "image.ocr", "audio.tts", "publish.browser"):
        assert expected in ids


def test_engine_ids_are_unique():
    ids = [cls.id for cls in ENGINES.values()]
    assert len(ids) == len(set(ids))


def test_paid_capabilities_are_marked():
    paid = {c.id for c in registry.CAPABILITIES if c.paid}
    assert "transcribe.asr" in paid
    assert "audio.tts" in paid
    assert "download.platform" not in paid


# ---------------------------------------------------------------- 标准

def test_standards_reject_banned_words():
    problems = standards.check({"title": "史上最好的方法"})
    assert any("违禁词" in p for p in problems)


def test_standards_reject_long_title():
    problems = standards.check({"title": "字" * 40})
    assert any("标题超长" in p for p in problems)


def test_standards_accept_normal_title():
    assert standards.check({"title": "三分钟看懂缓存穿透"}) == []


def test_hashtag_limit():
    problems = standards.check({"title": "正常标题", "hashtags": [f"t{i}" for i in range(20)]})
    assert any("话题过多" in p for p in problems)


# ---------------------------------------------------------------- 幂等

def test_idem_is_stable_for_same_input(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    a = make_idem("doc.parse", {"path": str(f)}, {"dir": "d"})
    b = make_idem("doc.parse", {"path": str(f)}, {"dir": "d"})
    assert a == b


def test_idem_changes_when_content_changes(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    a = make_idem("doc.parse", {"path": str(f)}, {})
    time.sleep(0.01)
    f.write_text("y" * 10, encoding="utf-8")
    b = make_idem("doc.parse", {"path": str(f)}, {})
    assert a != b


def test_snapshot_hash_changes_with_meta(tmp_path: Path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0" * 32)
    a = snapshot_hash([str(f)], {"title": "A"})
    b = snapshot_hash([str(f)], {"title": "B"})
    assert a != b


# ---------------------------------------------------------------- 存储

def test_usage_accumulates(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.add_usage("siliconflow", "transcribe.asr", 60, 0.5)
    store.add_usage("siliconflow", "transcribe.asr", 60, 0.25)
    assert store.month_cost() == pytest.approx(0.75)
    store.close()


def test_accounts_roundtrip(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    store.upsert_account({"id": "a1", "platform": "bilibili", "label": "主号",
                          "profile_dir": str(tmp_path / "p"), "created_at": 1.0})
    assert store.get_account("a1")["label"] == "主号"
    store.touch_account("a1", status="ok", last_ok=True, cookie_expiry=123.0)
    acc = store.get_account("a1")
    assert acc["status"] == "ok"
    assert acc["cookie_expiry"] == 123.0
    store.delete_account("a1")
    assert store.get_account("a1") is None
    store.close()


# ---------------------------------------------------------------- 队列

def test_capability_job_runs_through_queue(home: Path, cfg: Config):
    """doc.parse 是纯本地的，正好拿来验证整条链路。"""
    home.mkdir(parents=True, exist_ok=True)
    src = home / "note.md"
    src.write_text("# 标题\n\n正文\n", encoding="utf-8")

    manager = TaskManager(cfg, start_scheduler=True)
    executor = Executor(cfg, manager)
    try:
        job = executor.submit("doc.parse", {"path": str(src)}, {"dir": str(home)})
        assert job["status"] in ("queued", "running")

        for _ in range(120):
            current = executor.get_job(job["id"])
            if current["status"] in ("done", "error", "cancelled", "paused"):
                break
            time.sleep(0.1)

        assert current["status"] == "done", current.get("error")
        outputs = current["outputs"]
        assert outputs and Path(outputs[0]).exists()
        # 不能让产物把输入覆盖掉
        assert Path(outputs[0]).resolve() != src.resolve()
    finally:
        executor.close()
        manager.shutdown()


def test_submit_unknown_capability_rejected(cfg: Config):
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        with pytest.raises(ValueError):
            executor.submit("nope.nothing", {}, {})
    finally:
        executor.close()
        manager.shutdown()


def test_submit_records_who_asked(cfg: Config):
    """谁下的任务要留痕：人工跑的还是 Agent 工单，产出归属不一样。"""
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        order = executor.submit("doc.parse", {"path": "/nope.md"}, {},
                                source="agent", order_id="T-20261101-01")
        assert order["source"] == "agent"
        assert order["order_id"] == "T-20261101-01"

        manual = executor.submit("doc.parse", {"path": "/nope2.md"}, {})
        assert manual["source"] == "manual"
        assert manual["order_id"] == ""
    finally:
        executor.close()
        manager.shutdown()


def test_claim_fills_project_without_moving_files(cfg: Config):
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        art = executor.store.add_artifact({
            "task_id": "t1", "path": "/nope/a.mp4", "title": "a.mp4",
        })
        assert [r["id"] for r in executor.store.list_artifacts(ungrouped=True)] == [art["id"]]

        result = executor.claim_artifacts([art["id"]], "读书会", "第 3 期")
        assert result["claimed"] == 1

        got = executor.store.get_artifact(art["id"])
        assert (got["project"], got["sequence"]) == ("读书会", "第 3 期")
        assert got["path"] == "/nope/a.mp4"        # 只补元数据，不搬文件
        assert executor.store.list_artifacts(ungrouped=True) == []

        with pytest.raises(ValueError):
            executor.claim_artifacts([art["id"]], "   ")
    finally:
        executor.close()
        manager.shutdown()


def test_budget_gate_blocks_paid_job(cfg: Config):
    cfg.update({"monthlyBudget": 1.0})
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        executor.store.add_usage("siliconflow", "transcribe.asr", 10, 5.0)
        job = executor.submit("transcribe.asr", {"path": "/nonexistent.mp4"}, {})
        result = manager._budget_block(job["capability"])
        assert "预算上限" in result
    finally:
        executor.close()
        manager.shutdown()


def test_account_state_expiry_buckets(cfg: Config):
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        now = time.time()
        assert executor._account_state({"cookie_expiry": now + 10 * 86400}, now)["state"] == "ok"
        assert executor._account_state({"cookie_expiry": now + 2 * 86400}, now)["state"] == "expiring"
        assert executor._account_state({"cookie_expiry": now - 1}, now)["state"] == "expired"
        assert executor._account_state({}, now)["state"] == "never"
    finally:
        executor.close()
        manager.shutdown()


def test_publish_requires_matching_snapshot(cfg: Config, tmp_path: Path):
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        acc = executor.add_account("bilibili", "测试号")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"0" * 16)
        record = executor.publish_create(acc["id"], [str(video)], {"title": "正常标题"})
        with pytest.raises(ValueError, match="快照"):
            executor.publish_approve(record["id"], "wrong-hash")
    finally:
        executor.close()
        manager.shutdown()


def test_publish_rejects_content_below_standard(cfg: Config, tmp_path: Path):
    manager = TaskManager(cfg, start_scheduler=False)
    executor = Executor(cfg, manager)
    try:
        acc = executor.add_account("bilibili", "测试号")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"0" * 16)
        with pytest.raises(ValueError, match="标准"):
            executor.publish_create(acc["id"], [str(video)], {"title": "史上最好的"})
    finally:
        executor.close()
        manager.shutdown()
