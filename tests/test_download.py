# -*- coding: utf-8 -*-
"""区间寻址分片 —— `seg_range` / `download_all` / `http_get` 的 Range 处理。

背景（2026-10-15 的真 bug）：有的课程把整场直播切成几百片共用**同一个文件名**，
只靠 URL 上的 `start`/`end` 区分。那个 CDN 完全不看这两个查询参数，每次请求
都返回整个文件（4.4MB / 60 秒），于是 906 片叠成了 15 小时的视频（真实 66 分钟）。
只有 HTTP `Range` 头才认 —— 下面这些测试钉住这件事。
"""
import pytest

from ocemind import core

EXT = {
    "host": "https://ttv-tos.xet.tech",
    "path": "/vod/abc",
    "param": "sign=FAKE&t=1750000000&us=FAKE",
}

RANGED = ("3877768290_2020660754_1.ts?end=300235&resolution=1920x1080"
          "&start=0&type=mpegts&sign=FAKE&t=1750000000")
WHOLE = "156174317_1257317718_1.ts?resolution=1920x1080"


# ---------------------------------------------------------------- seg_range
def test_seg_range_reads_inclusive_bounds():
    """start=0&end=300235 是 300236 字节 —— 两端都含。"""
    assert core.seg_range(RANGED) == (0, 300235)
    assert core.seg_range(RANGED)[1] - core.seg_range(RANGED)[0] + 1 == 300236


def test_seg_range_handles_nonzero_start():
    seg = "x.ts?end=749555&start=300236&type=mpegts"
    assert core.seg_range(seg) == (300236, 749555)
    assert 749555 - 300236 + 1 == 449320


def test_seg_range_none_for_whole_file_segment():
    """整文件寻址的分片没有 start/end，不能被误判成区间。"""
    assert core.seg_range(WHOLE) is None


def test_seg_range_none_when_only_one_bound_present():
    assert core.seg_range("x.ts?start=0&resolution=1920x1080") is None
    assert core.seg_range("x.ts?end=100&resolution=1920x1080") is None


def test_seg_range_none_for_absolute_url_without_bounds():
    assert core.seg_range("https://cdn.e.com/p/s.ts?sign=x") is None


def test_seg_range_rejects_bad_input():
    assert core.seg_range("x.ts?start=abc&end=100") is None
    assert core.seg_range("x.ts?start=-1&end=100") is None
    assert core.seg_range("x.ts?start=500&end=100") is None


# ---------------------------------------------------------------- download_all
def _fake_get(calls):
    """假的 http_get：按 (url, byte_range) 记账，返回该区间的字节数。"""
    def _get(url, cookie, referer, timeout=40, retries=4, byte_range=None):
        calls.append((url, byte_range))
        if byte_range is None:
            return b"\x47" + b"W" * (60 * 1024)      # 整个文件，很大
        return b"\x47" * (byte_range[1] - byte_range[0] + 1)
    return _get


def test_download_all_sends_range_for_ranged_segments(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))
    segs = ["a.ts?start=0&end=300235", "b.ts?start=300236&end=749555"]

    fail = core.download_all(segs, EXT, "ko_token=x", "https://h5.example.com",
                             str(tmp_path), workers=2)

    assert fail == []
    assert [c[1] for c in calls] == [(0, 300235), (300236, 749555)]
    assert (tmp_path / "00001.ts").stat().st_size == 300236
    assert (tmp_path / "00002.ts").stat().st_size == 449320


def test_download_all_sends_no_range_for_whole_file_segments(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))

    fail = core.download_all([WHOLE], EXT, "", "https://h", str(tmp_path), workers=1)

    assert fail == []
    assert [c[1] for c in calls] == [None]


def test_download_all_redownloads_oversized_stale_cache(tmp_path, monkeypatch):
    """**这是那个 15 小时 bug 的回归钉。**

    坏版本往临时目录里留下的正是「每片都是整个文件」的 4.4MB .ts。旧判据是
    「文件 > 1KB 就算下好了」，于是这些脏数据被当成有效缓存 —— 重跑一万次
    也只会拼出同一个 15 小时视频。
    """
    stale = tmp_path / "00001.ts"
    stale.write_bytes(b"\x47" * 4488312)        # 整个文件，远超区间长度

    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))
    core.download_all(["a.ts?start=0&end=300235"], EXT, "", "https://h",
                      str(tmp_path), workers=1)

    assert len(calls) == 1, "过大的缓存必须被丢弃重下"
    assert stale.stat().st_size == 300236


def test_download_all_keeps_valid_cache(tmp_path, monkeypatch):
    """区间长度以内的缓存是真下好的，不能每次都重下（断点续传靠这个）。"""
    (tmp_path / "00001.ts").write_bytes(b"\x47" * 300236)

    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))
    fail = core.download_all(["a.ts?start=0&end=300235"], EXT, "", "https://h",
                             str(tmp_path), workers=1)

    assert calls == [] and fail == []


def test_download_all_tolerates_short_cache(tmp_path, monkeypatch):
    """CDN 少给几个字节也算下好了 —— 否则会陷入无限重下。"""
    (tmp_path / "00001.ts").write_bytes(b"\x47" * 300000)

    calls = []
    monkeypatch.setattr(core, "http_get", _fake_get(calls))
    core.download_all(["a.ts?start=0&end=300235"], EXT, "", "https://h",
                      str(tmp_path), workers=1)

    assert calls == []


# ---------------------------------------------------------------- http_get
class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body


class _Session:
    def __init__(self, status, body):
        self.status, self.body, self.headers = status, body, None

    def get(self, url, headers=None, timeout=None):
        self.headers = headers
        return _Resp(self.status, self.body)


def _patch_session(monkeypatch, status, body):
    sess = _Session(status, body)
    monkeypatch.setattr(core, "_session", lambda: sess)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    return sess


def test_http_get_sets_range_header(monkeypatch):
    sess = _patch_session(monkeypatch, 206, b"\x47" * 300236)
    body = core.http_get("https://cdn/p/s.ts", "c", "https://h",
                         byte_range=(0, 300235))
    assert sess.headers["Range"] == "bytes=0-300235"
    assert len(body) == 300236


def test_http_get_accepts_206(monkeypatch):
    """带 Range 的正常响应就是 206，不能被当成失败。"""
    _patch_session(monkeypatch, 206, b"\x47" * 1024)
    assert len(core.http_get("https://cdn/p/s.ts", "c", "https://h",
                             byte_range=(0, 1023))) == 1024


def test_http_get_raises_when_cdn_ignores_range(monkeypatch):
    """要了区间却收到整份文件 —— 必须炸，不能默默拼出超长视频。"""
    _patch_session(monkeypatch, 200, b"\x47" * 4488312)
    with pytest.raises(RuntimeError, match="忽略了 Range"):
        core.http_get("https://cdn/p/s.ts", "c", "https://h",
                      byte_range=(0, 300235), retries=2)


def test_http_get_ok_when_200_body_within_range(monkeypatch):
    """200 但体积没超区间，是合法响应（有些边缘节点就这么回），不该误报。"""
    _patch_session(monkeypatch, 200, b"\x47" * 1000)
    assert len(core.http_get("https://cdn/p/s.ts", "c", "https://h",
                             byte_range=(0, 300235))) == 1000


def test_http_get_without_range_accepts_any_size(monkeypatch):
    sess = _patch_session(monkeypatch, 200, b"\x47" * 4488312)
    assert len(core.http_get("https://cdn/p/s.ts", "c", "https://h")) == 4488312
    assert "Range" not in sess.headers
