# -*- coding: utf-8 -*-
"""yt-dlp 自更新。

yt-dlp 是整套东西里唯一「站点一改就得跟着改」的依赖，但它被编进了 sidecar，
只能跟着软件发版。平台改版是按周来的，为一个解析规则重新签名、重新打包、
让用户再装一遍，太贵了。

所以留了个纯 Python 的后门：把新版 wheel 解到用户数据目录，起子进程时让它
排在自带那份前面。

- **不碰 exe**：这台机器有智能应用控制，新编出来的 exe 直接起不来（os error
  4551）；yt-dlp 的 exe 也是无签名的 PyInstaller 产物，同样会被拦。而 wheel
  只是 .py 文件，没有这问题。
- **能回退**：删掉目录就是自带版本，不需要重装软件。
- **不污染环境变量**：只在派发 yt-dlp 的子进程里生效。

目录：`%LOCALAPPDATA%\\ocemind\\ytdlp\\yt_dlp\\...`（便携版跟着
`OCEMIND_LOCAL_HOME` 走）。
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from . import paths

PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
PYPI_HOST = "https://pypi.org"


# ---------------------------------------------------------------- 目录

def package_root() -> Path:
    return Path(paths.local_app_dir()) / "ytdlp"


def package_dir() -> Path:
    """外部 yt-dlp 包的父目录（里面是 `yt_dlp/`）。"""
    return package_root()


def external_package() -> Path:
    return package_dir() / "yt_dlp"


def has_external() -> bool:
    return (external_package() / "__init__.py").is_file()


# ---------------------------------------------------------------- 版本

_VER_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def _read_version_file(pkg: Path) -> str:
    """从 version.py 里抠版本号，不去 import——那会把整个包拉进当前进程。"""
    for name in ("version.py", "_version.py"):
        f = pkg / name
        if f.is_file():
            try:
                m = _VER_RE.search(f.read_text(encoding="utf-8", errors="replace"))
                return m.group(1) if m else ""
            except OSError:
                return ""
    return ""


def installed_version() -> str:
    return _read_version_file(external_package()) if has_external() else ""


def effective() -> dict:
    """现在真正会被用上的那份：外部包优先，没有就是编进 sidecar 的。"""
    ext = installed_version()
    if ext:
        return {"version": ext, "source": "external", "dir": str(package_dir())}
    try:
        mod = importlib.import_module("yt_dlp")
        ver = getattr(getattr(mod, "version", None), "__version__", "") or ""
    except Exception:  # noqa: BLE001
        ver = ""
    return {"version": ver, "source": "bundled" if ver else "", "dir": ""}


def _parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def is_newer(latest: str, current: str) -> bool:
    """yt-dlp 的版本是日期：2026.10.26 > 2026.08.19。解析不出来就当没更新。"""
    a, b = _parts(latest), _parts(current)
    if not a or not b:
        return False
    return a > b


# ---------------------------------------------------------------- 网络

def _proxies(cfg) -> dict | None:
    getter = getattr(cfg, "proxies", None)
    if callable(getter):
        try:
            return getter()
        except Exception:  # noqa: BLE001
            return None
    return None


def _normalize_proxy(proxies: dict | None) -> dict | None:
    """socks5 → socks5h：让 DNS 也在代理那头解析，不然国内解析 pypi.org 会拖很久。"""
    if not proxies:
        return None
    out = {}
    for key, url in proxies.items():
        if isinstance(url, str) and url.startswith("socks5://"):
            url = "socks5h://" + url[len("socks5://"):]
        out[key] = url
    return out


def _session(cfg):
    import requests

    s = requests.Session()
    proxies = _normalize_proxy(_proxies(cfg))
    if proxies:
        s.proxies.update(proxies)
    s.headers["User-Agent"] = "ocemind-executor/ytdlp-updater"
    return s


def check(cfg, timeout: float = 15.0) -> dict:
    """问 PyPI 最新版是多少，跟当前用的比一比。"""
    cur = effective()
    result = {"ok": False, "current": cur["version"], "source": cur["source"],
              "latest": "", "has_update": False, "size": 0, "url": "",
              "sha256": "", "error": ""}
    try:
        resp = _session(cfg).get(PYPI_JSON, timeout=timeout)
        resp.raise_for_status()
        doc = resp.json()
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"连不上 PyPI：{exc.__class__.__name__}"
        return result

    info = doc.get("info") or {}
    result["latest"] = info.get("version") or ""
    wheel = _pick_wheel(doc.get("urls") or [])
    if wheel:
        result["url"] = wheel["url"]
        result["size"] = wheel.get("size") or 0
        result["sha256"] = (wheel.get("digests") or {}).get("sha256") or ""
    result["ok"] = bool(result["latest"])
    result["has_update"] = is_newer(result["latest"], result["current"])
    return result


def _pick_wheel(files: list) -> dict:
    """挑通用 wheel（py3-none-any），别去下平台专用的。"""
    for f in files:
        name = f.get("filename") or ""
        if f.get("packagetype") == "bdist_wheel" and name.endswith(".whl") \
                and "py3-none-any" in name:
            return f
    for f in files:
        if (f.get("filename") or "").endswith(".whl"):
            return f
    return {}


# ---------------------------------------------------------------- 安装

def install(cfg, url: str, sha256: str = "", timeout: float = 60.0) -> dict:
    """下载 wheel 并换上去。失败时保持原样，不留下半截目录。"""
    if not url:
        return {"ok": False, "error": "没有可下载的 wheel 地址"}

    root = package_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dl-", dir=str(root)))
    try:
        whl = tmp / "yt_dlp.whl"
        digest = hashlib.sha256()
        with _session(cfg).get(url, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            with whl.open("wb") as fh:
                for chunk in resp.iter_content(1 << 16):
                    if chunk:
                        digest.update(chunk)
                        fh.write(chunk)
        if sha256 and digest.hexdigest().lower() != sha256.lower():
            return {"ok": False, "error": "下载的文件校验不对，已放弃"}

        stage = tmp / "stage"
        with zipfile.ZipFile(whl) as zf:
            zf.extractall(stage)
        new_pkg = stage / "yt_dlp"
        if not (new_pkg / "__init__.py").is_file():
            return {"ok": False, "error": "包里没有 yt_dlp，可能是空包"}

        version = _read_version_file(new_pkg)
        old = root / ".old"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        if external_package().exists():
            external_package().rename(old)
        shutil.move(str(new_pkg), str(external_package()))
        shutil.rmtree(old, ignore_errors=True)
        return {"ok": True, "version": version, "path": str(package_dir())}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove_external() -> bool:
    """回到自带版本。"""
    target = external_package()
    if not target.exists():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


# ---------------------------------------------------------------- 让子进程用上新版

class _ExternalFinder:
    """排在 PyInstaller 的 FrozenImporter 前面，先到磁盘上的新版里找。

    只在派发 yt-dlp 的子进程里装——那边是全新进程，不会有「模块已经
    导入过了」的问题。
    """

    def __init__(self, root: Path) -> None:
        self.root = str(root)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "yt_dlp" and not fullname.startswith("yt_dlp."):
            return None
        if not has_external():
            return None
        search = list(path) if path else [self.root]
        try:
            return importlib.machinery.PathFinder.find_spec(fullname, search, target)
        except Exception:  # noqa: BLE001
            return None


_finder_installed = False


def activate() -> bool:
    """把外部包插到 import 链最前面。装上了返回 True。"""
    global _finder_installed
    if not has_external():
        return False
    if _finder_installed:
        return True
    sys.meta_path.insert(0, _ExternalFinder(package_dir()))
    root = str(package_dir())
    if root not in sys.path:
        sys.path.insert(0, root)
    _finder_installed = True
    return True


def apply_env(env: dict) -> dict:
    """给 `python -m yt_dlp` 这种子进程用：PYTHONPATH 放最前面。

    冻结版不认 PYTHONPATH，那边靠 `run_ytdlp` 里的 `activate()`。
    """
    if not has_external():
        return env
    root = str(package_dir())
    old = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = root + (os.pathsep + old if old else "")
    return env
