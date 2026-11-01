# -*- coding: utf-8 -*-
"""parse_lookback / pick_line —— 接口响应解析与线路选择。

错误码翻译（CODE_HINT）是改版时最先要看的东西，值得钉住。
"""
import json

import pytest

from ocemind.core import CODE_HINT, parse_lookback, pick_line


# ------------------------------------------------------------ parse_lookback
def test_parse_ok_returns_data(lookback_payload):
    payload = lookback_payload(n_lines=3)
    data = parse_lookback(json.dumps(payload).encode("utf-8"))
    assert len(data) == 3
    assert data[0]["line_name"] == "线路1"


def test_parse_accepts_str_too(lookback_payload):
    """http_get 给的是 bytes，但单测里直接喂 str 也该能用。"""
    text = json.dumps(lookback_payload())
    assert parse_lookback(text) == parse_lookback(text.encode("utf-8"))


def test_expired_login_error_is_translated_to_plain_chinese():
    raw = json.dumps({"code": 11302, "msg": None, "data": None}).encode()
    with pytest.raises(RuntimeError) as e:
        parse_lookback(raw)
    assert "11302" in str(e.value)
    assert "登录态失效" in str(e.value)


def test_unknown_code_says_unknown_not_crash():
    raw = json.dumps({"code": 99999, "msg": "boom"}).encode()
    with pytest.raises(RuntimeError) as e:
        parse_lookback(raw)
    assert "未知错误" in str(e.value)
    assert "boom" in str(e.value)


def test_code_zero_but_no_data_is_reported_as_possible_revamp():
    """code=0 但 data 没了，是改版的典型信号，必须报出来而不是 KeyError。"""
    raw = json.dumps({"code": 0, "msg": "ok"}).encode()
    with pytest.raises(RuntimeError) as e:
        parse_lookback(raw)
    assert "改版" in str(e.value)


def test_all_documented_codes_have_hints():
    for code in (11302, 11303, 11304):
        assert code in CODE_HINT
        assert CODE_HINT[code].strip()


# ------------------------------------------------------------ pick_line
def test_pick_line_defaults_to_first(lookback_data):
    li, cloud, lname, seg, ext = pick_line(lookback_data(n_lines=3))
    assert li == 1
    assert lname == "线路1"
    assert cloud == "tx"
    assert ext["host"] == "https://cdn1.example.com"


def test_pick_line_by_number(lookback_data):
    li, _, lname, _, ext = pick_line(lookback_data(n_lines=3), prefer="2")
    assert li == 2
    assert ext["host"] == "https://cdn2.example.com"


def test_pick_line_by_number_as_int(lookback_data):
    li, _, _, _, _ = pick_line(lookback_data(n_lines=3), prefer=3)
    assert li == 3


def test_pick_line_by_cloud_name():
    data = [
        {"line_name": "线路1", "line_sharpness": [
            {"cloud": "hw", "url": "https://a/p.m3u8",
             "ext": {"host": "https://a", "path": "/p", "param": "x=1"}}]},
        {"line_name": "线路2", "line_sharpness": [
            {"cloud": "tx", "url": "https://b/p.m3u8",
             "ext": {"host": "https://b", "path": "/p", "param": "x=1"}}]},
    ]
    _, cloud, _, _, ext = pick_line(data, prefer="tx")
    assert cloud == "tx"
    assert ext["host"] == "https://b"


def test_pick_line_unmatched_prefer_falls_back_to_first(lookback_data):
    """线路号写错了不该直接失败——退回第一条比报错更有用。"""
    li, _, _, _, _ = pick_line(lookback_data(n_lines=2), prefer="99")
    assert li == 1


def test_pick_line_skips_entries_missing_host_or_url():
    data = [{"line_name": "坏线路", "line_sharpness": [
        {"cloud": "tx", "url": "https://a/p.m3u8", "ext": {}},          # 缺 host
        {"cloud": "tx", "url": None,
         "ext": {"host": "https://b", "path": "/p"}},                    # 缺 url
    ]}]
    with pytest.raises(RuntimeError, match="没有可用线路"):
        pick_line(data)


def test_pick_line_empty_data_raises():
    with pytest.raises(RuntimeError, match="没有可用线路"):
        pick_line([])
