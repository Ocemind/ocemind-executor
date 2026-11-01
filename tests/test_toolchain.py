# -*- coding: utf-8 -*-
"""外部工具定位：本机优先，包里自带的兜底。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from ocemind import paths, toolchain


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    """把项目根换成一个临时目录，vendor/tools 由测试自己造。"""
    monkeypatch.setattr(paths, "project_root", lambda: str(tmp_path))
    monkeypatch.delenv("OCEMIND_HOME", raising=False)
    return tmp_path / "vendor" / "tools"


def _exe(base: Path, name: str) -> Path:
    p = base / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return p


def test_bundled_dir_uses_vendor_in_source_mode(vendor):
    assert toolchain.bundled_dir() == vendor


def test_bundled_dir_prefers_tools_next_to_program(vendor, tmp_path, monkeypatch):
    """安装版 / 便携版把 ffmpeg 放在程序目录的 tools/ 下，不在 vendor 里。"""
    (tmp_path / "tools" / "ffmpeg" / "bin").mkdir(parents=True)
    assert toolchain.bundled_dir() == tmp_path / "tools"


def test_bundled_ffmpeg_found_anywhere_under_tools(vendor):
    exe = _exe(vendor / "ffmpeg" / "ffmpeg-master-win64-shared" / "bin",
               toolchain.exe_name())
    assert toolchain.bundled_path("ffmpeg") == exe


def test_no_bundled_ffmpeg_when_missing(vendor):
    assert toolchain.bundled_path("ffmpeg") is None


def test_resolve_prefers_settings_path(vendor, tmp_path):
    picked = _exe(tmp_path / "elsewhere", "ffmpeg.exe")
    assert toolchain.resolve("ffmpeg", str(picked)) == {
        "path": str(picked), "source": "settings"}


def test_resolve_prefers_system_over_bundled(vendor, monkeypatch, tmp_path):
    """本机装了就用本机的——这正是「装了就不要重复安装」的意思。"""
    _exe(vendor / "ffmpeg" / "bin", toolchain.exe_name())
    mine = _exe(tmp_path / "mine", "ffmpeg.exe")
    monkeypatch.setattr(toolchain, "system_path", lambda name: mine)
    assert toolchain.resolve("ffmpeg") == {"path": str(mine), "source": "system"}


def test_resolve_falls_back_to_bundled(vendor, monkeypatch):
    exe = _exe(vendor / "ffmpeg" / "bin", toolchain.exe_name())
    monkeypatch.setattr(toolchain, "system_path", lambda name: None)
    assert toolchain.resolve("ffmpeg") == {"path": str(exe), "source": "bundled"}


def test_resolve_empty_when_nothing_found(vendor, monkeypatch):
    monkeypatch.setattr(toolchain, "system_path", lambda name: None)
    monkeypatch.setattr(toolchain, "has_bundled_ytdlp", lambda: False)
    assert toolchain.resolve("ytdlp") == {"path": "", "source": ""}
    assert toolchain.resolve("ffmpeg") == {"path": "", "source": ""}


def test_resolve_bundled_ytdlp_has_no_path(vendor, monkeypatch):
    monkeypatch.setattr(toolchain, "system_path", lambda name: None)
    monkeypatch.setattr(toolchain, "has_bundled_ytdlp", lambda: True)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert toolchain.resolve("ytdlp") == {"path": "", "source": "bundled"}


def test_bundled_ytdlp_wins_over_path_shim(vendor, monkeypatch, tmp_path):
    """PATH 上那个 yt-dlp.exe 是 pip 生成的未签名壳，会被智能应用控制拦掉。"""
    shim = _exe(tmp_path / "Scripts", "yt-dlp.exe")
    monkeypatch.setattr(toolchain, "system_path", lambda name: shim)
    monkeypatch.setattr(toolchain, "has_bundled_ytdlp", lambda: True)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert toolchain.ytdlp_command() == [sys.executable, "--run-ytdlp"]


def test_ytdlp_command_uses_system_binary(vendor, monkeypatch, tmp_path):
    """包里那份不可用时，才退回本机装的。"""
    exe = _exe(tmp_path / "bin", "yt-dlp.exe")
    monkeypatch.setattr(toolchain, "system_path", lambda name: exe)
    monkeypatch.setattr(toolchain, "has_bundled_ytdlp", lambda: False)
    assert toolchain.ytdlp_command() == [str(exe)]


def test_ytdlp_command_dispatches_through_sidecar_when_frozen(vendor, monkeypatch):
    """冻结后没有独立 python，自带的 yt-dlp 只能让 sidecar 自己跑。"""
    monkeypatch.setattr(toolchain, "system_path", lambda name: None)
    monkeypatch.setattr(toolchain, "has_bundled_ytdlp", lambda: True)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert toolchain.ytdlp_command() == [sys.executable, "--run-ytdlp"]


def test_ytdlp_command_uses_module_in_source_mode(vendor, monkeypatch):
    monkeypatch.setattr(toolchain, "system_path", lambda name: None)
    monkeypatch.setattr(toolchain, "has_bundled_ytdlp", lambda: True)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert toolchain.ytdlp_command() == [sys.executable, "-m", "yt_dlp"]


def test_ffmpeg_exe_empty_when_missing(vendor, monkeypatch):
    monkeypatch.setattr(toolchain, "system_path", lambda name: None)
    assert toolchain.ffmpeg_exe() == ""


def test_paths_find_ffmpeg_uses_toolchain(vendor, monkeypatch, tmp_path):
    exe = _exe(tmp_path / "ff", "ffmpeg.exe")
    monkeypatch.setattr(toolchain, "system_path", lambda name: exe)
    paths.reset_ffmpeg_cache()
    try:
        assert paths.find_ffmpeg() == str(exe)
    finally:
        paths.reset_ffmpeg_cache()


def test_run_ytdlp_reports_missing_package(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)  # import 会失败
    assert toolchain.run_ytdlp(["--version"]) == 127
    assert "yt-dlp" in capsys.readouterr().err


def test_run_ytdlp_returns_exit_code(monkeypatch):
    fake = types.ModuleType("yt_dlp")
    fake.main = lambda: 3
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    assert toolchain.run_ytdlp(["--version"]) == 3
    assert sys.argv[0] == "yt-dlp"


def test_run_ytdlp_passes_argv(monkeypatch):
    seen = {}
    fake = types.ModuleType("yt_dlp")

    def main():
        seen["argv"] = list(sys.argv)
        return 0

    fake.main = main
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    toolchain.run_ytdlp(["--version", "https://example.com/v"])
    assert seen["argv"] == ["yt-dlp", "--version", "https://example.com/v"]
