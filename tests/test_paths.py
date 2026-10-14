# -*- coding: utf-8 -*-
"""paths —— 尤其是「便携资源目录」的语义。

包化之后最容易悄悄坏掉的就是这个：`dirname(__file__)` 从项目根变成了
包目录，于是「把 cookies.txt 放在 exe 旁边」就漂移成了「放在 xiaoe_dl/ 里」。
不会报错，只是找不到 cookie 文件。
"""
import os
import sys

import pytest

from xiaoe_dl import paths


def test_project_root_is_repo_root():
    root = paths.project_root()
    assert os.path.isfile(os.path.join(root, "run.py"))
    assert os.path.isdir(os.path.join(root, "xiaoe_dl"))


def test_source_mode_resource_dir_is_project_root():
    """源码运行时，便携 cookie 该在项目根目录找，不是包目录里。"""
    assert not paths.is_frozen()
    assert paths.resource_dir() == paths.project_root()
    assert not paths.resource_dir().endswith("xiaoe_dl")


def test_frozen_mode_resource_dir_is_exe_dir(monkeypatch):
    """打包后，便携 cookie 该在 exe 旁边找。

    直接改 sys.frozen / sys.executable 来模拟，不用真去打包一个 exe。
    """
    fake_exe = r"C:\somewhere\desktop\小鹅通下载器.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", fake_exe)
    try:
        assert paths.is_frozen()
        assert paths.resource_dir() == r"C:\somewhere\desktop"
    finally:
        monkeypatch.undo()


def test_cookie_candidates_order():
    """顺序变了会静默换掉使用的 cookie 文件，值得钉住。"""
    cands = paths.cookie_candidates()
    assert len(cands) == 5

    def idx(fragment):
        return next(i for i, c in enumerate(cands) if fragment in c)

    # 规范位置第一，便携位置第二，老位置垫底
    assert idx(os.path.join("xiaoe-dl", "cookies.txt")) == 0
    assert idx("cookies_session") > idx("cookies.txt")
    assert any(c.startswith(r"D:\视频") for c in cands)


def test_cookie_candidates_second_entry_is_next_to_executable_or_root():
    """第二条是「便携」位置——它跟着 resource_dir() 走。"""
    cands = paths.cookie_candidates()
    assert os.path.dirname(cands[1]) == paths.resource_dir()


def test_app_dir_follows_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\X\AppData\Roaming")
    assert paths.app_dir() == os.path.join(r"C:\Users\X\AppData\Roaming", "xiaoe-dl")


def test_profile_dir_is_under_localappdata_not_appdata(monkeypatch):
    """profile 放 APPDATA 会被漫游同步几十 MB 缓存，且路径一变 ABE 就失效。"""
    monkeypatch.setenv("APPDATA", r"C:\Users\X\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\X\AppData\Local")
    prof = paths.profile_dir()
    assert prof.startswith(r"C:\Users\X\AppData\Local")
    assert "Roaming" not in prof
    assert prof.endswith("browser_profile")


def test_find_ffmpeg_returns_something_usable():
    """找不到时退回字符串 'ffmpeg'（让 ffmpeg 自己报错），而不是 None。"""
    ff = paths.find_ffmpeg()
    assert isinstance(ff, str) and ff
