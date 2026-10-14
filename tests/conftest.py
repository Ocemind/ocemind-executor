# -*- coding: utf-8 -*-
"""共用 fixture。常量与构造器在 helpers.py。"""
import pytest

from helpers import NOW, default_cookie_lines, lookback_payload as _payload


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def make_cookiefile(tmp_path):
    """写一份合成 cookie 文件并返回路径。传 lines 可覆盖内容。"""
    def _make(lines=None, name="cookies.txt"):
        p = tmp_path / name
        p.write_text("\n".join(lines or default_cookie_lines()) + "\n",
                     encoding="utf-8")
        return str(p)
    return _make


@pytest.fixture
def lookback_payload():
    """get_lookback_list 的完整合成响应构造器（给 parse_lookback 用）。"""
    return _payload


@pytest.fixture
def lookback_data():
    """只要 data 那一段线路数组（给 pick_line 用）。"""
    def _make(n_lines=2):
        return _payload(n_lines=n_lines)["data"]
    return _make
