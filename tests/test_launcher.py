# -*- coding: utf-8 -*-
"""桌面启动器的清理逻辑。

真建快捷方式要走 PowerShell，测试里不碰；这里只钉住「旧入口会被清理、
别的文件不动」，那是迁移时最容易误删东西的地方。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))

import make_launcher  # noqa: E402


def test_remove_old_launchers_only_touches_known_names(tmp_path):
    old_a = tmp_path / "课程下载器.lnk"
    old_b = tmp_path / "TDM Lite.lnk"
    keep = tmp_path / "别的快捷方式.lnk"
    current = tmp_path / f"{make_launcher.LAUNCHER_NAME}.lnk"
    for p in (old_a, old_b, keep, current):
        p.write_text("x", encoding="utf-8")

    make_launcher.remove_old_launchers(str(tmp_path), out=lambda _m: None)

    assert not old_a.exists()
    assert not old_b.exists()
    assert keep.exists()
    assert current.exists(), "当前入口不能被当成旧快捷方式删掉"


def test_remove_old_launchers_is_quiet_when_nothing_to_do(tmp_path):
    lines = []
    make_launcher.remove_old_launchers(str(tmp_path), out=lines.append)
    assert lines == []


def test_new_launcher_name_is_ocemind():
    assert make_launcher.LAUNCHER_NAME == "Ocemind 内容执行器"
    assert make_launcher.ICON.endswith("app.ico")
    # 改名前的入口要能被清理规则命中，否则桌面上会并存多个同功能图标
    assert "课程下载器.lnk".endswith(make_launcher.OLD_LAUNCHER_SUFFIXES)
