# -*- coding: utf-8 -*-
"""parse_segments / abs_url —— m3u8 解析与分片地址拼接。

这是最容易悄悄坏掉的一环：拼错了不会报错，只会 404 或者下到错误的字节。
"""
from download import abs_url, parse_segments

EXT = {
    "host": "https://ttv-tos.xet.tech",
    "path": "/vod/abc",
    "param": "sign=FAKE&t=1750000000&us=FAKE",
}


def test_parse_skips_comments_and_blanks():
    txt = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "\n"
        "seg_1.ts?resolution=1920x1080\n"
        "\n"
        "#EXT-X-ENDLIST\n"
        "seg_2.ts?resolution=1920x1080\n"
    )
    assert parse_segments(txt) == [
        "seg_1.ts?resolution=1920x1080",
        "seg_2.ts?resolution=1920x1080",
    ]


def test_parse_handles_crlf():
    """Windows 上抓下来的 m3u8 可能是 CRLF，\\r 不能混进分片名。"""
    txt = "#EXTM3U\r\nseg_1.ts\r\nseg_2.ts\r\n"
    segs = parse_segments(txt)
    assert segs == ["seg_1.ts", "seg_2.ts"]
    assert all("\r" not in s for s in segs)


def test_parse_empty_playlist():
    assert parse_segments("#EXTM3U\n") == []


def test_abs_url_uses_question_mark_when_segment_has_no_query():
    got = abs_url("seg_1.ts", EXT)
    assert got == "https://ttv-tos.xet.tech/vod/abc/seg_1.ts?sign=FAKE&t=1750000000&us=FAKE"


def test_abs_url_uses_ampersand_when_segment_already_has_query():
    """分片名自带 ?resolution=...，签名要用 & 接上去，不能再来一个 ?。"""
    got = abs_url("seg_1.ts?resolution=1920x1080", EXT)
    assert got.count("?") == 1
    assert got.endswith("&sign=FAKE&t=1750000000&us=FAKE")
    assert "resolution=1920x1080&sign=" in got


def test_abs_url_normalizes_slashes():
    """host 结尾多斜杠、path 两端带斜杠，都不该拼出 // 或缺失的 /。"""
    ext = {"host": "https://cdn.example.com/", "path": "/vod/abc/", "param": "p=1"}
    got = abs_url("s.ts", ext)
    assert got == "https://cdn.example.com/vod/abc/s.ts?p=1"
    assert "//vod" not in got.replace("https://", "")


def test_abs_url_passes_through_absolute_segments():
    """有些 m3u8 里直接给绝对地址，那就不能再去拼 ext 了。"""
    seg = "https://other.cdn.com/path/s.ts?sign=x"
    assert abs_url(seg, EXT) == seg


def test_abs_url_without_param_yields_no_dangling_separator():
    ext = {"host": "https://cdn.example.com", "path": "/p"}
    assert abs_url("s.ts", ext) == "https://cdn.example.com/p/s.ts?"
