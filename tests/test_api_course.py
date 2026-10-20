# -*- coding: utf-8 -*-
"""Web 接口里的课程部分。

直接调路由函数，不起 HTTP 服务（starlette 的 TestClient 还要额外装 httpx2，
为一个冒烟测试不值得）。重点：探测课程**不能联网**，导入 cookie 要落到
规范位置，登录出错要给出人话。
"""
import pytest
from fastapi import HTTPException

from xiaoe_dl import auth, paths
from xiaoe_dl.downloader import api as api_mod
from xiaoe_dl.downloader.config import Config
from xiaoe_dl.downloader.manager import TaskManager

URL = "https://app1.h5.xiaoeknow.com/p/course/video/l_abcdef1234567890abcd"
COOKIE_TEXT = "\n".join([
    "# Netscape HTTP Cookie File",
    ".xiaoeknow.com\tTRUE\t/\tFALSE\t4102444800\tko_token\tabc123",
]) + "\n"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOE_DL_HOME", str(tmp_path / "appdata"))
    cfg = Config(home=tmp_path / "home", legacy_home=tmp_path / "none")
    mgr = TaskManager(cfg, start_scheduler=False)
    yield api_mod.create_app(cfg, mgr)
    mgr.shutdown()


def endpoint(app, path, method="POST"):
    for route in app.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"没有路由 {method} {path}")


def test_probe_course_never_hits_network(app, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("课程探测不能联网")

    monkeypatch.setattr(api_mod, "probe_video", boom)
    monkeypatch.setattr(api_mod, "probe_http", boom)

    info = endpoint(app, "/api/probe")(api_mod.ProbeBody(url=URL))

    assert info["ok"] is True
    assert info["kind"] == "course"
    assert info["app_id"] == "app1"
    assert info["alive_id"] == "l_abcdef1234567890abcd"
    assert info["needs_cookie"] is True


def test_probe_course_sees_valid_cookie(app, tmp_path):
    endpoint(app, "/api/course/cookies/import")(
        api_mod.CookieImportBody(text=COOKIE_TEXT))

    info = endpoint(app, "/api/probe")(api_mod.ProbeBody(url=URL))

    assert info["needs_cookie"] is False


def test_import_writes_canonical_location(app, tmp_path):
    result = endpoint(app, "/api/course/cookies/import")(
        api_mod.CookieImportBody(text=COOKIE_TEXT))

    dest = paths.app_dir() + "/cookies.txt"
    assert result["imported"] == 1
    assert result["has_file"] is True
    assert result["path"].endswith("cookies.txt")
    with open(dest, encoding="utf-8") as f:
        assert "ko_token" in f.read()


def test_import_from_path(app, tmp_path):
    src = tmp_path / "exported.txt"
    src.write_text(COOKIE_TEXT, encoding="utf-8")

    result = endpoint(app, "/api/course/cookies/import")(
        api_mod.CookieImportBody(path=str(src)))

    assert result["imported"] == 1


def test_import_rejects_garbage(app):
    with pytest.raises(HTTPException) as err:
        endpoint(app, "/api/course/cookies/import")(
            api_mod.CookieImportBody(text="这不是 cookie 文件"))
    assert err.value.status_code == 400


def test_login_requires_app_id(app):
    with pytest.raises(HTTPException) as err:
        endpoint(app, "/api/course/login")(api_mod.LoginBody())
    assert err.value.status_code == 400


def test_login_without_playwright_is_explained(app, monkeypatch):
    monkeypatch.setattr(auth, "playwright_available", lambda: False)
    with pytest.raises(HTTPException) as err:
        endpoint(app, "/api/course/login")(
            api_mod.LoginBody(app_id="app1", alive_id="l_x"))
    assert err.value.status_code == 409
    assert "playwright" in err.value.detail


def test_login_status_starts_idle(app):
    status = endpoint(app, "/api/course/login/status", "GET")()
    assert status["state"] == "idle"
    assert status["playwright"] in (True, False)


def test_course_task_can_be_added_through_api(app, monkeypatch):
    monkeypatch.setattr(auth, "playwright_available", lambda: False)
    added = endpoint(app, "/api/tasks")(
        api_mod.AddTaskBody(url=URL, kind="course", dir="D:/tmp"))
    assert added["task"]["kind"] == "course"
