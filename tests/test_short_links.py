# -*- coding: utf-8 -*-
"""短链还原：域名表、跟跳转、加任务时记下原始短链。

域名表是从 CR TubeGet 的配置里直接搬的，这里只抽几条验一下还在，
剩下靠「数据目录可覆盖」兜底——站点加了新短链改 JSON 就行。
"""
import http.server
import threading

import pytest

from ocemind import links
from ocemind.downloader import config as config_mod
from ocemind.downloader import manager as manager_mod
from ocemind.downloader.manager import TaskManager


def test_domains_cover_common_share_links():
    for host in ("v.douyin.com", "b23.tv", "xhslink.com", "t.cn", "vt.tiktok.com"):
        assert links.is_short(f"https://{host}/abc")
    assert not links.is_short("https://www.bilibili.com/video/BV1xx411c7mD")
    assert not links.is_short("")


def test_expand_returns_empty_for_normal_url():
    assert links.expand("https://www.bilibili.com/video/BV1xx411c7mD") == ""


@pytest.fixture
def redirect_server():
    """一个把 /short 跳到 /final 的本地服务。"""
    state = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            if self.path.startswith("/short"):
                self.send_response(302)
                self.send_header("Location", state["final"])
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()

        do_GET = do_HEAD

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    state["final"] = f"http://127.0.0.1:{port}/final"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/short"
    server.shutdown()


def test_expand_follows_redirect(monkeypatch, redirect_server):
    monkeypatch.setattr(links, "short_domains", lambda: {"127.0.0.1"})
    assert links.is_short(redirect_server)
    assert links.expand(redirect_server).endswith("/final")


def test_expand_does_not_cache_failures(monkeypatch):
    monkeypatch.setattr(links, "short_domains", lambda: {"127.0.0.1"})
    assert links.expand("http://127.0.0.1:9/nothing", timeout=0.3) == ""


def _manager(tmp_path):
    cfg = config_mod.Config(home=tmp_path / "home")
    return TaskManager(cfg, start_scheduler=False)


def test_add_records_original_short_link(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod, "is_short", lambda url: True)
    monkeypatch.setattr(manager_mod, "expand",
                        lambda url: "https://www.bilibili.com/video/BV1xx411c7mD")
    mgr = _manager(tmp_path)
    try:
        task = mgr.add("https://b23.tv/abc", "auto", 0, str(tmp_path), 8)
        assert task["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert task["url_original"] == "https://b23.tv/abc"
    finally:
        mgr.shutdown()


def test_add_keeps_short_link_when_expand_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod, "is_short", lambda url: True)
    monkeypatch.setattr(manager_mod, "expand", lambda url: "")
    mgr = _manager(tmp_path)
    try:
        task = mgr.add("https://b23.tv/abc", "auto", 0, str(tmp_path), 8)
        assert task["url"] == "https://b23.tv/abc"
        assert task["url_original"] == ""
    finally:
        mgr.shutdown()
