# -*- coding: utf-8 -*-
"""算出 yt-dlp 用到、但我们自己的代码没用到、因此没被打进包的标准库模块。

背景
----
yt-dlp 是运行时下载的 zipapp，不在 PyInstaller 的分析范围里（见
`ocemind-sidecar.spec` 的 `excludes`）。它 import 的一部分标准库模块，
主程序自己从来不 import，PyInstaller 就不会打进 `base_library.zip`。
于是冻结进程里 `import yt_dlp` 会报 `No module named 'optparse'`、
`No module named 'html.parser'` ——依赖装是装上了，解释器里没有。

注意要收**带点号的完整名字**：PyInstaller 的 hiddenimports 里写 `html`
只带进来包本身，`html.parser` 仍然缺。

这里在**构建时**扫一遍构建机上装好的 yt-dlp（requirements.txt 里有），
把它导入的标准库模块补成 hiddenimports。构建机没装 yt-dlp 时用下面这份
按 yt-dlp 2026.10.26 抄下来的兜底清单。

这个文件被两个 .spec 引用，别挪位置。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys

#: 兜底：yt-dlp 2026.10.26 导入的标准库模块（构建机没有 yt_dlp 时使用）
FALLBACK = [
    "abc", "array", "asyncio", "atexit", "base64", "binascii", "bisect",
    "calendar", "codecs", "collections", "collections.abc", "concurrent",
    "concurrent.futures", "contextlib", "contextvars", "copy", "ctypes",
    "ctypes.wintypes", "dataclasses", "datetime", "email", "email.header",
    "email.message", "email.utils", "enum", "errno", "fileinput", "functools",
    "getpass", "glob", "hashlib", "heapq", "hmac", "html", "html.entities",
    "html.parser", "http", "http.client", "http.cookiejar", "http.cookies",
    "http.server", "importlib", "importlib.abc", "importlib.machinery",
    "importlib.resources", "importlib.util", "inspect", "io", "itertools",
    "json", "locale", "logging", "math", "mimetypes", "msvcrt", "netrc",
    "operator", "optparse", "os", "os.path", "pathlib", "pkgutil", "platform",
    "quopri", "random", "re", "secrets", "shlex", "shutil", "signal", "socket",
    "sqlite3", "ssl", "string", "struct", "subprocess", "sys", "sysconfig",
    "tempfile", "textwrap", "threading", "time", "tokenize", "traceback",
    "types", "typing", "unicodedata", "urllib", "urllib.error", "urllib.parse",
    "urllib.request", "urllib.response", "uuid", "warnings", "xml",
    "xml.etree", "xml.etree.ElementTree", "zipfile", "zipimport", "zlib",
]


def _scan(package_dir: pathlib.Path) -> set[str]:
    """收集包内所有 .py 的**标准库**导入，保留点号（html.parser 这种）。"""
    stdlib = set(sys.stdlib_module_names)
    found: set[str] = set()
    for py in package_dir.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] in stdlib:
                        found.add(".".join(parts))
            elif (isinstance(node, ast.ImportFrom)
                  and node.level == 0 and node.module):
                parts = node.module.split(".")
                if parts[0] not in stdlib:
                    continue
                found.add(".".join(parts))
                # `from html import parser` 这种也要补上子模块
                for alias in node.names:
                    candidate = ".".join(parts + [alias.name])
                    if _importable(candidate):
                        found.add(candidate)
    return found


def _importable(name: str) -> bool:
    """真的导一次：`pty` / `fcntl` 在 Windows 上 find_spec 找得到，
    但 import 会抛 ImportError，那种模块不能写进 hiddenimports。"""
    try:
        importlib.import_module(name)
        return True
    except Exception:  # noqa: BLE001 - 任何导入失败都当不可用
        return False


def ytdlp_stdlib_imports() -> list[str]:
    """返回要补进 hiddenimports 的模块名（已过滤本机不存在的平台模块）。"""
    names: set[str] = set(FALLBACK)
    try:
        spec = importlib.util.find_spec("yt_dlp")
        if spec and spec.submodule_search_locations:
            names |= _scan(pathlib.Path(list(spec.submodule_search_locations)[0]))
    except (ImportError, ValueError, OSError):
        pass
    return sorted(n for n in names
                  if n != "__future__" and _importable(n))


if __name__ == "__main__":
    print("\n".join(ytdlp_stdlib_imports()))
