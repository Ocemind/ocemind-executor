# -*- coding: utf-8 -*-
"""版本号在四个地方必须一致。

Python 包、Tauri 壳、npm 包、Cargo 包各写一份版本；发版时漏改一个，
更新检查（`latest.json` 与当前版本比较）和安装包元数据就会对不上，
而且不会报错。这里直接钉住。
"""
import json
import re
from pathlib import Path

from ocemind import __version__, paths

ROOT = Path(paths.project_root())


def _cargo_package_version(text: str) -> str:
    # 只取 [package] 段里的 version，别把依赖的 `serde = "1"` 当成版本
    package = text.split("[package]", 1)[1].split("\n[", 1)[0]
    return re.search(r'^version = "([^"]+)"', package, re.M).group(1)


def test_python_versions_match():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == __version__


def test_desktop_versions_match():
    tauri = json.loads(
        (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert tauri["version"] == __version__

    npm = json.loads(
        (ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert npm["version"] == __version__

    cargo = (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    assert _cargo_package_version(cargo) == __version__
