# -*- coding: utf-8 -*-
"""任务队列里的第三种类型：course。

不启动调度线程（`start_scheduler=False`），也不联网——这里只验证
数据层的行为：分类、重复保护、进度映射、清理。
"""
import pytest

from xiaoe_dl.downloader import manager as manager_mod
from xiaoe_dl.downloader.config import Config
from xiaoe_dl.downloader.manager import TaskManager

URL = "https://app1.h5.xiaoeknow.com/p/course/video/l_abcdef1234567890abcd"


@pytest.fixture
def cfg(tmp_path):
    return Config(home=tmp_path / "home", legacy_home=tmp_path / "none")


@pytest.fixture
def mgr(cfg):
    m = TaskManager(cfg, start_scheduler=False)
    yield m
    m.shutdown()


def test_course_url_is_detected(mgr, cfg):
    task = mgr.add(URL, "", 0, str(cfg.home), 0)
    assert task["kind"] == "course"
    assert task["course_key"].startswith("app1:l_abcdef1234567890abcd:")


def test_non_replay_link_is_refused(mgr, cfg):
    """课程页里的普通视频链接（v_ 开头）没有回放 ID，不该进队列空跑一趟。"""
    url = "https://app1.h5.xiaoeknow.com/p/course/video/v_6190abcd"
    with pytest.raises(ValueError, match="回放 ID"):
        mgr.add(url, "", 0, str(cfg.home), 0)
    assert mgr.list() == []


def test_duplicate_course_is_refused(mgr, cfg):
    mgr.add(URL, "course", 0, str(cfg.home), 0)
    with pytest.raises(ValueError, match="已经在队列里"):
        mgr.add(URL, "course", 0, str(cfg.home), 0)


def test_same_course_other_folder_is_allowed(mgr, cfg, tmp_path):
    mgr.add(URL, "course", 0, str(tmp_path / "a"), 0)
    task = mgr.add(URL, "course", 0, str(tmp_path / "b"), 0)
    assert task["kind"] == "course"


def test_done_course_can_be_added_again(mgr, cfg):
    first = mgr.add(URL, "course", 0, str(cfg.home), 0)
    with mgr.lock:
        first["status"] = "done"
    assert mgr.add(URL, "course", 0, str(cfg.home), 0)["kind"] == "course"


def test_plain_links_keep_old_classification(mgr, cfg):
    assert mgr.add("https://e.com/a.zip", "", 0, str(cfg.home), 0)["kind"] == "file"
    assert mgr.add("https://e.com/watch?v=1", "", 0, str(cfg.home), 0)["kind"] == "video"


def test_speed_sampling_does_not_clobber_course_progress(mgr):
    """课程进度是分片回调写的，字节数采样不能把它覆盖回 0。"""
    task = mgr.add(URL, "course", 0, "D:/tmp", 0)
    with mgr.lock:
        task["status"] = "downloading"
        task["progress"] = 42.0
        task["segments_done"] = 21
        task["segments_total"] = 50
        mgr.active.add(task["id"])

    mgr._sample_speeds()

    assert task["progress"] == 42.0
    assert task["segments_done"] == 21


def test_progress_events_update_task(mgr):
    task = mgr.add(URL, "course", 0, "D:/tmp", 0)
    events = []

    class FakeEngine:
        def __init__(self, cfg, task_id, on_progress=None):
            self.on_progress = on_progress

        def stop(self):
            pass

        def run(self, url, out_dir):
            for ev in events:
                self.on_progress(ev)
            return {"ok": True, "path": "D:/tmp/课程.mp4"}

    original = manager_mod.CourseDownloader
    manager_mod.CourseDownloader = FakeEngine
    try:
        events.extend([
            {"stage": "resolving"},
            {"stage": "title", "title": "第 3 讲"},
            {"stage": "output", "path": "D:/tmp/课程.mp4"},
            {"stage": "segments", "total": 100},
            {"stage": "downloading", "done": 25, "total": 100},
        ])
        mgr._run_course(task)
    finally:
        manager_mod.CourseDownloader = original

    assert task["title"] == "第 3 讲"
    assert task["progress"] == 25.0
    assert task["segments_total"] == 100
    assert task["filename"] == "课程.mp4"


def test_merging_event_sets_status(mgr):
    task = mgr.add(URL, "course", 0, "D:/tmp", 0)

    class FakeEngine:
        def __init__(self, cfg, task_id, on_progress=None):
            self.on_progress = on_progress

        def stop(self):
            pass

        def run(self, url, out_dir):
            self.on_progress({"stage": "segments", "total": 10})
            self.on_progress({"stage": "downloading", "done": 10, "total": 10})
            self.on_progress({"stage": "merging"})
            return {"ok": True, "path": ""}

    original = manager_mod.CourseDownloader
    manager_mod.CourseDownloader = FakeEngine
    try:
        mgr._run_course(task)
    finally:
        manager_mod.CourseDownloader = original

    assert task["status"] == "merging"
    assert task["progress"] == 100.0


def test_cancel_course_removes_temp_dir_but_keeps_output(mgr, cfg, tmp_path, monkeypatch):
    task = mgr.add(URL, "course", 0, str(tmp_path), 0)
    tmp = tmp_path / "scratch"
    tmp.mkdir()
    (tmp / "00001.ts").write_bytes(b"x")
    out = tmp_path / "课程.mp4"
    out.write_bytes(b"mp4")

    monkeypatch.setattr(manager_mod, "tmp_dir_for", lambda *a: str(tmp))
    mgr._remove_files(task)

    assert not tmp.exists()
    assert out.exists(), "取消任务绝不能删已合并好的视频"
