# -*- coding: utf-8 -*-
"""批量错峰：一次粘好几条时，别让它们同时开跑。

只测调度决策（`_pick_starts`），不起调度线程、不联网——真正开跑会去
下载，这里只关心「这一拍该放几条」。
"""
import pytest

from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager

URLS = [f"https://example.com/v{i}.mp4" for i in range(3)]


@pytest.fixture
def cfg(tmp_path):
    return Config(home=tmp_path / "home")


@pytest.fixture
def mgr(cfg):
    m = TaskManager(cfg, start_scheduler=False)
    yield m
    m.shutdown()


def _add_all(mgr, cfg, n=3):
    for url in URLS[:n]:
        mgr.add(url, "file", 0, str(cfg.home), 0)


def test_starts_one_at_a_time(mgr, cfg):
    """默认有间隔：一拍只放一条，不会一次把空位填满。"""
    cfg.update({"maxConcurrent": 3, "startStagger": 1.2})
    _add_all(mgr, cfg)

    first = mgr._pick_starts(now=100.0)
    assert len(first) == 1
    mgr._last_start = 100.0

    # 才过了 0.4 秒（调度线程的节拍），还没到间隔
    assert mgr._pick_starts(now=100.4) == []

    # 过了间隔就能放下一条
    assert len(mgr._pick_starts(now=101.3)) == 1


def test_zero_stagger_keeps_old_behaviour(mgr, cfg):
    """间隔设 0 时退回老逻辑：有几个空位就起几个。"""
    cfg.update({"maxConcurrent": 3, "startStagger": 0})
    _add_all(mgr, cfg)
    assert len(mgr._pick_starts(now=100.0)) == 3


def test_respects_concurrency_slots(mgr, cfg):
    """空位用完就一条都不放，跟有没有间隔无关。"""
    cfg.update({"maxConcurrent": 1, "startStagger": 1.2})
    _add_all(mgr, cfg)
    mgr.active.add("pretend-running")
    assert mgr._pick_starts(now=1e6) == []


def test_oldest_first(mgr, cfg):
    """按加入时间排队，不受字典顺序影响——先粘的先生。"""
    cfg.update({"maxConcurrent": 3, "startStagger": 1.2})
    _add_all(mgr, cfg)
    oldest = min(mgr.tasks.values(), key=lambda t: t["created_at"])
    assert mgr._pick_starts(now=1e6) == [oldest["id"]]


def test_default_setting_is_on(cfg):
    """新装默认就错峰，用户不用知道有这回事。"""
    assert cfg.get("startStagger") > 0
