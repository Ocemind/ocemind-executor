# -*- coding: utf-8 -*-
"""课程引擎 —— `core.run_one` 到任务队列之间的适配层。

三件事最关键：绝不隐式弹浏览器、被打断要能和真失败区分开、
每个任务用自己的临时目录。全部用替身，不联网。
"""
import threading

import pytest

from xiaoe_dl import auth
from xiaoe_dl.downloader import course_engine
from xiaoe_dl.downloader.config import Config
from xiaoe_dl.downloader.course_engine import CourseDownloader, tmp_dir_for

URL = "https://app1.h5.xiaoeknow.com/p/course/video/l_abcdef1234567890abcd"
ALIVE = "l_abcdef1234567890abcd"


@pytest.fixture
def cfg(tmp_path):
    return Config(home=tmp_path, legacy_home=tmp_path / "none")


def _cookies(monkeypatch):
    monkeypatch.setattr(course_engine.auth, "get_cookie",
                        lambda **kw: ("C:/c/cookies.txt", ["fake"]))


def test_tmp_dir_is_per_task():
    a = tmp_dir_for(URL, "task0001")
    b = tmp_dir_for(URL, "task0002")
    assert a != b, "同一门课的两个任务不能共用一个临时目录"
    assert ALIVE[-8:] in a


def test_tmp_dir_works_without_alive_id():
    assert tmp_dir_for("https://example.com/x", "abc") .endswith("abc")


def test_missing_ids_is_a_clear_error(cfg):
    result = CourseDownloader(cfg).run("https://example.com/not-a-course", "/tmp")
    assert result["ok"] is False
    assert "app_id" in result["error"]


def test_auth_error_is_reported_not_raised(cfg, monkeypatch):
    def no_cookie(**kw):
        raise auth.AuthError("没有 cookies.txt")

    monkeypatch.setattr(course_engine.auth, "get_cookie", no_cookie)

    result = CourseDownloader(cfg).run(URL, "/tmp")

    assert result == {"ok": False, "error": "没有 cookies.txt"}


def test_never_opens_browser(cfg, monkeypatch):
    """队列里跑任务时只认手动文件，mode 必须是 manual。"""
    seen = {}

    def fake_get_cookie(**kw):
        seen.update(kw)
        return "C:/c/cookies.txt", []

    monkeypatch.setattr(course_engine.auth, "get_cookie", fake_get_cookie)
    monkeypatch.setattr(course_engine.core, "run_one",
                        lambda *a, **k: True)

    CourseDownloader(cfg).run(URL, "/tmp")

    assert seen["mode"] == "manual"
    assert seen["app_id"] == "app1"


def test_success_returns_output_path(cfg, monkeypatch, tmp_path):
    _cookies(monkeypatch)
    out = tmp_path / "课程.mp4"

    def fake_run_one(url, app_id, alive_id, cookies, args, **kw):
        kw["on_progress"]({"stage": "done", "path": str(out)})
        return True

    monkeypatch.setattr(course_engine.core, "run_one", fake_run_one)

    result = CourseDownloader(cfg).run(URL, str(tmp_path))

    assert result == {"ok": True, "path": str(out)}


def test_args_come_from_settings(cfg, monkeypatch, tmp_path):
    _cookies(monkeypatch)
    cfg.update({"courseWorkers": 32, "courseLine": "hw", "courseKeepTs": True})
    seen = {}

    def fake_run_one(url, app_id, alive_id, cookies, args, **kw):
        seen["args"] = args
        seen["tmpdir"] = kw.get("tmpdir")
        return True

    monkeypatch.setattr(course_engine.core, "run_one", fake_run_one)

    CourseDownloader(cfg, "task42").run(URL, str(tmp_path))

    args = seen["args"]
    assert args.workers == 32
    assert args.line == "hw"
    assert args.keep_ts is True
    assert args.test is None
    assert seen["tmpdir"] == tmp_dir_for(URL, "task42")


def test_stop_maps_to_cancelled(cfg, monkeypatch):
    _cookies(monkeypatch)
    engine = CourseDownloader(cfg)

    def fake_run_one(*a, **kw):
        engine.stop()                    # 模拟下载途中按了暂停
        return False

    monkeypatch.setattr(course_engine.core, "run_one", fake_run_one)

    assert engine.run(URL, "/tmp") == {"ok": False, "cancelled": True}


def test_error_event_becomes_error_message(cfg, monkeypatch):
    _cookies(monkeypatch)

    def fake_run_one(*a, **kw):
        kw["on_progress"]({"stage": "error", "error": "缺失 3 个分片"})
        return False

    monkeypatch.setattr(course_engine.core, "run_one", fake_run_one)

    result = CourseDownloader(cfg).run(URL, "/tmp")
    assert result == {"ok": False, "error": "缺失 3 个分片"}


def test_unexpected_exception_is_caught(cfg, monkeypatch):
    _cookies(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg 合并失败")

    monkeypatch.setattr(course_engine.core, "run_one", boom)

    result = CourseDownloader(cfg).run(URL, "/tmp")
    assert result["ok"] is False
    assert "ffmpeg 合并失败" in result["error"]


def test_stop_is_handed_to_core_as_event(cfg, monkeypatch):
    _cookies(monkeypatch)
    engine = CourseDownloader(cfg)
    engine.stop()
    seen = {}

    def fake_run_one(*a, **kw):
        seen["event"] = kw["stop_event"]
        return False

    monkeypatch.setattr(course_engine.core, "run_one", fake_run_one)
    engine.run(URL, "/tmp")

    assert isinstance(seen["event"], threading.Event)
    assert seen["event"].is_set()
