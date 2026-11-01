# -*- coding: utf-8 -*-
"""外部工具（ffmpeg / yt-dlp）的定位。

两个工具随软件一起打包，装到别人电脑上不用再单独装。但这台机器上要是
已经有了，就用本机的：不重复占一份空间，别人装的是新版也不会被自带的
旧版盖掉。

查找顺序：设置里填的 → 本机装的（PATH、常见安装目录）→ 自带的。

yt-dlp 是 Python 包，直接编进 sidecar；ffmpeg 是 150 MB 的二进制，作为普通
文件装到程序目录的 `tools/ffmpeg/` 下（源码运行时对应项目的 `vendor/tools/`）。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

from . import paths

#: 各平台下 ffmpeg 可执行文件名（Linux / AppImage 上不能带 .exe）
EXE_NAMES = {"windows": "ffmpeg.exe", "linux": "ffmpeg", "darwin": "ffmpeg"}


def exe_name(tool: str = "ffmpeg") -> str:
    system = "windows" if os.name == "nt" else (
        "darwin" if sys.platform == "darwin" else "linux")
    return EXE_NAMES.get(system, tool)


def bundled_dir() -> Path:
    """随软件打包的工具目录。

    ffmpeg 有 150 MB，不能塞进 onefile 的 sidecar（每次启动都要解包一遍，
    实测慢 7 秒），所以它是**普通文件**装程序旁边的：

    - 安装版：Tauri 的 `bundle.resources` 拷到程序目录下的 `tools/`
    - 便携版：`make_portable.py` 把 `vendor/tools` 拷成 `tools/`
    - 源码运行：项目里的 `vendor/tools`（由 `tools/fetch_vendor.py` 下载）
    """
    here = Path(paths.resource_dir())
    if (here / "tools" / "ffmpeg").is_dir():
        return here / "tools"
    if paths.is_frozen():
        base = getattr(sys, "_MEIPASS", "") or str(here)
        return Path(base) / "tools"
    return here / "vendor" / "tools"


def _find_file(root: Path, name: str) -> Path | None:
    if not root.is_dir():
        return None
    direct = root / name
    if direct.is_file():
        return direct
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def bundled_path(name: str) -> Path | None:
    """自带的那个可执行文件。yt-dlp 在包里而不在磁盘上，返回 None。"""
    if name == "ffmpeg":
        return _find_file(bundled_dir() / "ffmpeg", exe_name())
    return None


def has_bundled_ytdlp() -> bool:
    """yt-dlp 作为 Python 包编进 sidecar，能不能 import 就知道在不在。"""
    try:
        return importlib.util.find_spec("yt_dlp") is not None
    except (ImportError, ValueError):
        return False


def _common_dirs() -> list[Path]:
    """包管理器 / 手动安装的常见落点（PATH 没覆盖到的那些）。"""
    if os.name == "nt":
        roots = [os.environ.get(k) for k in
                 ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA",
                  "USERPROFILE", "ProgramData")]
        dirs = []
        for root in filter(None, roots):
            dirs += [Path(root) / "ffmpeg" / "bin",
                     Path(root) / "scoop" / "shims",
                     Path(root) / "chocolatey" / "bin",
                     Path(root) / "Microsoft" / "WinGet" / "Links"]
        dirs.append(Path("C:/ffmpeg/bin"))
        return dirs
    return [Path("/usr/local/bin"), Path("/usr/bin"), Path("/opt/homebrew/bin"),
            Path.home() / ".local" / "bin"]


def system_path(name: str) -> Path | None:
    """这台机器自己装的工具。有就用它，不再占一份自带的。"""
    exes = ("yt-dlp", "yt-dlp.exe") if name == "ytdlp" else (exe_name(), "ffmpeg.exe")
    for exe in exes:
        found = shutil.which(exe)
        if found:
            return Path(found)
    for base in _common_dirs():
        for exe in exes:
            p = base / exe
            if p.is_file():
                return p
    return None


def resolve(name: str, explicit: str = "") -> dict:
    """定位一个工具，返回 `{path, source}`，source ∈ settings/system/bundled/空。

    `path` 对 yt-dlp 是「怎么调用」的形式：本机装的是可执行文件路径，自带的是
    空串（要靠 sidecar 自己的解释器跑）。
    """
    if explicit and Path(explicit).exists():
        return {"path": str(explicit), "source": "settings"}
    if name == "ytdlp":
        # 包里的 yt_dlp 排在 PATH 上的 yt-dlp 前面：pip 生成的 yt-dlp.exe 是
        # 重新链接出来的、没有签名，在这台机器上会被智能应用控制直接拦掉
        if has_bundled_ytdlp():
            return {"path": "", "source": "system" if not paths.is_frozen() else "bundled"}
        found = system_path("ytdlp")
        return {"path": str(found), "source": "system"} if found else {"path": "", "source": ""}
    found = system_path(name)
    if found:
        return {"path": str(found), "source": "system"}
    bundled = bundled_path(name)
    if bundled:
        return {"path": str(bundled), "source": "bundled"}
    return {"path": "", "source": ""}


def ffmpeg_exe(explicit: str = "") -> str:
    """可执行文件路径；找不到返回空串。"""
    return resolve("ffmpeg", explicit)["path"]


def ytdlp_command(explicit: str = "") -> list[str]:
    """给下载引擎用的命令前缀。

    冻结后没有独立的 python，自带的 yt-dlp 只能让 sidecar 自己派发
    （`--run-ytdlp`）：直接写 `-m yt_dlp` 会被当成程序自己的命令行参数吃掉。
    """
    found = resolve("ytdlp", explicit)
    if found["path"]:
        return [found["path"]]
    if paths.is_frozen():
        return [sys.executable, "--run-ytdlp"]
    return [sys.executable, "-m", "yt_dlp"]


def run_ytdlp(argv: list[str]) -> int:
    """在 sidecar 自己的解释器里跑 yt-dlp 的 CLI。"""
    try:
        import yt_dlp
    except ImportError as exc:
        print(f"yt-dlp 不可用：{exc}", file=sys.stderr)
        return 127
    sys.argv = ["yt-dlp"] + list(argv)
    try:
        return int(yt_dlp.main() or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
