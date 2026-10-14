# -*- coding: utf-8 -*-
"""cookie 解析、按域过滤、状态汇报。

这里的每一条都对应一个真实踩过的坑，别当形式主义。
"""
import time

import pytest

import xiaoe_cookies as C
from helpers import APP_ID, DAY, NOW, OTHER_APP, cookie_line


# ------------------------------------------------------------ parse_netscape
def test_parses_all_fields():
    text = cookie_line(APP_ID + ".h5.xiaoeknow.com", "ko_token", "v", 123456)
    (c,) = C.parse_netscape(text)
    assert c.domain == APP_ID + ".h5.xiaoeknow.com"
    assert c.name == "ko_token"
    assert c.value == "v"
    assert c.expires == 123456
    assert c.secure is True


def test_skips_comments_blanks_and_short_lines():
    text = "\n".join([
        "# Netscape HTTP Cookie File",
        "",
        "only\tthree\tfields",
        cookie_line(".xiaoeknow.com", "a", "1", 1),
    ])
    assert [c.name for c in C.parse_netscape(text)] == ["a"]


def test_keeps_value_containing_equals_signs():
    """base64 / JWT 形态的 cookie 值里一堆 =，按 = 切会直接截断。"""
    text = cookie_line(".xiaoeknow.com", "t", "abc=def=ghi==", 1)
    (c,) = C.parse_netscape(text)
    assert c.value == "abc=def=ghi=="


def test_httponly_prefixed_lines_are_data_not_comments():
    """浏览器导出时会给 HttpOnly cookie 加 '#HttpOnly_' 前缀，那是有效数据行。"""
    line = cookie_line(".xiaoeknow.com", "ko_token", "v", 1000)
    text = "#HttpOnly_" + line
    parsed = C.parse_netscape(text)
    assert len(parsed) == 1
    assert parsed[0].name == "ko_token"
    assert parsed[0].domain == ".xiaoeknow.com"


def test_unparseable_expiry_becomes_session_cookie():
    line = "\t".join([".xiaoeknow.com", "TRUE", "/", "TRUE", "garbage", "a", "1"])
    (c,) = C.parse_netscape(line)
    assert c.expires == 0


# ------------------------------------------------------------ load_cookies
def test_load_cookies_returns_header_and_objects(make_cookiefile):
    path = make_cookiefile()
    header, cookies = C.load_cookies(path)
    assert len(cookies) == 6
    assert "ko_token=FAKE_TOKEN_THIS_APP" in header


# --------------------------------------------------- cookie_header_for_app
def test_cookie_header_excludes_other_apps_cookies(make_cookiefile):
    """核心用途：profile 访问过第二个小鹅通账号后，不能把他的 token 发过来。"""
    _, cookies = C.load_cookies(make_cookiefile())
    header = C.cookie_header_for_app(cookies, APP_ID)
    assert "FAKE_TOKEN_THIS_APP" in header
    assert "FAKE_TOKEN_OTHER_APP" not in header
    assert OTHER_APP not in header


def test_cookie_header_keeps_root_domain_cookies(make_cookiefile):
    """根域 cookie 是所有 app 共享的，要留下。"""
    _, cookies = C.load_cookies(make_cookiefile())
    header = C.cookie_header_for_app(cookies, APP_ID)
    # sid / session_only / expired 都挂在本 app 子域下，应当都在
    for name in ("sid", "session_only", "expired"):
        assert f"{name}=" in header


def test_duplicate_name_prefers_more_specific_domain(make_cookiefile):
    """同名时子域优先于根域——子域上的才是这个 app 自己下发的登录态。"""
    _, cookies = C.load_cookies(make_cookiefile())
    header = C.cookie_header_for_app(cookies, APP_ID)
    assert header.count("ko_token=") == 1
    assert "FAKE_TOKEN_ROOT_DOMAIN" not in header


def test_cookie_header_for_unknown_app_keeps_only_root():
    cookies = C.parse_netscape("\n".join([
        cookie_line(".xiaoeknow.com", "root_only", "1", 1),
        cookie_line(APP_ID + ".h5.xiaoeknow.com", "sub_only", "2", 1),
    ]))
    header = C.cookie_header_for_app(cookies, "appnowhere")
    assert "root_only=1" in header
    assert "sub_only" not in header


def test_cookie_header_empty_input():
    assert C.cookie_header_for_app([], APP_ID) == ""


# ------------------------------------------------------------ find_ko_token
def test_finds_app_specific_token(make_cookiefile):
    _, cookies = C.load_cookies(make_cookiefile())
    tok = C.find_ko_token(cookies, APP_ID, now=NOW)
    assert tok is not None
    assert tok.value == "FAKE_TOKEN_THIS_APP"


def test_does_not_hand_other_apps_token_to_a_different_app(make_cookiefile):
    """回归测试：早期版本在「按域过滤后为空」时会回退到全部候选，
    等于把 A 账号的登录态发给 B 账号。

    这里刻意只放子域的 ko_token：默认 fixture 里还有一条根域的，
    而根域对任何 app 都合法，混在一起就测不出隔离是否生效了。
    """
    path = make_cookiefile([
        cookie_line(f"{APP_ID}.h5.xiaoeknow.com", "ko_token", "mine",
                    int(NOW) + DAY),
        cookie_line(f"{OTHER_APP}.h5.xiaoeknow.com", "ko_token", "theirs",
                    int(NOW) + DAY),
    ])
    _, cookies = C.load_cookies(path)
    assert C.find_ko_token(cookies, APP_ID, now=NOW).value == "mine"
    assert C.find_ko_token(cookies, OTHER_APP, now=NOW).value == "theirs"
    assert C.find_ko_token(cookies, "appunknown000", now=NOW) is None


def test_falls_back_to_root_domain_token_when_no_app_specific_one():
    cookies = C.parse_netscape(
        cookie_line(".xiaoeknow.com", "ko_token", "root", int(NOW) + DAY))
    tok = C.find_ko_token(cookies, APP_ID, now=NOW)
    assert tok is not None and tok.value == "root"


def test_expired_token_is_not_returned(make_cookiefile):
    _, cookies = C.load_cookies(make_cookiefile())
    # 把“现在”推到两天后，本 app 的 ko_token 已过期
    assert C.find_ko_token(cookies, APP_ID, now=NOW + 3 * DAY) is None


def test_session_cookie_counts_as_alive(make_cookiefile):
    cookies = C.parse_netscape(
        cookie_line(APP_ID + ".h5.xiaoeknow.com", "ko_token", "sess", 0))
    assert C.find_ko_token(cookies, APP_ID, now=NOW) is not None


def test_no_app_id_accepts_any_token(make_cookiefile):
    _, cookies = C.load_cookies(make_cookiefile())
    assert C.find_ko_token(cookies, None, now=NOW) is not None


# ----------------------------------------------------------- cookie_status
def test_status_reports_ko_token_expiry(make_cookiefile):
    n, text = C.cookie_status(make_cookiefile(), now=NOW)
    assert n == 6
    assert "还剩 2 天后" in text
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(NOW + 2 * DAY))
    assert when in text


def test_status_flags_expired(make_cookiefile):
    n, text = C.cookie_status(make_cookiefile(), now=NOW + 10 * DAY)
    assert "已过期" in text


def test_status_missing_file():
    assert C.cookie_status(None) == (None, None)
    assert C.cookie_status("Z:/definitely/not/here.txt") == (None, None)


def test_status_without_ko_token_falls_back_to_earliest(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("\n".join([
        cookie_line(".xiaoeknow.com", "a", "1", int(NOW) + DAY),
        cookie_line(".xiaoeknow.com", "b", "2", int(NOW) + 5 * DAY),
    ]), encoding="utf-8")
    n, msg = C.cookie_status(str(p), now=NOW)
    assert n == 2
    assert "最早" in msg
