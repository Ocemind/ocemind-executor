# -*- coding: utf-8 -*-
"""抖音分享链接的识别与流选择。全部离线，不启动浏览器。"""
from __future__ import annotations

from pathlib import Path

import pytest

from ocemind import douyin
from ocemind.links import first_url, looks_like_url

SHARE_TEXT = ("7.61 复制打开抖音，看看【高百烈开源版的作品】本体论到底是什么？   "
              "https://v.douyin.com/vet3Ygfcgos/ lCU:/ :1pm 01/23 Y@m.Dh")


# ---------------------------------------------------------------- 分享文案

def test_first_url_finds_link_in_share_text():
    assert first_url(SHARE_TEXT) == "https://v.douyin.com/vet3Ygfcgos/"


def test_first_url_strips_trailing_punctuation():
    assert first_url("看这个 https://v.douyin.com/abc/，好看") == \
        "https://v.douyin.com/abc/"


def test_first_url_adds_scheme_to_bare_domain():
    assert first_url("v.douyin.com/abc/ 复制") == "https://v.douyin.com/abc/"


def test_first_url_none_when_absent():
    assert first_url("没有链接的一段话") == ""
    assert looks_like_url("没有链接的一段话") is False


def test_looks_like_url_in_text():
    assert looks_like_url(SHARE_TEXT) is True


# ---------------------------------------------------------------- 识别

@pytest.mark.parametrize("url", [
    "https://v.douyin.com/vet3Ygfcgos/",
    "https://www.douyin.com/video/7677529739451485474",
    "https://www.iesdouyin.com/share/video/7677529739451485474/",
    "v.douyin.com/abc/",
])
def test_is_douyin_url(url):
    assert douyin.is_douyin_url(url) is True


@pytest.mark.parametrize("url", [
    "https://notdouyin.com/video/123",
    "https://douyin.com.evil.example/video/123",
    "https://www.bilibili.com/video/BV1xx",
    "",
])
def test_is_not_douyin_url(url):
    assert douyin.is_douyin_url(url) is False


def test_parse_aweme_id_variants():
    assert douyin.parse_aweme_id(
        "https://www.douyin.com/video/7677529739451485474") == "7677529739451485474"
    assert douyin.parse_aweme_id(
        "https://www.iesdouyin.com/share/video/7677529739451485474/?x=1") == \
        "7677529739451485474"
    assert douyin.parse_aweme_id(
        "https://www.douyin.com/note/7677529739451485474") == "7677529739451485474"
    assert douyin.parse_aweme_id("https://v.douyin.com/abc/") == ""


# ---------------------------------------------------------------- 流选择

class _Req:
    def __init__(self, url: str) -> None:
        self.url = url


def test_collector_classifies_and_prefers_avc_high_bitrate():
    collector = douyin._Collector()
    base = ("https://v96-web.douyinvod.com/x/6ae6392b/video/tos/cn/tos-cn-vd-0026/"
            "obj/")
    collector.on_request(_Req(base + "hevc-obj/media-video-hevc/?br=3000&mime_type=video_mp4"))
    collector.on_request(_Req(base + "avc-hi/media-video-avc1/?br=1822&mime_type=video_mp4"))
    collector.on_request(_Req(base + "avc-lo/media-video-avc1/?br=900&mime_type=video_mp4"))
    collector.on_request(_Req(base + "aud/media-audio-und-mp4a/?br=189&mime_type=video_mp4"))
    # 同一路径换签名参数，应覆盖而不是重复
    collector.on_request(_Req(base + "aud/media-audio-und-mp4a/?br=189&l=2"))

    assert "br=1822" in collector.pick("video")       # avc1 优先
    assert "media-audio" in collector.pick("audio")
    assert "br=189" in collector.pick("audio")
    assert collector.has("video") and collector.has("audio")


def test_collector_accepts_merged_stream_without_media_marker():
    """成片流：路径里没有 media-video / media-audio，一条 mp4 自带音轨。"""
    collector = douyin._Collector()
    merged = ("https://v26-webf.douyinvod.com/x/6ae7a3cd/video/tos/cn/"
              "tos-cn-ve-15/oUBZE951/?a=6383&br=965&mime_type=video_mp4")
    collector.on_request(_Req(merged))
    assert collector.classify(merged) == "merged"
    assert collector.pick("merged") == merged
    assert collector.has("merged") and not collector.has("video")


def test_choose_streams_prefers_split_over_merged():
    collector = douyin._Collector()
    base = "https://v96-web.douyinvod.com/x/video/tos/cn/tos-cn-vd-0026/obj/"
    collector.on_request(_Req(base + "avc/media-video-avc1/?br=1822"))
    collector.on_request(_Req(base + "aud/media-audio-und-mp4a/?br=189"))
    collector.on_request(_Req(base + "whole/?br=965&mime_type=video_mp4"))
    video, audio, merged = douyin._choose_streams(collector)
    assert "media-video" in video and "media-audio" in audio and merged is False


def test_choose_streams_falls_back_to_merged():
    collector = douyin._Collector()
    whole = "https://v3-dy-o.zjcdn.com/x/video/tos/cn/tos-cn-ve-15/abc/?br=965"
    collector.on_request(_Req(whole))
    video, audio, merged = douyin._choose_streams(collector)
    assert video == whole and audio == "" and merged is True


def test_collector_ignores_unrelated_requests():
    collector = douyin._Collector()
    collector.on_request(_Req("https://www.douyin.com/aweme/v1/web/hot/search/list/"))
    collector.on_request(_Req("https://example.com/media-video-avc1/"))
    # 播放器的占位短片不是作品本身
    collector.on_request(_Req(
        "https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/uuu_265.mp4"))
    assert not collector.has("video") and not collector.has("audio")
    assert not collector.has("merged")


# ---------------------------------------------------------------- 杂项

def test_clean_title_strips_douyin_suffix():
    assert douyin._clean_title("本体论到底是什么？ - 抖音", "767") == \
        "本体论到底是什么？"
    assert douyin._clean_title("", "767") == "douyin_767"


def test_unique_path_avoids_overwrite(tmp_path: Path):
    dest = tmp_path / "片名 [767].mp4"
    dest.write_bytes(b"x")
    other = douyin._unique_path(dest)
    assert other.name == "片名 [767] (1).mp4"
