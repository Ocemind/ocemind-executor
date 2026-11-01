# -*- coding: utf-8 -*-
"""B 站接口解析的纯逻辑：识别、挑流、画质档位。全部离线。"""
from __future__ import annotations

import pytest

from ocemind import bilibili


@pytest.mark.parametrize("url", [
    "https://www.bilibili.com/video/BV1j3x16XEuR",
    "https://m.bilibili.com/video/BV1j3x16XEuR",
    "https://b23.tv/abc123",
    "bilibili.com/video/av117535370384828",
])
def test_is_bilibili_url(url):
    assert bilibili.is_bilibili_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.douyin.com/video/7697957306089582682",
    "https://bilibili.com.evil.example/video/BV1j3x16XEuR",
    "https://www.bilibili.tv/video/BV1j3x16XEuR",
    "",
])
def test_is_not_bilibili_url(url):
    assert bilibili.is_bilibili_url(url) is False


def test_parse_video_id_variants():
    assert bilibili.parse_video_id(
        "https://www.bilibili.com/video/BV1j3x16XEuR?p=2") == \
        ("bvid", "BV1j3x16XEuR")
    assert bilibili.parse_video_id(
        "https://www.bilibili.com/video/av117535370384828") == \
        ("aid", "117535370384828")
    assert bilibili.parse_video_id("https://b23.tv/abc123") == ("", "")


def test_page_index_reads_p_param():
    assert bilibili._page_index("https://www.bilibili.com/video/BV1x?a=1&p=3") == 3
    assert bilibili._page_index("https://www.bilibili.com/video/BV1x") == 1
    assert bilibili._page_index("https://www.bilibili.com/video/BV1x?p=x") == 1


def test_qn_for_height_picks_nearest_ceiling():
    assert bilibili._qn_for(0) == 127
    assert bilibili._qn_for(1080) == 80
    assert bilibili._qn_for(900) == 80
    assert bilibili._qn_for(720) == 64
    assert bilibili._qn_for(200) == bilibili._QN_BY_HEIGHT[240]


def test_pick_dash_takes_tallest_then_avc():
    data = {"dash": {
        "video": [
            {"height": 480, "codecid": 7, "bandwidth": 900, "baseUrl": "v480"},
            {"height": 720, "codecid": 13, "bandwidth": 3000, "baseUrl": "v720-av1"},
            {"height": 720, "codecid": 7, "bandwidth": 2000, "baseUrl": "v720-avc"},
        ],
        "audio": [
            {"bandwidth": 100, "baseUrl": "a100"},
            {"bandwidth": 300, "baseUrl": "a300"},
        ],
    }}
    picked = bilibili._pick_dash(data)
    assert picked["video_url"] == "v720-avc"      # 高度优先，同高 avc 优先
    assert picked["audio_url"] == "a300"          # 音轨取码率最高的
    assert picked["merged"] is False


def test_pick_dash_without_audio_marks_merged():
    data = {"dash": {"video": [{"height": 480, "baseUrl": "v"}], "audio": []}}
    picked = bilibili._pick_dash(data)
    assert picked["video_url"] == "v" and picked["merged"] is True


def test_pick_single_needs_exactly_one_segment():
    one = {"format": "mp4720", "durl": [{"url": "https://upos/x.mp4"}]}
    assert bilibili._pick_single(one) == {
        "video_url": "https://upos/x.mp4", "audio_url": "",
        "merged": True, "height": 720}
    # 多段 flv 要按顺序拼，交给 DASH 那条路更稳
    multi = {"format": "flv720", "durl": [{"url": "a"}, {"url": "b"}]}
    assert bilibili._pick_single(multi) is None
