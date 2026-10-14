# -*- coding: utf-8 -*-
"""safe_name —— 把课程标题变成合法的 Windows 文件名。"""
import os

import pytest

from xiaoe_dl.core import safe_name


@pytest.mark.parametrize("bad", list('<>:"/\\|?*'))
def test_each_illegal_char_replaced(bad):
    assert safe_name(f"a{bad}b") == "a_b"


def test_path_separators_cannot_survive():
    """这是这条函数真正的存在理由：标题里的 / 会写到别的目录去。

    注意 `..` 本身**会保留**——`.` 不在非法字符表里。安全的是「结果一定是
    单个文件名成分」这件事，不是「不含 ..」。所以这里断言的是前者。
    """
    out = safe_name("../../etc/passwd")
    assert "/" not in out and "\\" not in out
    assert os.path.basename(out) == out
    assert os.path.dirname(out) == ""


def test_control_chars_replaced():
    assert safe_name("a\nb\rc\td") == "a_b_c_d"


def test_strips_leading_trailing_space_and_dot():
    # Windows 不允许文件名以 . 或空格结尾
    assert safe_name("  课程名.  ") == "课程名"
    assert safe_name("..课程..") == "课程"


@pytest.mark.parametrize("empty", ["", "   ", "...", " . "])
def test_empty_becomes_untitled(empty):
    assert safe_name(empty) == "untitled"


def test_whitespace_only_yields_underscores_not_untitled():
    """记录一个已知的小怪异点：制表符/换行先被替换成 `_`，再裁剪首尾，
    于是结果是非空的 "__" 而不是 "untitled"。

    无害（都是合法文件名），但这个顺序差异值得留个记录，
    免得将来有人看到 `__` 以为是 bug 又改一遍。
    """
    assert safe_name("\n\t") == "__"
    assert safe_name("\t") == "_"


def test_truncates_to_120_chars():
    assert len(safe_name("啊" * 500)) == 120


def test_chinese_and_normal_punctuation_survive():
    assert safe_name("35、区间震荡量价行为案例分析（上）") == "35、区间震荡量价行为案例分析（上）"
