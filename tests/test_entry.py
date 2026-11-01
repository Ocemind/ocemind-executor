# -*- coding: utf-8 -*-
"""run.py 的四路分发。

入口分错是最烦的一类问题：命令行用户带着 `--url` 却被拉起图形界面，
或者 `--tk` 被 argparse 当成未知参数报错。这里用假模块替身，不启动任何真界面。
"""
import sys
import types

import pytest

import run


@pytest.fixture
def calls(monkeypatch):
    """把三条启动路径换成记录器。"""
    record = {}

    desktop = types.ModuleType("ocemind.desktop")
    desktop.main = lambda force_browser=False: record.setdefault(
        "desktop", force_browser)
    monkeypatch.setitem(sys.modules, "ocemind.desktop", desktop)

    gui = types.ModuleType("ocemind.gui")
    gui.main_gui = lambda: record.setdefault("tk", True)
    monkeypatch.setitem(sys.modules, "ocemind.gui", gui)

    monkeypatch.setattr(run, "_run_cli", lambda: record.setdefault("cli", True))
    return record


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["run.py", *args])


def test_no_args_launches_desktop(monkeypatch, calls):
    _argv(monkeypatch)
    run.main()
    assert calls == {"desktop": False}


def test_web_uses_browser_mode(monkeypatch, calls):
    _argv(monkeypatch, "--web")
    run.main()
    assert calls == {"desktop": True}


def test_tk_flag_launches_gui(monkeypatch, calls):
    _argv(monkeypatch, "--tk")
    run.main()
    assert calls == {"tk": True}


def test_cli_args_go_to_cli(monkeypatch, calls):
    _argv(monkeypatch, "--url", "https://example.com/course")
    run.main()
    assert calls == {"cli": True}


def test_tk_wins_over_web(monkeypatch, calls):
    _argv(monkeypatch, "--web", "--tk")
    run.main()
    assert calls == {"tk": True}


def test_help_goes_to_cli(monkeypatch, calls):
    _argv(monkeypatch, "--help")
    run.main()
    assert calls == {"cli": True}
