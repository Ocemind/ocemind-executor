# -*- coding: utf-8 -*-
"""parse_course_url —— 从五花八门的课程链接里抠 app_id / alive_id。"""
import pytest

from ocemind.core import parse_course_url

APP_ID = "apptvagt9li8549"
ALIVE_ID = "l_6ab33ce1e4b0694c354c1a5b"

FULL_URL = (
    "https://apptvagt9li8549.h5.xiaoeknow.com/v4/course/alive/"
    "l_6ab33ce1e4b0694c354c1a5b?type=2&resource_type=4"
    f"&resource_id={ALIVE_ID}&app_id={APP_ID}"
    "&pro_id=p_681aa5bde4b0694ca0a8a2b4&conduit_type=live_group"
)


def test_full_real_url():
    assert parse_course_url(FULL_URL) == (APP_ID, ALIVE_ID)


def test_app_id_falls_back_to_host_regex():
    """链接里没有 app_id 参数时，从主机名里抠。"""
    url = f"https://{APP_ID}.h5.xiaoeknow.com/v4/course/alive/{ALIVE_ID}"
    assert parse_course_url(url) == (APP_ID, ALIVE_ID)


def test_alive_id_prefers_alive_id_param_over_resource_id():
    url = (f"https://{APP_ID}.h5.xiaoeknow.com/x"
           f"?app_id={APP_ID}&alive_id=l_aaaaaaaaaaaaaaaaaaaaaaaa"
           f"&resource_id=l_bbbbbbbbbbbbbbbbbbbbbbbb")
    assert parse_course_url(url) == (APP_ID, "l_aaaaaaaaaaaaaaaaaaaaaaaa")


def test_alive_id_falls_back_to_resource_id_param():
    url = (f"https://{APP_ID}.h5.xiaoeknow.com/x"
           f"?app_id={APP_ID}&resource_id={ALIVE_ID}")
    assert parse_course_url(url) == (APP_ID, ALIVE_ID)


@pytest.mark.parametrize("url", [
    "",
    "https://example.com/whatever",
    "not a url at all",
    "https://foo.h5.xiaoeknow.com/v4/course/alive/no-id-here",
])
def test_unparseable_returns_nones(url):
    app_id, alive_id = parse_course_url(url)
    assert not (app_id and alive_id), f"{url!r} 不该解析出完整结果"


def test_host_regex_does_not_match_non_xiaoeknow_host():
    url = "https://evil.com/x?alive_id=l_aaaaaaaaaaaaaaaaaaaaaaaa"
    app_id, alive_id = parse_course_url(url)
    assert app_id is None
    assert alive_id == "l_aaaaaaaaaaaaaaaaaaaaaaaa"
