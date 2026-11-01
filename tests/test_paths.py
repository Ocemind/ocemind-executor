# -*- coding: utf-8 -*-
"""paths —— 尤其是「便携资源目录」的语义。

包化之后最容易悄悄坏掉的就是这个：`dirname(__file__)` 从项目根变成了
包目录，于是「把 cookies.txt 放在 exe 旁边」就漂移成了「放在 ocemind/ 里」。
不会报错，只是找不到 cookie 文件。
"""
import os
import sys

import pytest

from ocemind import paths


def test_project_root_is_repo_root():
    root = paths.project_root()
    assert os.path.isfile(os.path.join(root, "run.py"))
    assert os.path.isdir(os.path.join(root, "ocemind"))


def test_source_mode_resource_dir_is_project_root():
    """源码运行时，便携 cookie 该在项目根目录找，不是包目录里。"""
    assert not paths.is_frozen()
    assert paths.resource_dir() == paths.project_root()
    assert not paths.resource_dir().endswith("ocemind")


def test_frozen_mode_resource_dir_is_exe_dir(monkeypatch):
    """打包后，便携 cookie 该在 exe 旁边找。

    直接改 sys.frozen / sys.executable 来模拟，不用真去打包一个 exe。
    """
    fake_exe = r"C:\somewhere\desktop\Ocemind 内容执行器.exe"
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
    assert idx(os.path.join("ocemind", "cookies.txt")) == 0
    assert idx("cookies_session") > idx("cookies.txt")
    assert any(c.startswith(r"D:\视频") for c in cands)


def test_cookie_candidates_second_entry_is_next_to_executable_or_root():
    """第二条是「便携」位置——它跟着 resource_dir() 走。"""
    cands = paths.cookie_candidates()
    assert os.path.dirname(cands[1]) == paths.resource_dir()


def test_desktop_dir_only_returns_a_real_directory():
    """找不到就返回 None，绝不返回一个不存在的路径。

    部署脚本会直接往这个目录写快捷方式，返回假路径会导致「静默部署失败」。
    """
    d = paths.desktop_dir()
    assert d is None or os.path.isdir(d)


def test_desktop_dir_falls_back_when_user_profile_is_wrong(monkeypatch):
    """这台机器的桌面被重定向到了 D:\\桌面，展开 user profile 会指到不存在的目录。

    这里**不写死 D:\\桌面**（换台机器跑测试不该红），只断言那条不变量：
    候选里第一个不存在时，要能落到真正存在的那个。
    """
    monkeypatch.setenv("USERPROFILE", r"Z:\definitely-not-here")
    d = paths.desktop_dir()
    assert d is None or os.path.isdir(d)


def test_app_dir_follows_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\X\AppData\Roaming")
    assert paths.app_dir() == os.path.join(r"C:\Users\X\AppData\Roaming", "ocemind")


def test_profile_dir_is_under_localappdata_not_appdata(monkeypatch):
    """profile 放 APPDATA 会被漫游同步几十 MB 缓存，且路径一变 ABE 就失效。"""
    monkeypatch.setenv("APPDATA", r"C:\Users\X\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\X\AppData\Local")
    prof = paths.profile_dir()
    assert prof.startswith(r"C:\Users\X\AppData\Local")
    assert "Roaming" not in prof
    assert prof.endswith("browser_profile")


def test_local_app_dir_can_be_overridden_for_portable(monkeypatch, tmp_path):
    """便携版把浏览器 profile 也留在解压目录里，不写用户目录。"""
    monkeypatch.setenv("OCEMIND_LOCAL_HOME", str(tmp_path))
    assert paths.local_app_dir() == str(tmp_path)


def test_local_app_dir_defaults_to_localappdata(monkeypatch):
    monkeypatch.delenv("OCEMIND_LOCAL_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\X\AppData\Local")
    assert paths.local_app_dir() == os.path.join(
        r"C:\Users\X\AppData\Local", "ocemind")


def test_find_ffmpeg_returns_something_usable():
    """找不到时退回字符串 'ffmpeg'（让 ffmpeg 自己报错），而不是 None。"""
    ff = paths.find_ffmpeg()
    assert isinstance(ff, str) and ff
