# -*- coding: utf-8 -*-
"""账号浏览器目录的占用统计与清理。

重点不是「删得干净」，是「别删错」：登录态、正在用的账号、还活着的目录，
一个都不能动。
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocemind.executor import gc


def _profile(home: Path, name: str) -> Path:
    root = home / "profiles" / name
    (root / "Default").mkdir(parents=True)
    (root / "Default" / "Cookies").write_text("login state", encoding="utf-8")
    (root / "Default" / "Cache").mkdir()
    (root / "Default" / "Cache" / "f_000001").write_bytes(b"x" * 4096)
    (root / "GrShaderCache").mkdir()
    (root / "GrShaderCache" / "data").write_bytes(b"y" * 2048)
    return root


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    live = _profile(home, "bilibili-aaa")
    orphan = _profile(home, "douyin-zzz")
    cfg = SimpleNamespace(home=home)
    accounts = [{"id": "a1", "label": "主号", "platform": "bilibili",
                 "profile_dir": str(live)}]
    return SimpleNamespace(cfg=cfg, accounts=accounts, live=live, orphan=orphan)


def test_scan_counts(setup):
    got = gc.scan(setup.cfg, setup.accounts, running_ids=[])
    assert len(got["profiles"]) == 2
    assert got["total"] > 0
    assert got["cache"] > 0
    assert got["orphan_total"] > 0

    rows = {r["name"]: r for r in got["profiles"]}
    assert rows["bilibili-aaa"]["orphan"] is False
    assert rows["bilibili-aaa"]["label"] == "主号"
    assert rows["douyin-zzz"]["orphan"] is True


def test_account_profile_outside_root(setup, tmp_path):
    """账号记录指到别处的目录（改过名、搬过家）也要算进来，不能当成没账号。"""
    moved = _profile(tmp_path, "elsewhere/bilibili-aaa")
    accounts = [{"id": "a1", "label": "主号", "platform": "bilibili",
                 "profile_dir": str(moved)}]
    got = gc.scan(setup.cfg, accounts, running_ids=[])

    rows = {r["path"]: r for r in got["profiles"]}
    assert str(moved) in rows
    assert rows[str(moved)]["orphan"] is False
    # profiles/ 下那份老的没人认领
    assert rows[str(setup.live)]["orphan"] is True
    # 清缓存两边都清，孤儿目录留着
    got = gc.clean(setup.cfg, accounts, running_ids=[])
    assert not (moved / "Default" / "Cache").exists()
    assert (moved / "Default" / "Cookies").is_file()
    assert setup.live.is_dir()


def test_scan_marks_running(setup):
    got = gc.scan(setup.cfg, setup.accounts, running_ids=["a1"])
    rows = {r["name"]: r for r in got["profiles"]}
    assert rows["bilibili-aaa"]["running"] is True


def test_clean_keeps_login_state(setup):
    got = gc.clean(setup.cfg, setup.accounts, running_ids=[])
    assert got["freed"] > 0
    assert (setup.live / "Default" / "Cookies").is_file()      # 登录态在
    assert not (setup.live / "Default" / "Cache").exists()      # 缓存在删
    assert not (setup.live / "GrShaderCache").exists()
    assert setup.orphan.is_dir()                                # 孤儿目录不顺手删


def test_clean_skips_running_profile(setup):
    got = gc.clean(setup.cfg, setup.accounts, running_ids=["a1"])
    assert (setup.live / "Default" / "Cache").is_dir()
    assert not any(r.startswith("bilibili-aaa") for r in got["removed"])


def test_clean_orphans_only_when_asked(setup):
    got = gc.clean(setup.cfg, setup.accounts, running_ids=[], orphans=True)
    assert not setup.orphan.exists()
    assert "douyin-zzz" in got["removed"]


def test_result_is_remembered(setup):
    assert gc.last_result(setup.cfg) is None
    got = gc.clean(setup.cfg, setup.accounts, running_ids=[])
    last = gc.last_result(setup.cfg)
    assert last["freed"] == got["freed"]
    assert last["at"] == got["at"]
    # 上次结果里的 freed 要能在界面上说清楚
    assert gc.human_size(got["freed"]).endswith(("B", "KB", "MB"))


def test_missing_root_is_not_an_error(tmp_path):
    cfg = SimpleNamespace(home=tmp_path / "nope")
    got = gc.scan(cfg, [])
    assert got["profiles"] == []
    assert got["total"] == 0


def test_human_size():
    assert gc.human_size(0) == "0 B"
    assert gc.human_size(2048) == "2.0 KB"
    assert gc.human_size(37 * 1024 * 1024) == "37.0 MB"
