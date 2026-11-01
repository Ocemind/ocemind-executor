# -*- coding: utf-8 -*-
"""外部开源工具（ffmpeg / yt-dlp）的探测与按需安装。

为什么要有这一层
----------------
安装包里带了 Python（PyInstaller 烘进 sidecar），但 ffmpeg 和 yt-dlp 是
独立项目、更新频率和我们的发版节奏对不上，打进去就等于每升一次 ffmpeg
就要重发一次安装包。所以：

1. **先探测本机**：PATH、常见安装目录里有就直接用，不重复下载 100 MB；
2. **没有才下载**：下载到数据目录（`%APPDATA%\\ocemind\\tools\\`），
   不动系统 PATH，卸载时删掉数据目录即可；
3. **优先国内镜像**：镜像上放一份 `index.json` + 文件，客户端先问镜像、
   失败再回落到 GitHub。镜像由 `tools/sync_tools_mirror.py` 从 GitHub
   同步，服务器上挂个定时任务就能一直保持最新。

下载一律校验 SHA-256（镜像的 `index.json` 里带），没有校验值的来源不用。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import paths

#: 镜像/清单里用的平台键
def platform_key() -> str:
    system = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("amd64", "x86_64") else (
        "aarch64" if machine in ("arm64", "aarch64") else machine)
    return f"{system}-{arch}"


#: 兜底来源：没有配镜像时直接从上游 GitHub Release 拿
FALLBACK_URLS = {
    "ffmpeg": {
        "windows-x86_64": "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-lgpl.zip",
        "linux-x86_64": "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-lgpl.tar.xz",
        "linux-aarch64": "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linuxarm64-lgpl.tar.xz",
    },
    "ytdlp": {
        # 官方把它叫 zipapp：一个 zip，Python 直接能 import 里面的 yt_dlp
        "any": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
    },
}

#: 各平台可执行文件名
EXE_NAMES = {
    "ffmpeg": {"windows": "ffmpeg.exe", "linux": "ffmpeg", "darwin": "ffmpeg"},
}

DEFAULT_MIRROR = ""     # 由设置项 / 环境变量指定；空 = 直接走 GitHub
UA = "ocemind-toolchain/1"

_lock = threading.Lock()
#: "/api/system" 直接读它，下载进度也写在这里
STATE: dict[str, dict] = {}


def tools_dir() -> Path:
    return Path(paths.app_dir()) / "tools"


def _state(name: str) -> dict:
    with _lock:
        return STATE.setdefault(name, {"status": "unknown", "progress": 0.0,
                                       "version": "", "path": "", "error": ""})


def _set_state(name: str, **kw) -> None:
    st = _state(name)
    with _lock:
        st.update(kw)


def mirror_base(setting: str = "") -> str:
    """镜像根地址。设置项 > 环境变量 > 内置默认（空）。"""
    for cand in (setting, os.environ.get("OCEMIND_TOOLS_MIRROR", ""), DEFAULT_MIRROR):
        cand = (cand or "").strip()
        if cand:
            return cand.rstrip("/")
    return ""


def _download_once(url: str, dest: Path, progress=None,
                   expected_sha256: str = "", total_hint: int = 0) -> None:
    """流式下载到 dest，边下边算 SHA-256。先写 .part，成功后再改名。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("content-length") or total_hint or 0)
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if expected_sha256:
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"SHA-256 校验失败：{actual[:16]}… ≠ {expected_sha256[:16]}…")
    os.replace(tmp, dest)


def _download(url: str, dest: Path, progress=None, expected_sha256: str = "",
              total_hint: int = 0, attempts: int = 3) -> None:
    """带重试的下载。

    GitHub 在国内常见的是 TLS 半路断开（`UNEXPECTED_EOF_WHILE_READING`），
    同一地址重试往往就好了，没必要让用户手动再点一次「下载缺失工具」。
    校验失败不重试——那是内容真的不对，重下也是同一个结果。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            _download_once(url, dest, progress, expected_sha256, total_hint)
            return
        except RuntimeError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            last = exc
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def _safe_extract_tar(archive: Path, target_dir: Path) -> None:
    with tarfile.open(archive) as tf:
        root = target_dir.resolve()
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                continue
            dest = (target_dir / member.name).resolve()
            if not str(dest).startswith(str(root) + os.sep):
                continue
            tf.extract(member, target_dir)


def _extract(archive: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            root = target_dir.resolve()
            for info in zf.infolist():
                dest = (target_dir / info.filename).resolve()
                if not str(dest).startswith(str(root) + os.sep):
                    continue
                zf.extract(info, target_dir)
    elif tarfile.is_tarfile(archive):
        _safe_extract_tar(archive, target_dir)
    else:
        raise RuntimeError(f"不认识的压缩格式：{archive.name}")


def _find_file(root: Path, name: str) -> Path | None:
    direct = root / name
    if direct.is_file():
        return direct
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def _install_ffmpeg(archive: Path) -> Path:
    target = tools_dir() / "ffmpeg"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    _extract(archive, target)
    exe_name = EXE_NAMES["ffmpeg"].get(platform.system().lower(), "ffmpeg")
    exe = _find_file(target, exe_name)
    if not exe:
        raise RuntimeError("压缩包里没有找到 ffmpeg 可执行文件")
    # 官方包里 ffplay 有 130 MB，我们用不到；只留 ffmpeg / ffprobe
    for player in exe.parent.glob("ffplay*"):
        try:
            player.unlink()
        except OSError:
            pass
    if os.name != "nt":
        try:
            exe.chmod(0o755)
        except OSError:
            pass
    return exe


def _install_ytdlp(archive: Path) -> Path:
    """yt-dlp 是 zipapp：原样放着，运行时用 zipimport 加载就行。"""
    target = tools_dir() / "ytdlp"
    target.mkdir(parents=True, exist_ok=True)
    dest = target / "yt-dlp.zip"
    shutil.copy2(archive, dest)
    return dest


def _fetch_manifest(base: str) -> dict:
    if not base:
        return {}
    try:
        with urllib.request.urlopen(
                urllib.request.Request(base + "/index.json", headers={"User-Agent": UA}),
                timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (urllib.error.URLError, OSError, ValueError):
        return {}


def _entry_for(manifest: dict, name: str) -> dict:
    tools = manifest.get("tools") if isinstance(manifest, dict) else None
    if not isinstance(tools, dict):
        return {}
    by_platform = tools.get(name)
    if not isinstance(by_platform, dict):
        return {}
    key = platform_key() if name != "ytdlp" else "any"
    entry = by_platform.get(key) or by_platform.get("any")
    return entry if isinstance(entry, dict) else {}


def managed_path(name: str) -> Path | None:
    """已经装到数据目录里的工具路径。"""
    if name == "ffmpeg":
        exe_name = EXE_NAMES["ffmpeg"].get(platform.system().lower(), "ffmpeg")
        exe = _find_file(tools_dir() / "ffmpeg", exe_name)
        return exe
    if name == "ytdlp":
        p = tools_dir() / "ytdlp" / "yt-dlp.zip"
        return p if p.is_file() else None
    return None


def _common_dirs() -> list[Path]:
    """包管理器/手动安装的常见落点（PATH 没覆盖到的那些）。"""
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
    """本机自己装好的工具（PATH 或常见目录）。有就不下载。"""
    exes = ("yt-dlp", "yt-dlp.exe") if name == "ytdlp" else ("ffmpeg", "ffmpeg.exe")
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


def available(name: str, explicit: str = "") -> Path | None:
    """设置里指定的 > 托管目录 > 本机已装。

    设置里填了路径还去下载一份是纯浪费，所以显式路径排最前。
    """
    if explicit and Path(explicit).exists():
        return Path(explicit)
    return managed_path(name) or system_path(name)


def ensure(name: str, base: str = "", progress=None) -> Path:
    """确保工具可用：本机已有记录的状态优先，缺了就从镜像/上游下载。"""
    found = managed_path(name)
    if found:
        _set_state(name, status="ok", path=str(found), progress=1.0, error="")
        return found

    _set_state(name, status="downloading", progress=0.0, error="")
    try:
        return _ensure(name, base, progress)
    except Exception as exc:
        # 直接调用（不是走后台线程）时也要把状态落到 error，否则界面永远显示
        # 「下载中」，重试按钮也点不动
        _set_state(name, status="error", progress=0.0, error=str(exc))
        raise


def _ensure(name: str, base: str, progress=None) -> Path:
    manifest = _fetch_manifest(base) if base else {}
    entry = _entry_for(manifest, name)
    if entry.get("url"):
        url = entry["url"]
        if base and not url.startswith("http"):
            url = f"{base}/{url.lstrip('/')}"
        sha = entry.get("sha256") or ""
        size = int(entry.get("size") or 0)
        version = entry.get("version") or ""
    else:
        key = platform_key() if name != "ytdlp" else "any"
        url = FALLBACK_URLS.get(name, {}).get(key, "")
        sha, size, version = "", 0, ""
    if not url:
        raise RuntimeError(f"{name} 在 {platform_key()} 上没有可用的下载源")

    files_dir = tools_dir() / "cache"
    files_dir.mkdir(parents=True, exist_ok=True)
    # 后缀按真实格式给：解压靠内容嗅探，但临时文件名别骗人
    suffix = next((s for s in (".zip", ".tar.xz", ".tar.gz", ".tar.bz2", ".tgz")
                   if url.lower().endswith(s)), ".bin")
    with tempfile.TemporaryDirectory(dir=files_dir) as tmp:
        archive = Path(tmp) / ("download" + suffix)

        def report(done, total):
            pct = (done / total) if total else 0.0
            _set_state(name, progress=round(min(pct, 0.99), 3))
            if progress:
                progress(done, total)

        _download(url, archive, report, expected_sha256=sha, total_hint=size)
        path = _install_ytdlp(archive) if name == "ytdlp" else _install_ffmpeg(archive)

    _set_state(name, status="ok", path=str(path), progress=1.0,
               version=version, error="")
    return path


def _worker(name: str, base: str) -> None:
    try:
        ensure(name, base)
    except Exception as exc:  # noqa: BLE001 - 下载失败不该打断任何东西
        _set_state(name, status="error", progress=0.0, error=str(exc))


def ensure_in_background(name: str, base: str = "") -> threading.Thread | None:
    """后台装。已经在装就不重复起线程。"""
    state = _state(name)
    if state["status"] == "downloading":
        return None
    thread = threading.Thread(target=_worker, args=(name, base), daemon=True,
                              name=f"toolchain-{name}")
    thread.start()
    return thread


def autostart(cfg) -> None:
    """启动时补缺：本机没有的才下载，有的话不碰。"""
    if os.environ.get("OCEMIND_NO_TOOL_DOWNLOAD"):
        return
    if not cfg.get("autoDownloadTools"):
        return
    base = mirror_base(cfg.get("toolsMirror") or "")
    explicit = {"ffmpeg": cfg.get("ffmpegPath") or "",
                "ytdlp": cfg.get("ytdlpPath") or ""}
    for name in ("ffmpeg", "ytdlp"):
        found = available(name, explicit.get(name, ""))
        if found:
            _set_state(name, status="ok", path=str(found))
            continue
        _set_state(name, status="missing", path="")
        ensure_in_background(name, base)


def run_ytdlp(argv: list[str]) -> int:
    """在 sidecar 自己的解释器里跑 yt-dlp 的 CLI（zipapp 放 sys.path）。"""
    managed = managed_path("ytdlp")
    if not managed:
        print("yt-dlp 还没安装", file=sys.stderr)
        return 127
    sys.path.insert(0, str(managed))
    sys.argv = ["yt-dlp"] + list(argv)
    try:
        import yt_dlp
    except ImportError as exc:  # 损坏的包
        print(f"yt-dlp 加载失败：{exc}", file=sys.stderr)
        return 127
    try:
        return int(yt_dlp.main() or 0)
    except SystemExit as exc:
        return int(exc.code or 0)


def ytdlp_command() -> list[str]:
    """给下载引擎用的命令前缀。

    打包后没有独立 python 可执行文件，`-m yt_dlp` 会被 sidecar 当成它自己的
    命令行参数吃掉（结果跑成 `ocemind.cli`）。所以冻结环境一律走
    `--run-ytdlp`：装了 zipapp 就正常跑，没装就立刻报「还没安装」，
    不会挂 20 秒。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-ytdlp"]
    managed = managed_path("ytdlp")
    if managed:
        return [sys.executable, str(Path(paths.project_root()) / "run.py"), "--run-ytdlp"]
    if shutil.which("yt-dlp"):
        return [shutil.which("yt-dlp")]
    return [sys.executable, "-m", "yt_dlp"]


def status_snapshot() -> dict:
    out = {}
    with _lock:
        for name in ("ffmpeg", "ytdlp"):
            st = STATE.get(name)
            if st is None:
                found = available(name)
                st = {"status": "ok" if found else "missing", "path": str(found or ""),
                      "progress": 1.0 if found else 0.0, "version": "", "error": ""}
            out[name] = dict(st)
    return out
