# -*- coding: utf-8 -*-
"""账号级代理。

写错的代理不会报错，只会安静地直连出去——账号就在没代理的情况下登录了。
所以这里盯三件事：写错必须拒、归一化要准、真的进了 Chrome 启动参数。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager
from ocemind.executor.accounts.cdp import ChromeProcess
from ocemind.executor.accounts.proxy import display, normalize
from ocemind.executor.facade import Executor
from ocemind.executor.store import Store


# ------------------------------------------------------------ 地址归一化

@pytest.mark.parametrize("raw,want", [
    ("127.0.0.1:7890", "http://127.0.0.1:7890"),
    (" 127.0.0.1:7890 ", "http://127.0.0.1:7890"),
    ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
    ("HTTP://127.0.0.1:7890/", "http://127.0.0.1:7890"),
    ("socks5://127.0.0.1:10808", "socks5://127.0.0.1:10808"),
    ("socks5h://127.0.0.1:10808", "socks5://127.0.0.1:10808"),
    ("https://proxy.example.com:8443", "https://proxy.example.com:8443"),
    ("", ""),
    ("   ", ""),
])
def test_normalize_accepts(raw, want):
    assert normalize(raw) == want


@pytest.mark.parametrize("raw,hint", [
    ("127.0.0.1", "缺少端口"),
    ("socks5://", "缺少主机名"),
    ("ftp://127.0.0.1:21", "只支持"),
    ("http://user:pass@127.0.0.1:7890", "账号密码"),
    ("http://127.0.0.1:99999", "端口"),
    ("http://127.0.0.1:abc", "端口"),
    ("http://127.0.0.1:7890/path", "不要带路径"),
])
def test_normalize_rejects_with_reason(raw, hint):
    with pytest.raises(ValueError) as exc:
        normalize(raw)
    assert hint in str(exc.value)


def test_display_is_short():
    assert display("") == "直连"
    assert display("http://127.0.0.1:7890") == "127.0.0.1:7890"
    assert display("socks5://127.0.0.1:10808") == "socks5://127.0.0.1:10808"


# ------------------------------------------------------- Chrome 启动参数

def _chrome(proxy: str = "") -> ChromeProcess:
    proc = ChromeProcess(chrome_path="chrome.exe", profile_dir="p", proxy=proxy)
    proc.port = 9222
    return proc


def test_proxy_reaches_chrome_args():
    args = _chrome("socks5://127.0.0.1:10808").args("https://www.bilibili.com")
    assert "--proxy-server=socks5://127.0.0.1:10808" in args
    assert args[-1] == "https://www.bilibili.com"


def test_no_proxy_no_flag():
    args = _chrome().args()
    assert not [a for a in args if a.startswith("--proxy-server")]


def test_user_data_dir_still_isolated_with_proxy():
    """代理不能顶掉账号隔离——两个参数得同时在场。"""
    args = _chrome("http://127.0.0.1:7890").args()
    assert any(a.startswith("--user-data-dir=") for a in args)
    assert any(a.startswith("--proxy-server=") for a in args)


# ------------------------------------------------------------- 老库迁移

def test_old_database_gets_proxy_column(tmp_path: Path):
    """2.5.0 之前的库没有 proxy 列，打开时要自动补上，不能报错。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE accounts (
            id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL,
            profile_dir TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            last_ok_at REAL, last_check_at REAL, cookie_expiry REAL,
            status TEXT NOT NULL DEFAULT 'never', note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL);
        INSERT INTO accounts (id, platform, label, profile_dir, created_at)
        VALUES ('a1', 'bilibili', '主号', '/tmp/p', 1.0);
    """)
    conn.commit()
    conn.close()

    store = Store(path)
    acc = store.get_account("a1")
    assert acc["label"] == "主号"
    assert acc["proxy"] == ""


# --------------------------------------------------------------- 门面行为

@pytest.fixture()
def executor(tmp_path: Path):
    cfg = Config(tmp_path / "home")
    manager = TaskManager(cfg, start_scheduler=False)
    ex = Executor(cfg, manager)
    try:
        yield ex
    finally:
        ex.close()
        manager.shutdown()


def test_set_account_proxy_roundtrip(executor):
    acc = executor.add_account("bilibili", "主号")
    got = executor.set_account_proxy(acc["id"], "127.0.0.1:7890")
    assert got["changed"] is True
    assert got["display"] == "127.0.0.1:7890"

    listed = {a["id"]: a for a in executor.accounts()}[acc["id"]]
    assert listed["proxy"] == "http://127.0.0.1:7890"
    assert listed["proxy_display"] == "127.0.0.1:7890"

    # 再存一遍同样的值不要装作改了
    again = executor.set_account_proxy(acc["id"], "http://127.0.0.1:7890")
    assert again["changed"] is False

    cleared = executor.set_account_proxy(acc["id"], "")
    assert cleared["proxy"] == ""
    assert executor.store.get_account(acc["id"])["proxy"] == ""


def test_set_account_proxy_rejects_garbage(executor):
    acc = executor.add_account("douyin", "小号")
    with pytest.raises(ValueError):
        executor.set_account_proxy(acc["id"], "不是代理")
    # 拒绝的时候不能把原来的值改掉
    assert executor.store.get_account(acc["id"])["proxy"] == ""


def test_set_account_proxy_unknown_account(executor):
    with pytest.raises(ValueError):
        executor.set_account_proxy("nope", "127.0.0.1:7890")


def test_new_account_has_empty_proxy(executor):
    acc = executor.add_account("kuaishou", "")
    assert acc["proxy"] == ""
