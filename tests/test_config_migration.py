# -*- coding: utf-8 -*-
"""downloader.config —— 数据目录统一与一次性迁移。

合并前那版下载器把配置存在 `%APPDATA%\\TDMLite`，Tk 界面把保存目录存在
`%APPDATA%\\ocemind\\config.json` 的 `out_dir` 里。合并后三者共用
`%APPDATA%\\ocemind`，旧数据只能迁一次：新 settings.json 已经在就绝不能再覆盖，
否则用户改过的设置会被旧值悄悄盖回去。
"""
import json

import pytest

from ocemind import paths
from ocemind.downloader.config import DEFAULT_SETTINGS, Config


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_fresh_home_gets_default_settings(tmp_path):
    cfg = Config(home=tmp_path, legacy_home=tmp_path / "none")
    assert cfg.settings_path == tmp_path / "settings.json"
    assert cfg.tasks_path == tmp_path / "tasks.json"
    assert cfg.log_path == tmp_path / "app.log"
    assert cfg.tk_config_path == tmp_path / "config.json"
    assert cfg.settings_path.exists()
    saved = _read(cfg.settings_path)
    assert set(saved) == set(DEFAULT_SETTINGS)
    assert cfg.get("defaultDir") == saved["defaultDir"]


def test_legacy_settings_are_overlaid(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "settings.json").write_text(json.dumps({
        "defaultDir": "E:/片库",
        "defaultConnections": 16,
        "notAKey": "drop me",
    }), encoding="utf-8")
    home = tmp_path / "home"

    cfg = Config(home=home, legacy_home=legacy)

    assert cfg.get("defaultDir") == "E:/片库"
    assert cfg.get("defaultConnections") == 16
    saved = _read(cfg.settings_path)
    assert "notAKey" not in saved


def test_legacy_tasks_are_copied(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    tasks = [{"id": "abc123", "url": "https://example.com/a.zip", "kind": "file"}]
    (legacy / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")

    cfg = Config(home=tmp_path / "home", legacy_home=legacy)

    assert cfg.tasks_path.exists()
    assert _read(cfg.tasks_path) == tasks


def test_tk_out_dir_seeds_default_dir(tmp_path):
    """没有旧版本的设置时，退回 Tk 界面记的保存目录。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"out_dir": "F:/课程"}), encoding="utf-8")

    cfg = Config(home=tmp_path, legacy_home=tmp_path / "none")

    assert cfg.get("defaultDir") == "F:/课程"


def test_legacy_settings_win_over_tk_out_dir(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "settings.json").write_text(
        json.dumps({"defaultDir": "E:/片库"}), encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"out_dir": "F:/课程"}), encoding="utf-8")

    cfg = Config(home=tmp_path, legacy_home=legacy)

    assert cfg.get("defaultDir") == "E:/片库"


def test_migration_never_overwrites_existing_settings(tmp_path):
    """第二次启动时旧值不能把用户改过的设置盖回去。"""
    (tmp_path / "settings.json").write_text(
        json.dumps({"defaultDir": "G:/新目录"}), encoding="utf-8")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "settings.json").write_text(
        json.dumps({"defaultDir": "E:/片库"}), encoding="utf-8")

    cfg = Config(home=tmp_path, legacy_home=legacy)

    assert cfg.get("defaultDir") == "G:/新目录"


def test_migration_does_not_delete_legacy_files(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    src = legacy / "settings.json"
    src.write_text(json.dumps({"defaultDir": "E:/片库"}), encoding="utf-8")

    Config(home=tmp_path / "home", legacy_home=legacy)

    assert src.exists()


def test_save_syncs_tk_out_dir(tmp_path):
    cfg = Config(home=tmp_path, legacy_home=tmp_path / "none")
    cfg.update({"defaultDir": "H:/新保存目录"})
    assert _read(tmp_path / "config.json")["out_dir"] == "H:/新保存目录"


def test_save_keeps_other_tk_config_keys(tmp_path):
    """config.json 里还有 Tk 自己的别的设置，同步时不能整个覆盖掉。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"out_dir": "old", "workers": 12}), encoding="utf-8")
    cfg = Config(home=tmp_path, legacy_home=tmp_path / "none")

    cfg.save()

    data = _read(tmp_path / "config.json")
    assert data["workers"] == 12
    assert data["out_dir"] == cfg.get("defaultDir")


def test_update_drops_unknown_keys_but_keeps_course_settings(tmp_path):
    cfg = Config(home=tmp_path, legacy_home=tmp_path / "none")
    cfg.update({"courseWorkers": 32, "bogusKey": 1})
    assert cfg.get("courseWorkers") == 32
    assert "bogusKey" not in _read(tmp_path / "settings.json")


def test_changing_ffmpeg_path_resets_cache(tmp_path, monkeypatch):
    """自动探测的结果是模块级缓存，设置里改过路径后必须让它失效。"""
    calls = []
    monkeypatch.setattr(paths, "reset_ffmpeg_cache", lambda: calls.append(1))
    cfg = Config(home=tmp_path, legacy_home=tmp_path / "none")

    cfg.update({"ffmpegPath": "D:/y/ffmpeg.exe"})
    assert len(calls) == 1

    # 值没变（包括别的键一起提交）就不该白白清缓存
    cfg.update({"ffmpegPath": "D:/y/ffmpeg.exe", "maxConcurrent": 5})
    assert len(calls) == 1


def test_reset_ffmpeg_cache_forces_rediscovery(monkeypatch):
    monkeypatch.setattr(paths, "_FFMPEG_CACHE", [])
    monkeypatch.setattr(paths.shutil, "which", lambda name: "C:/a/ffmpeg.exe")
    assert paths.find_ffmpeg() == "C:/a/ffmpeg.exe"
    monkeypatch.setattr(paths.shutil, "which", lambda name: "C:/b/ffmpeg.exe")
    assert paths.find_ffmpeg() == "C:/a/ffmpeg.exe"   # 命中缓存
    paths.reset_ffmpeg_cache()
    assert paths.find_ffmpeg() == "C:/b/ffmpeg.exe"


def test_explicit_home_never_reads_real_legacy_dir(tmp_path, monkeypatch):
    """传了 home 就是测试/便携模式，绝不去碰真实机器上的旧目录。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    cfg = Config(home=tmp_path / "home")
    assert cfg.legacy_home is None


def test_app_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OCEMIND_HOME", str(tmp_path / "override"))
    assert paths.app_dir() == str(tmp_path / "override")
    assert not (tmp_path / "override").exists()  # 只是一个路径，不该被建出来
