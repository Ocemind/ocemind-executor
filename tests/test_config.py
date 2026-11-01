# -*- coding: utf-8 -*-
"""downloader.config —— 数据目录、设置读写与 Tk 的 out_dir 同步。

所有数据都在 `%APPDATA%\\ocemind`（`paths.app_dir()`）。同目录下的
`config.json` 由 Tk 界面使用：第一次跑时拿它的 `out_dir` 当默认保存目录，
之后每次保存设置都同步回去，两边看到的是同一个「默认保存目录」。
"""
import json
from pathlib import Path

from ocemind import paths
from ocemind.downloader.config import DEFAULT_SETTINGS, Config


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_fresh_home_gets_default_settings(tmp_path):
    cfg = Config(home=tmp_path)
    assert cfg.settings_path == tmp_path / "settings.json"
    assert cfg.tasks_path == tmp_path / "tasks.json"
    assert cfg.log_path == tmp_path / "app.log"
    assert cfg.tk_config_path == tmp_path / "config.json"
    assert cfg.settings_path.exists()
    saved = _read(cfg.settings_path)
    assert set(saved) == set(DEFAULT_SETTINGS)
    assert cfg.get("defaultDir") == saved["defaultDir"]


def test_tk_out_dir_seeds_default_dir(tmp_path):
    """第一次跑：没有 settings.json，就用 Tk 界面记的保存目录。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"out_dir": "F:/课程"}), encoding="utf-8")

    cfg = Config(home=tmp_path)

    assert cfg.get("defaultDir") == "F:/课程"


def test_existing_settings_are_not_overwritten(tmp_path):
    """第二次启动时 Tk 的旧值不能把用户改过的设置盖回去。"""
    (tmp_path / "settings.json").write_text(
        json.dumps({"defaultDir": "G:/新目录"}), encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"out_dir": "F:/课程"}), encoding="utf-8")

    cfg = Config(home=tmp_path)

    assert cfg.get("defaultDir") == "G:/新目录"


def test_save_syncs_tk_out_dir(tmp_path):
    cfg = Config(home=tmp_path)
    cfg.update({"defaultDir": "H:/新保存目录"})
    assert _read(tmp_path / "config.json")["out_dir"] == "H:/新保存目录"


def test_save_keeps_other_tk_config_keys(tmp_path):
    """config.json 里还有 Tk 自己的别的设置，同步时不能整个覆盖掉。"""
    (tmp_path / "config.json").write_text(
        json.dumps({"out_dir": "old", "workers": 12}), encoding="utf-8")
    cfg = Config(home=tmp_path)

    cfg.save()

    data = _read(tmp_path / "config.json")
    assert data["workers"] == 12
    assert data["out_dir"] == cfg.get("defaultDir")


def test_update_drops_unknown_keys_but_keeps_course_settings(tmp_path):
    cfg = Config(home=tmp_path)
    cfg.update({"courseWorkers": 32, "bogusKey": 1})
    assert cfg.get("courseWorkers") == 32
    assert "bogusKey" not in _read(tmp_path / "settings.json")


def test_changing_ffmpeg_path_resets_cache(tmp_path, monkeypatch):
    """自动探测的结果是模块级缓存，设置里改过路径后必须让它失效。"""
    calls = []
    monkeypatch.setattr(paths, "reset_ffmpeg_cache", lambda: calls.append(1))
    cfg = Config(home=tmp_path)

    cfg.update({"ffmpegPath": "D:/y/ffmpeg.exe"})
    assert len(calls) == 1

    # 值没变（包括别的键一起提交）就不该白白清缓存
    cfg.update({"ffmpegPath": "D:/y/ffmpeg.exe", "maxConcurrent": 5})
    assert len(calls) == 1


def test_reset_ffmpeg_cache_forces_rediscovery(monkeypatch):
    # toolchain 会把 which() 的结果规整成 Path，比较前统一一下分隔符
    a = str(Path("C:/a/ffmpeg.exe"))
    b = str(Path("C:/b/ffmpeg.exe"))
    monkeypatch.setattr(paths, "_FFMPEG_CACHE", [])
    monkeypatch.setattr(paths.shutil, "which", lambda name: "C:/a/ffmpeg.exe")
    assert paths.find_ffmpeg() == a
    monkeypatch.setattr(paths.shutil, "which", lambda name: "C:/b/ffmpeg.exe")
    assert paths.find_ffmpeg() == a   # 命中缓存
    paths.reset_ffmpeg_cache()
    assert paths.find_ffmpeg() == b


def test_app_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OCEMIND_HOME", str(tmp_path / "override"))
    assert paths.app_dir() == str(tmp_path / "override")
    assert not (tmp_path / "override").exists()  # 只是一个路径，不该被建出来
