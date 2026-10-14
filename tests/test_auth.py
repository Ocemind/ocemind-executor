# -*- coding: utf-8 -*-
"""登录态双模式。

这里的测试**全部离线**：真的开浏览器要联网、要人操作，不适合放进默认测试集。
浏览器那部分的正确性靠 `tools/diagnose.py` 和手动跑 `--login` 验证。

重点测的是纯逻辑：cookie 双向转换、落盘的原子性、以及 get_cookie 那棵决策树
——决策树最容易出的事是「该走快路径的时候弹了浏览器」和「没凭证时静默返回
一份过期的」，这两条都钉住了。
"""
import os
import time as _time

import pytest

from helpers import APP_ID, DAY, NOW
from xiaoe_dl import auth
from xiaoe_dl.auth import AuthError, Cookie, from_browser, save_cookies, to_netscape
from xiaoe_dl.cookies import find_ko_token, parse_netscape


def _long_ago():
    """一个**相对真实时钟**的过去时间戳。

    不能写成 `NOW - DAY`：helpers.NOW 是 2027-01 的固定点（给确定性断言用），
    而 get_cookie 内部是按**真实时钟**判断过期的——用 NOW 减一天算出来反而是
    未来，测试会假绿。凡是走 get_cookie 的用例都得用这个。
    """
    return int(_time.time()) - 365 * DAY


# ------------------------------------------------------------ from_browser
def test_from_browser_maps_fields():
    raw = [{"domain": f"{APP_ID}.h5.xiaoeknow.com", "path": "/",
            "secure": True, "expires": NOW + DAY,
            "name": "ko_token", "value": "T"}]
    (c,) = from_browser(raw)
    assert c.domain == f"{APP_ID}.h5.xiaoeknow.com"
    assert c.path == "/"
    assert c.secure is True
    assert c.expires == int(NOW + DAY)
    assert c.name == "ko_token"
    assert c.value == "T"


def test_from_browser_normalises_session_cookies():
    """会话级 cookie 的 expires 是 -1，统一归一成 0。

    留着负值会让「会话级」和「已过期」在后续判断里混淆。
    """
    (c,) = from_browser([{"domain": "x.io", "name": "a", "value": "b",
                          "expires": -1}])
    assert c.expires == 0


@pytest.mark.parametrize("bad", [None, "", "abc", "12.5x"])
def test_from_browser_survives_unparsable_expires(bad):
    (c,) = from_browser([{"domain": "x.io", "name": "a", "value": "b",
                          "expires": bad}])
    assert c.expires == 0


def test_from_browser_handles_missing_keys():
    (c,) = from_browser([{}])
    assert c.domain == "" and c.name == "" and c.path == "/"
    assert c.secure is False and c.expires == 0


def test_from_browser_accepts_none():
    assert from_browser(None) == []


# ------------------------------------------------------------- to_netscape
def _ck(domain="x.io", name="a", value="b", expires=0, path="/", secure=False):
    return Cookie(domain=domain, path=path, secure=secure,
                  expires=expires, name=name, value=value)


def test_to_netscape_round_trips():
    """写出去再读回来必须一模一样——这是「登录一次，之后一直能用」的地基。"""
    cks = [
        _ck(f"{APP_ID}.h5.xiaoeknow.com", "ko_token", "TOKEN", int(NOW + DAY),
            secure=True),
        _ck(".xiaoeknow.com", "root_thing", "v", int(NOW + DAY)),
        _ck(f"{APP_ID}.h5.xiaoeknow.com", "session_only", "v2", 0),
    ]
    assert parse_netscape(to_netscape(cks)) == cks


def test_to_netscape_starts_with_header():
    assert to_netscape([]).startswith("# Netscape HTTP Cookie File")


def test_to_netscape_marks_subdomain_flag_from_leading_dot():
    """第二列是「是否含子域」，由域名前面有没有点决定。"""
    out = to_netscape([_ck(".xiaoeknow.com", "a", "b")])
    assert out.splitlines()[1].split("\t")[1] == "TRUE"
    out = to_netscape([_ck("xiaoeknow.com", "a", "b")])
    assert out.splitlines()[1].split("\t")[1] == "FALSE"


def test_to_netscape_clamps_negative_expires_to_zero():
    out = to_netscape([_ck(expires=-1)])
    assert out.splitlines()[1].split("\t")[4] == "0"


@pytest.mark.parametrize("val", ["has\ttab", "has\nnewline", "has\rreturn"])
def test_to_netscape_skips_values_that_would_corrupt_the_file(val):
    """值里混进制表符/换行会把整行结构搞坏，读回来时静默丢数据。
    宁可少写这一条，也不要写出一份格式错乱的文件。"""
    out = to_netscape([_ck(value=val)])
    assert len(out.strip().splitlines()) == 1      # 只剩头那一行


@pytest.mark.parametrize("dom", ["", "   "])
def test_to_netscape_skips_rows_without_domain(dom):
    out = to_netscape([_ck(domain=dom)])
    assert len(out.strip().splitlines()) == 1


def test_to_netscape_keeps_equals_signs_in_value():
    """base64 值里常有 `=`，切分不能截断它。"""
    cks = [_ck(value="abc=def=ghi")]
    assert parse_netscape(to_netscape(cks))[0].value == "abc=def=ghi"


# ------------------------------------------------------------ save_cookies
def test_save_cookies_writes_and_returns_path(tmp_path):
    dest = str(tmp_path / "sub" / "cookies.txt")     # 子目录不存在，要能自建
    path = save_cookies([_ck("x.io", "ko_token", "V")], dest)
    assert path == dest
    assert os.path.exists(dest)
    assert parse_netscape(open(dest, encoding="utf-8").read())[0].value == "V"


def test_save_cookies_leaves_no_temp_file(tmp_path):
    """原子写的证据：不能留下 .tmp 残骸。"""
    dest = str(tmp_path / "cookies.txt")
    save_cookies([_ck()], dest)
    assert os.listdir(tmp_path) == ["cookies.txt"]


def test_save_cookies_overwrites_existing(tmp_path):
    dest = str(tmp_path / "cookies.txt")
    save_cookies([_ck(value="OLD")], dest)
    save_cookies([_ck(value="NEW")], dest)
    assert parse_netscape(open(dest, encoding="utf-8").read())[0].value == "NEW"


def test_saved_file_is_usable_by_find_ko_token(tmp_path):
    """端到端：从浏览器收到的东西落盘之后，主流程真的要能认出它。"""
    dest = str(tmp_path / "cookies.txt")
    cks = [_ck(f"{APP_ID}.h5.xiaoeknow.com", "ko_token", "LIVE", int(NOW + DAY))]
    save_cookies(cks, dest)
    loaded = parse_netscape(open(dest, encoding="utf-8").read())
    assert find_ko_token(loaded, APP_ID, now=NOW).value == "LIVE"


# -------------------------------------------------------- playwright 探测
def test_playwright_available_returns_bool_not_raises():
    """exe 里 playwright 被排除了，这个函数必须安静地返回 False 而不是炸。"""
    assert isinstance(auth.playwright_available(), bool)


# ---------------------------------------------------------- get_cookie 决策
@pytest.fixture
def no_browser(monkeypatch):
    """模拟 exe 环境：没有 playwright。任何一步想开浏览器都会立刻失败。"""
    monkeypatch.setattr(auth, "playwright_available", lambda: False)
    def boom(*a, **k):
        raise AssertionError("不该走到浏览器这一步")
    monkeypatch.setattr(auth, "refresh", boom)
    monkeypatch.setattr(auth, "login_interactive", boom)


def test_fast_path_uses_file_without_touching_browser(make_cookiefile, monkeypatch):
    monkeypatch.setattr(auth, "playwright_available",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("快路径不该探测 playwright")))
    path, cks = auth.get_cookie(app_id=APP_ID, manual_path=make_cookiefile())
    assert path and find_ko_token(cks, APP_ID)


def test_manual_mode_returns_file_even_when_token_expired(make_cookiefile):
    """手动模式下不擅自替用户做主。过期也交出去，让接口去报 11302 更好懂。"""
    from helpers import cookie_line
    p = make_cookiefile([cookie_line(f"{APP_ID}.h5.xiaoeknow.com",
                                     "ko_token", "STALE", _long_ago())])
    path, cks = auth.get_cookie(app_id=APP_ID, mode="manual", manual_path=p)
    assert path == p and len(cks) == 1


def test_manual_mode_without_any_file_raises(monkeypatch):
    # 必须钉住「一份都没有」。不钉的话，开发机上真实存在的 cookie 文件
    # 会让这条测试永远绿，等于什么都没测。
    monkeypatch.setattr(auth, "_first_existing", lambda: None)
    with pytest.raises(AuthError) as e:
        auth.get_cookie(app_id=APP_ID, mode="manual", manual_path=None)
    assert "登录态" in str(e.value)
    assert "cookies.txt" in str(e.value)      # 指引里必须给出落盘位置


def test_auto_mode_falls_back_to_stale_file_with_warning(
        make_cookiefile, no_browser, monkeypatch):
    """没凭证可用、但手上有一份过期的：交出去 + 明确警告，而不是直接崩。"""
    from helpers import cookie_line
    p = make_cookiefile([cookie_line(f"{APP_ID}.h5.xiaoeknow.com",
                                     "ko_token", "STALE", _long_ago())])
    monkeypatch.setattr(auth, "_first_existing", lambda: p)
    seen = []
    path, cks = auth.get_cookie(app_id=APP_ID, on_status=seen.append)
    assert path == p
    assert any("11302" in m for m in seen), "必须说清失败的后果是什么"


def test_auto_mode_raises_when_nothing_is_available(no_browser, monkeypatch):
    monkeypatch.setattr(auth, "_first_existing", lambda: None)
    with pytest.raises(AuthError):
        auth.get_cookie(app_id=APP_ID)


def test_auto_mode_does_not_login_when_interactive_not_allowed(
        no_browser, monkeypatch):
    """exe 场景：playwright 不可用 + 没有文件 → 报错，而不是卡在那里等浏览器。"""
    monkeypatch.setattr(auth, "_first_existing", lambda: None)
    with pytest.raises(AuthError):
        auth.get_cookie(app_id=APP_ID, interactive_ok=False)


def test_interactive_result_is_saved_and_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_first_existing", lambda: None)
    monkeypatch.setattr(auth, "playwright_available", lambda: True)
    monkeypatch.setattr(auth, "refresh", lambda *a, **k: None)   # 续不到
    fresh = [_ck(f"{APP_ID}.h5.xiaoeknow.com", "ko_token", "FRESH", int(NOW + DAY))]
    monkeypatch.setattr(auth, "login_interactive",
                        lambda app_id, **k: fresh)
    dest = str(tmp_path / "cookies.txt")
    monkeypatch.setattr(auth, "save_cookies",
                        lambda cks, d=None: save_cookies(cks, dest))

    path, cks = auth.get_cookie(app_id=APP_ID, interactive_ok=True)
    assert path == dest
    assert find_ko_token(cks, APP_ID, now=NOW).value == "FRESH"


def test_refresh_result_is_preferred_over_asking_user(tmp_path, monkeypatch):
    """能静默续期就别打扰用户——不该弹浏览器。"""
    monkeypatch.setattr(auth, "_first_existing", lambda: None)
    monkeypatch.setattr(auth, "playwright_available", lambda: True)
    refreshed = [_ck(f"{APP_ID}.h5.xiaoeknow.com", "ko_token", "R", int(NOW + DAY))]
    monkeypatch.setattr(auth, "refresh", lambda *a, **k: refreshed)
    monkeypatch.setattr(auth, "login_interactive",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("续期成功就不该再让用户登")))
    dest = str(tmp_path / "c.txt")
    monkeypatch.setattr(auth, "save_cookies", lambda cks, d=None: save_cookies(cks, dest))

    _, cks = auth.get_cookie(app_id=APP_ID, interactive_ok=True)
    assert find_ko_token(cks, APP_ID, now=NOW).value == "R"


# ------------------------------------------------------------ api_verifier
def test_api_verifier_true_when_api_accepts(monkeypatch):
    import xiaoe_dl.core as core
    monkeypatch.setattr(core, "get_lookback", lambda *a, **k: ("h5", [{"x": 1}]))
    assert auth.api_verifier(APP_ID, "l_x")([_ck()]) is True


def test_api_verifier_false_when_api_rejects(monkeypatch):
    """这条是它存在的全部理由：匿名的 ko_token 也在、也没过期，
    但接口会拒。只有真调一次才能分辨。"""
    import xiaoe_dl.core as core
    def reject(*a, **k):
        raise RuntimeError("code=11302")
    monkeypatch.setattr(core, "get_lookback", reject)
    assert auth.api_verifier(APP_ID, "l_x")([_ck()]) is False
