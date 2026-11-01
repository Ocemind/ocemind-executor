# -*- coding: utf-8 -*-
"""toolchain —— 外部工具（ffmpeg / yt-dlp）探测与按需下载。

这一层的失败方式都很安静：找不到就退回 PATH、下载坏了就报个错，
真正致命的是「装了个来路不明的文件还当成 ffmpeg 用」和「镜像里
校验值对不上也照装」。所以测试重点在：挑对包、校验、路径安全。
"""
import hashlib
import http.server
import io
import json
import os
import threading
import zipfile
from pathlib import Path

import pytest

from ocemind import toolchain


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把数据目录挪到临时目录，顺带清掉进程级的下载状态。"""
    monkeypatch.setenv("OCEMIND_HOME", str(tmp_path))
    monkeypatch.delenv("OCEMIND_TOOLS_MIRROR", raising=False)
    monkeypatch.setattr(toolchain, "STATE", {}, raising=False)
    return tmp_path


@pytest.fixture
def serve(tmp_path):
    """起一个本地 HTTP 服务，返回 (根地址, 放文件的目录)。"""
    root = tmp_path / "site"
    root.mkdir()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", root
    server.shutdown()


def _zipapp_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 平台与清单
def test_platform_key_shape():
    key = toolchain.platform_key()
    system, arch = key.split("-", 1)
    assert system in ("windows", "linux", "darwin")
    assert arch


def test_entry_for_prefers_platform_then_any():
    manifest = {"tools": {
        "ffmpeg": {"windows-x86_64": {"url": "ffmpeg/win.zip"},
                   "any": {"url": "ffmpeg/any.zip"}},
        "ytdlp": {"any": {"url": "ytdlp/yt-dlp.zip"}},
    }}
    monkeypatch_key = toolchain.platform_key()
    entry = toolchain._entry_for(manifest, "ffmpeg")
    assert entry["url"] in ("ffmpeg/win.zip", "ffmpeg/any.zip")
    if monkeypatch_key == "windows-x86_64":
        assert entry["url"] == "ffmpeg/win.zip"
    # yt-dlp 不分平台，一律 any
    assert toolchain._entry_for(manifest, "ytdlp")["url"] == "ytdlp/yt-dlp.zip"


def test_entry_for_survives_garbage():
    for manifest in ({}, {"tools": []}, {"tools": {"ffmpeg": "nope"}}, None):
        assert toolchain._entry_for(manifest, "ffmpeg") == {}


def test_mirror_base_precedence(monkeypatch):
    monkeypatch.setenv("OCEMIND_TOOLS_MIRROR", "https://env.example/tools/")
    assert toolchain.mirror_base("https://setting.example/t") == "https://setting.example/t"
    assert toolchain.mirror_base("") == "https://env.example/tools"
    monkeypatch.delenv("OCEMIND_TOOLS_MIRROR")
    assert toolchain.mirror_base("") == ""


# ---------------------------------------------------------------- 本机探测
def test_system_path_finds_exe_on_path(home, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    exe.write_bytes(b"")
    monkeypatch.setenv("PATH", str(bindir), prepend=os.pathsep)
    assert toolchain.system_path("ffmpeg") == exe


def test_managed_path_wins_over_system(home, monkeypatch):
    monkeypatch.setattr(toolchain, "system_path", lambda name: Path("/usr/bin/ffmpeg"))
    exe_name = toolchain.EXE_NAMES["ffmpeg"].get(__import__("platform").system().lower())
    managed = toolchain.tools_dir() / "ffmpeg" / "bin" / exe_name
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"")
    assert toolchain.managed_path("ffmpeg") == managed
    assert toolchain.available("ffmpeg") == managed


def test_status_snapshot_reports_missing(home, monkeypatch):
    monkeypatch.setattr(toolchain, "available", lambda name, explicit="": None)
    snap = toolchain.status_snapshot()
    assert set(snap) == {"ffmpeg", "ytdlp"}
    assert all(v["status"] == "missing" for v in snap.values())


# ---------------------------------------------------------------- 下载与校验
def test_download_verifies_sha256(home, serve, tmp_path):
    base, root = serve
    data = b"payload" * 1000
    (root / "f.bin").write_bytes(data)
    dest = tmp_path / "out.bin"
    toolchain._download(f"{base}/f.bin", dest, expected_sha256=_sha(data))
    assert dest.read_bytes() == data
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_rejects_bad_sha256(home, serve, tmp_path):
    base, root = serve
    (root / "f.bin").write_bytes(b"tampered")
    dest = tmp_path / "out.bin"
    with pytest.raises(RuntimeError, match="SHA-256"):
        toolchain._download(f"{base}/f.bin", dest, expected_sha256="0" * 64)
    assert not dest.exists()


def test_download_retries_transient_failure(home, tmp_path, monkeypatch):
    """GitHub 在国内常半路断 TLS，同一地址重试一次往往就好。"""
    calls = []

    def flaky(url, dest, progress=None, expected_sha256="", total_hint=0):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("UNEXPECTED_EOF_WHILE_READING")
        dest.write_bytes(b"ok")

    monkeypatch.setattr(toolchain, "_download_once", flaky)
    monkeypatch.setattr(toolchain.time, "sleep", lambda s: None)
    dest = tmp_path / "out.bin"
    toolchain._download("https://example/yt-dlp", dest)
    assert dest.read_bytes() == b"ok"
    assert len(calls) == 3


def test_download_gives_up_after_attempts(home, tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "_download_once",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(toolchain.time, "sleep", lambda s: None)
    with pytest.raises(OSError):
        toolchain._download("https://example/x", tmp_path / "out.bin", attempts=2)


def test_download_does_not_retry_sha_mismatch(home, tmp_path, monkeypatch):
    calls = []

    def bad(url, dest, progress=None, expected_sha256="", total_hint=0):
        calls.append(1)
        raise RuntimeError("SHA-256 校验失败")

    monkeypatch.setattr(toolchain, "_download_once", bad)
    with pytest.raises(RuntimeError):
        toolchain._download("https://example/x", tmp_path / "o.bin")
    assert len(calls) == 1


def test_download_reports_progress(home, serve, tmp_path):
    base, root = serve
    (root / "f.bin").write_bytes(b"x" * 5000)
    seen = []
    toolchain._download(f"{base}/f.bin", tmp_path / "o.bin",
                        progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1][0] == 5000


# ---------------------------------------------------------------- 路径安全
def test_extract_skips_path_escape(home, tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "nope")
        zf.writestr("ok/keep.txt", "yes")
    target = tmp_path / "target"
    toolchain._extract(archive, target)
    assert not (tmp_path / "escaped.txt").exists()
    assert (target / "ok" / "keep.txt").read_text() == "yes"


def test_extract_rejects_unknown_format(home, tmp_path):
    archive = tmp_path / "weird.bin"
    archive.write_bytes(b"not an archive at all")
    with pytest.raises(RuntimeError):
        toolchain._extract(archive, tmp_path / "t")


# ---------------------------------------------------------------- 安装
def test_install_ffmpeg_finds_exe_in_nested_dir(home, tmp_path):
    """BtbN 的包里可执行文件在 <顶层目录>/bin/ 下面，不能只看根目录。"""
    import platform
    exe_name = toolchain.EXE_NAMES["ffmpeg"].get(platform.system().lower(), "ffmpeg")
    archive = tmp_path / "ff.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"ffmpeg-7.0/bin/{exe_name}", b"MZ")
        zf.writestr("ffmpeg-7.0/LICENSE", "LGPL")
    exe = toolchain._install_ffmpeg(archive)
    assert exe.is_file()
    assert toolchain.managed_path("ffmpeg") == exe


def test_install_ffmpeg_drops_ffplay(home, tmp_path):
    """官方包里的 ffplay 有一百多 MB，装进来纯占地方。"""
    import platform
    exe_name = toolchain.EXE_NAMES["ffmpeg"].get(platform.system().lower(), "ffmpeg")
    play = "ffplay.exe" if os.name == "nt" else "ffplay"
    probe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    archive = tmp_path / "ff.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"d/bin/{exe_name}", b"MZ")
        zf.writestr(f"d/bin/{play}", b"MZ")
        zf.writestr(f"d/bin/{probe}", b"MZ")
    exe = toolchain._install_ffmpeg(archive)
    assert exe.name == exe_name
    assert (exe.parent / probe).exists()
    assert not (exe.parent / play).exists()


def test_install_ffmpeg_rejects_archive_without_exe(home, tmp_path):
    archive = tmp_path / "ff.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    with pytest.raises(RuntimeError, match="ffmpeg"):
        toolchain._install_ffmpeg(archive)


def test_install_ytdlp_keeps_zipapp(home, tmp_path):
    archive = tmp_path / "yt-dlp"
    archive.write_bytes(b"PK..")
    dest = toolchain._install_ytdlp(archive)
    assert dest.name == "yt-dlp.zip" and dest.read_bytes() == b"PK.."
    assert toolchain.managed_path("ytdlp") == dest


# ---------------------------------------------------------------- 端到端
def test_ensure_installs_ytdlp_from_mirror(home, serve):
    base, root = serve
    payload = _zipapp_bytes({"yt_dlp/__init__.py": "def main():\n    return 0\n"})
    (root / "ytdlp" / "any").mkdir(parents=True)
    (root / "ytdlp" / "any" / "yt-dlp.zip").write_bytes(payload)
    (root / "index.json").write_text(json.dumps({"tools": {"ytdlp": {"any": {
        "url": "ytdlp/any/yt-dlp.zip", "sha256": _sha(payload),
        "size": len(payload), "version": "2026.10.26"}}}}), encoding="utf-8")

    path = toolchain.ensure("ytdlp", base)
    assert path == toolchain.tools_dir() / "ytdlp" / "yt-dlp.zip"
    assert path.read_bytes() == payload
    st = toolchain.status_snapshot()["ytdlp"]
    assert st["status"] == "ok" and st["version"] == "2026.10.26"


def test_ensure_falls_back_to_github_without_mirror(home, monkeypatch):
    """没配镜像时用上游地址；这里只验证挑对了地址，不真的下 100 MB。"""
    captured = {}

    def fake_download(url, dest, progress=None, expected_sha256="", total_hint=0):
        captured["url"] = url
        raise RuntimeError("stop here")

    monkeypatch.setattr(toolchain, "_download", fake_download)
    with pytest.raises(RuntimeError):
        toolchain.ensure("ytdlp", "")
    assert captured["url"].startswith("https://github.com/yt-dlp/yt-dlp/")


def test_ensure_skips_download_when_managed(home, monkeypatch):
    managed = toolchain.tools_dir() / "ytdlp" / "yt-dlp.zip"
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"zip")
    monkeypatch.setattr(toolchain, "_download",
                        lambda *a, **k: pytest.fail("不该下载"))
    assert toolchain.ensure("ytdlp") == managed


def test_ensure_rejects_sha_mismatch_from_mirror(home, serve):
    base, root = serve
    (root / "ytdlp" / "any").mkdir(parents=True)
    (root / "ytdlp" / "any" / "yt-dlp.zip").write_bytes(b"good")
    (root / "index.json").write_text(json.dumps({"tools": {"ytdlp": {"any": {
        "url": "ytdlp/any/yt-dlp.zip", "sha256": "f" * 64}}}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="SHA-256"):
        toolchain.ensure("ytdlp", base)
    assert toolchain.managed_path("ytdlp") is None


def test_ensure_marks_error_state_on_direct_failure(home, monkeypatch):
    """直接调用失败时状态必须落到 error，否则界面永远停在「下载中」。"""
    monkeypatch.setattr(toolchain, "_ensure",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        toolchain.ensure("ffmpeg")
    assert toolchain.status_snapshot()["ffmpeg"]["status"] == "error"


def test_ensure_in_background_retries_after_error(home, monkeypatch):
    """上一次失败之后点重试，得能真的再起一个线程。"""
    toolchain._set_state("ffmpeg", status="error", error="上一次失败")
    monkeypatch.setattr(toolchain, "ensure", lambda name, base="": Path("/x"))
    thread = toolchain.ensure_in_background("ffmpeg", "")
    assert thread is not None


def test_ensure_in_background_records_error(home, monkeypatch):
    monkeypatch.setattr(toolchain, "ensure",
                        lambda name, base="": (_ for _ in ()).throw(RuntimeError("boom")))
    thread = toolchain.ensure_in_background("ffmpeg", "")
    thread.join(5)
    st = toolchain.status_snapshot()["ffmpeg"]
    assert st["status"] == "error" and "boom" in st["error"]


def test_ensure_in_background_no_double_start(home, monkeypatch):
    toolchain._set_state("ffmpeg", status="downloading")
    started = []
    monkeypatch.setattr(toolchain, "ensure", lambda *a: started.append(1))
    assert toolchain.ensure_in_background("ffmpeg", "") is None
    assert started == []


def test_autostart_skips_existing_tools(home, monkeypatch):
    class Cfg:
        def get(self, key, default=None):
            return {"autoDownloadTools": True, "toolsMirror": ""}.get(key, default)

    started = []
    monkeypatch.setattr(toolchain, "available", lambda name, explicit="": Path("/usr/bin") / name)
    monkeypatch.setattr(toolchain, "ensure_in_background",
                        lambda name, base="": started.append(name) or _Thread())
    toolchain.autostart(Cfg())
    assert started == []


class _Thread:
    def start(self):
        pass


def test_autostart_honours_explicit_paths(home, monkeypatch, tmp_path):
    """设置里填了路径还去下一份，等于白占一百多 MB。"""
    given = tmp_path / "ffmpeg.exe"
    given.write_bytes(b"")
    given_yt = tmp_path / "yt-dlp.exe"
    given_yt.write_bytes(b"")
    monkeypatch.setattr(toolchain, "ensure_in_background",
                        lambda name, base="": pytest.fail("不该下载"))

    class Cfg:
        def get(self, key, default=None):
            return {"autoDownloadTools": True, "ffmpegPath": str(given),
                    "ytdlpPath": str(given_yt)}.get(key, default)

    toolchain.autostart(Cfg())
    assert toolchain.status_snapshot()["ffmpeg"]["path"] == str(given)


def test_autostart_respects_switch(home, monkeypatch):
    monkeypatch.setattr(toolchain, "available", lambda name, explicit="": None)
    monkeypatch.setattr(toolchain, "ensure_in_background",
                        lambda name, base="": pytest.fail("不该下载"))

    class Off:
        def get(self, key, default=None):
            return False if key == "autoDownloadTools" else default

    toolchain.autostart(Off())


# ---------------------------------------------------------------- 接口
@pytest.fixture
def api_app(tmp_path, monkeypatch):
    from ocemind.downloader import api as api_mod
    from ocemind.downloader.config import Config
    from ocemind.downloader.manager import TaskManager

    monkeypatch.setenv("OCEMIND_HOME", str(tmp_path / "appdata"))
    cfg = Config(home=tmp_path / "home")
    mgr = TaskManager(cfg, start_scheduler=False)
    yield api_mod.create_app(cfg, mgr)
    mgr.shutdown()


def _endpoint(app, path, method="GET"):
    for route in app.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"没有路由 {method} {path}")


def test_status_endpoint_is_light(api_app, monkeypatch):
    """轮询接口不能去跑工具子进程，否则下载时每两秒拖两个进程出来。"""
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("不该跑子进程"))
    monkeypatch.setattr(toolchain, "available", lambda name, explicit="": None)
    data = _endpoint(api_app, "/api/tools/status")()
    assert data["toolchain"]["ffmpeg"]["status"] == "missing"


def test_ensure_endpoint_starts_only_missing(api_app, monkeypatch):
    started = []
    monkeypatch.setattr(toolchain, "available",
                        lambda name, explicit="": None if name == "ffmpeg" else Path("/x/yt-dlp"))
    monkeypatch.setattr(toolchain, "ensure_in_background",
                        lambda name, base="": started.append(name) or _Thread())
    data = _endpoint(api_app, "/api/tools/ensure", "POST")()
    assert data["started"] == ["ffmpeg"]
    assert started == ["ffmpeg"]


def test_autostart_respects_env_kill_switch(home, monkeypatch):
    monkeypatch.setenv("OCEMIND_NO_TOOL_DOWNLOAD", "1")
    monkeypatch.setattr(toolchain, "ensure_in_background",
                        lambda name, base="": pytest.fail("不该下载"))

    class On:
        def get(self, key, default=None):
            return True if key == "autoDownloadTools" else default

    toolchain.autostart(On())
