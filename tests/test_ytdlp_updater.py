# -*- coding: utf-8 -*-
"""yt-dlp 自更新：版本比较、挑 wheel、下载安装、回退、导入优先级。

不联网：PyPI 那边用一个本地 HTTP 服务假装，下发的 wheel 也是现造的。
"""
import hashlib
import http.server
import threading
import zipfile
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocemind import paths, ytdlp_updater as yu


class _Cfg:
    def proxies(self):
        return None


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把数据目录挪到临时目录，别动本机真正装的那份。"""
    monkeypatch.setenv("OCEMIND_LOCAL_HOME", str(tmp_path / "local"))
    yield tmp_path
    # 装进去的包要在用例结束后清掉，别影响同进程里别的用例
    yu.remove_external()
    yu._finder_installed = False


def make_wheel(path: Path, version: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("yt_dlp/__init__.py", "")
        zf.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
    return path


WHEEL = "yt_dlp-9.9.9-py3-none-any.whl"


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    make_wheel(root / WHEEL, "9.9.9")
    with zipfile.ZipFile(root / "bad-9.9.9-py3-none-any.whl", "w") as zf:
        zf.writestr("nope/x.py", "")

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def url(name: str = WHEEL) -> str:
        return f"http://127.0.0.1:{port}/{name}"

    yield SimpleNamespace(url=url, root=root)
    httpd.shutdown()


# ---------------------------------------------------------------- 版本

@pytest.mark.parametrize("latest,current,want", [
    ("2026.10.26", "2026.08.19", True),
    ("2026.08.19", "2026.10.26", False),
    ("2026.10.26", "2026.10.26", False),
    ("2026.9.1", "2026.10.1", False),
    ("", "2026.10.26", False),
    ("2026.10.26", "", False),
])
def test_is_newer(latest, current, want):
    assert yu.is_newer(latest, current) is want


def test_pick_wheel_prefers_universal():
    files = [
        {"filename": "yt_dlp-1.0-cp313-none-win_amd64.whl", "packagetype": "bdist_wheel"},
        {"filename": "yt_dlp-1.0-py3-none-any.whl", "packagetype": "bdist_wheel"},
        {"filename": "yt_dlp-1.0.tar.gz", "packagetype": "sdist"},
    ]
    assert yu._pick_wheel(files)["filename"].endswith("py3-none-any.whl")


# ---------------------------------------------------------------- 安装

def test_install_and_revert(home, server):
    assert yu.has_external() is False
    assert yu.installed_version() == ""

    digest = hashlib.sha256((server.root / WHEEL).read_bytes()).hexdigest()
    out = yu.install(_Cfg(), server.url(), digest)

    assert out["ok"], out
    assert out["version"] == "9.9.9"
    assert yu.has_external()
    assert yu.installed_version() == "9.9.9"
    assert (yu.external_package() / "__init__.py").is_file()
    # 临时下载目录不留垃圾
    assert [p.name for p in yu.package_root().iterdir()] == ["yt_dlp"]

    assert yu.remove_external() is True
    assert yu.has_external() is False


def test_install_refuses_bad_checksum(home, server):
    out = yu.install(_Cfg(), server.url(), "0" * 64)
    assert out["ok"] is False
    assert "校验" in out["error"]
    assert yu.has_external() is False


def test_install_rejects_non_package(home, server):
    out = yu.install(_Cfg(), server.url("bad-9.9.9-py3-none-any.whl"))
    assert out["ok"] is False
    assert "yt_dlp" in out["error"]
    assert yu.has_external() is False


# ---------------------------------------------------------------- 生效方式

def test_activate_puts_external_first(home, server):
    yu.install(_Cfg(), server.url())
    assert yu.activate() is True

    finder = yu._ExternalFinder(Path(yu.package_dir()))
    spec = finder.find_spec("yt_dlp", None, None)
    assert spec is not None
    assert str(spec.origin).startswith(str(yu.package_dir())), spec.origin
    # 别的模块不该被这个 finder 接管
    assert finder.find_spec("json", None, None) is None


def test_apply_env_prepends_pythonpath(home, server):
    env = yu.apply_env({"PYTHONPATH": "C:/old"})
    assert env["PYTHONPATH"] == "C:/old"       # 没装时原样返回

    yu.install(_Cfg(), server.url())
    env = yu.apply_env({"PYTHONPATH": "C:/old"})
    assert env["PYTHONPATH"].startswith(str(yu.package_dir()))
    assert env["PYTHONPATH"].endswith("C:/old")


def test_effective_reports_source(home, server):
    assert yu.effective()["source"] in ("bundled", "")   # 测试环境里编没编进包都行
    yu.install(_Cfg(), server.url())
    got = yu.effective()
    assert got["source"] == "external"
    assert got["version"] == "9.9.9"


def test_local_dir_follows_env(home):
    assert str(yu.package_root()).startswith(str(paths.local_app_dir()))
