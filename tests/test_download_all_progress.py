# -*- coding: utf-8 -*-
"""分片进度回调、停止语义、原子合并，以及课程链接判定。

网页界面靠这些事件画进度条。这里钉住三件容易悄悄坏掉的事：
进度必须能涨到总数、停止后不能再发请求、合并中途失败不能留下一个
「看起来已完成」的 mp4。
"""
import os
import threading
import types

import pytest

from xiaoe_dl import core

EXT = {"host": "https://cdn.example.com", "path": "/v", "param": "sign=x"}


def _fake_get(calls, size=1024):
    def _get(url, cookie, referer, timeout=40, retries=4, byte_range=None):
        calls.append(url)
        if byte_range:
            return b"\x47" * (byte_range[1] - byte_range[0] + 1)
        return b"\x47" * size
    return _get


# ---------------------------------------------------------------- 进度
def test_progress_reaches_total(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "http_get", _fake_get([]))
    events = []
    segs = [f"{i}.ts" for i in range(1, 6)]

    fail = core.download_all(segs, EXT, "", "https://h", str(tmp_path), workers=2,
                             on_progress=events.append)

    assert fail == []
    assert events[-1] == {"stage": "downloading", "done": 5, "total": 5}
    done = [e["done"] for e in events]
    assert done == sorted(done), "进度不能倒退"
    assert all(e["total"] == 5 for e in events)


def test_progress_counts_cached_segments(tmp_path, monkeypatch):
    """续传时旧分片直接复用，进度条要把它算进去，否则永远到不了 100%。"""
    (tmp_path / "00001.ts").write_bytes(b"\x47" * 4096)
    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))
    events = []

    core.download_all(["a.ts", "b.ts"], EXT, "", "https://h", str(tmp_path),
                      workers=1, on_progress=events.append)

    assert len(calls) == 1
    assert events[-1]["done"] == 2


def test_progress_callback_exception_does_not_break_download(tmp_path, monkeypatch):
    """UI 回调炸了不能把下载带崩。"""
    monkeypatch.setattr(core, "http_get", _fake_get([]))

    def boom(_event):
        raise RuntimeError("前端崩了")

    fail = core.download_all(["a.ts"], EXT, "", "https://h", str(tmp_path),
                             workers=1, on_progress=boom)
    assert fail == []
    assert (tmp_path / "00001.ts").exists()


def test_stop_event_prevents_new_requests(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))
    stop = threading.Event()
    stop.set()

    core.download_all([f"{i}.ts" for i in range(10)], EXT, "", "https://h",
                      str(tmp_path), workers=2, stop_event=stop)

    assert calls == []


# ---------------------------------------------------------------- 合并
class _Proc:
    def __init__(self, code=0, stderr=""):
        self.returncode, self.stderr = code, stderr


def test_merge_is_atomic(tmp_path, monkeypatch):
    """写 .tmp.mp4 再改名：中途崩掉不会留下半个「已完成」的文件。"""
    out = tmp_path / "course.mp4"

    def fake_run(cmd, **kw):
        target = cmd[-1]
        assert target.endswith(".tmp.mp4")
        with open(target, "wb") as f:
            f.write(b"data")
        return _Proc()

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    core.merge(str(tmp_path), 0, str(out), ffmpeg="ffmpeg")

    assert out.read_bytes() == b"data"
    assert list(tmp_path.glob("*.tmp.mp4")) == []


def test_merge_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    out = tmp_path / "course.mp4"

    def fake_run(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(b"half")
        return _Proc(code=1, stderr="boom")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="合并失败"):
        core.merge(str(tmp_path), 0, str(out), ffmpeg="ffmpeg")

    assert not out.exists()
    assert list(tmp_path.glob("*.tmp.mp4")) == []


# ---------------------------------------------------------------- 链接判定
@pytest.mark.parametrize("url", [
    "https://abc.h5.xiaoeknow.com/p/course/video/v_123",
    "https://xiaoeknow.com/course/123",
    "https://APP.h5.xiaoeknow.com/?app_id=app1&alive_id=l_abc",
])
def test_is_course_url_true(url):
    assert core.is_course_url(url)


@pytest.mark.parametrize("url", [
    "https://cdn.example.com/v.mp4?ref=xiaoeknow.com",
    "https://notxiaoeknow.com/a",
    "https://xiaoeknow.com.evil.com/a",
    "",
    "not a url",
])
def test_is_course_url_false(url):
    assert not core.is_course_url(url)


# ---------------------------------------------------------------- run_one 事件
def _args(tmp_path, **kw):
    base = dict(line=None, out="", dir=str(tmp_path), force=False, test=None,
                workers=1, keep_ts=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _fake_cookies():
    return []


def test_run_one_emits_cancelled_without_touching_network(tmp_path, monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(core, "get_lookback",
                        lambda *a, **k: pytest.fail("不应该请求接口"))
    events = []

    ok = core.run_one("https://a.h5.xiaoeknow.com/x", "app1", "l_abc",
                      _fake_cookies(), _args(tmp_path),
                      on_progress=events.append, stop_event=stop)

    assert ok is False
    assert [e["stage"] for e in events] == ["resolving", "cancelled"]


def test_run_one_emits_error_on_api_failure(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("code=11302")

    monkeypatch.setattr(core, "get_lookback", boom)
    events = []

    ok = core.run_one("https://a.h5.xiaoeknow.com/x", "app1", "l_abc",
                      _fake_cookies(), _args(tmp_path), on_progress=events.append)

    assert ok is False
    assert events[-1] == {"stage": "error", "error": "code=11302"}


def test_run_one_existing_output_emits_done(tmp_path, monkeypatch):
    """「已存在跳过」也算成功，必须发 done(path)，否则适配器拿不到文件路径。"""
    out = tmp_path / "already.mp4"
    out.write_bytes(b"x")
    monkeypatch.setattr(core, "get_lookback", lambda *a, **k: ("https://h", [{}]))
    monkeypatch.setattr(core, "pick_line",
                        lambda data, prefer=None: (1, "tx", "线路1", {}, EXT))
    events = []

    ok = core.run_one("https://a.h5.xiaoeknow.com/x", "app1", "l_abc",
                      _fake_cookies(), _args(tmp_path, out=str(out)),
                      on_progress=events.append)

    assert ok is True
    assert {"stage": "done", "path": str(out)} in events


def test_run_one_uses_given_tmpdir(tmp_path, monkeypatch):
    """每个任务一个临时目录，两条链路才不至于互相覆盖分片。"""
    out = tmp_path / "o.mp4"
    mine = tmp_path / "mine"
    monkeypatch.setattr(core, "get_lookback", lambda *a, **k: ("https://h", [{}]))
    monkeypatch.setattr(core, "pick_line",
                        lambda data, prefer=None: (1, "tx", "线路1", {}, EXT))
    monkeypatch.setattr(core, "grab_playlist",
                        lambda *a, **k: "#EXTM3U\n00001.ts\n")
    monkeypatch.setattr(core, "download_all",
                        lambda segs, *a, **kw: [])
    merged = {}

    def fake_merge(tmpdir, n, out_path, ffmpeg=None):
        merged["tmpdir"] = tmpdir
        merged["ffmpeg"] = ffmpeg
        open(out_path, "wb").close()
        return out_path

    monkeypatch.setattr(core, "merge", fake_merge)

    ok = core.run_one("https://a.h5.xiaoeknow.com/x", "app1", "l_abc",
                      _fake_cookies(), _args(tmp_path, out=str(out)),
                      tmpdir=str(mine), ffmpeg="D:/ff/ffmpeg.exe")

    assert ok is True
    assert merged == {"tmpdir": str(mine), "ffmpeg": "D:/ff/ffmpeg.exe"}
    assert os.path.exists(out)


def test_run_one_stops_before_merge(tmp_path, monkeypatch):
    """暂停落在合并前：不启动 ffmpeg，任务交给调用方判成「已暂停」。"""
    stop = threading.Event()
    monkeypatch.setattr(core, "get_lookback", lambda *a, **k: ("https://h", [{}]))
    monkeypatch.setattr(core, "pick_line",
                        lambda data, prefer=None: (1, "tx", "线路1", {}, EXT))
    monkeypatch.setattr(core, "grab_playlist", lambda *a, **k: "#EXTM3U\n00001.ts\n")

    def fake_download(*a, **kw):
        stop.set()          # 下载过程中用户按了暂停
        return []

    monkeypatch.setattr(core, "download_all", fake_download)
    monkeypatch.setattr(core, "merge", lambda *a, **kw: pytest.fail("不该合并"))
    events = []

    ok = core.run_one("https://a.h5.xiaoeknow.com/x", "app1", "l_abc",
                      _fake_cookies(), _args(tmp_path, out=str(tmp_path / "o.mp4")),
                      on_progress=events.append, stop_event=stop)

    assert ok is False
    assert events[-1]["stage"] == "cancelled"
