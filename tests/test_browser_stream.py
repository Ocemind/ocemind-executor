# -*- coding: utf-8 -*-
"""账号浏览器的画面和输入。

这两块最容易「看着能用、其实没连上」：
  - 画面靠 Page.startScreencast 推帧，每一帧都必须回 ack，不然 Chrome 只推一帧就停；
  - 输入靠 CDP 的鼠标/键盘事件，参数错一个字段，页面就当没这回事。
所以这里用假的 CDP 会话把真正发出去的消息钉住，不联网、不开浏览器。
"""
from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from ocemind.downloader import api as api_mod
from ocemind.downloader.config import Config
from ocemind.downloader.manager import TaskManager
from ocemind.executor.accounts.browser import BrowserSession

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeSession:
    """只记录 CDP 调用，回包一律空。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.notes: list[tuple[str, dict]] = []
        self.handlers: dict[str, list] = {}

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        return {}

    def notify(self, method, params=None):
        self.notes.append((method, params or {}))

    def on(self, method, handler):
        self.handlers.setdefault(method, []).append(handler)

    def send_named(self, name):
        return [params for method, params in self.calls if method == name]


class FakeChrome:
    def __init__(self):
        self.session = FakeSession()


@pytest.fixture
def session():
    sess = BrowserSession({"id": "a1", "platform": "bilibili",
                           "profile_dir": "unused"}, Config(home="unused"))
    sess.chrome = FakeChrome()          # 「已经开着」，ensure_started 会直接返回
    return sess


# ---------------------------------------------------------------- 画面

def test_screencast_frame_is_stored_and_acked(session):
    """收到一帧要存下来，并且必须回 ack，否则浏览器不再推下一帧。"""
    data = base64.b64encode(JPEG).decode()
    session._on_frame({"data": data, "sessionId": 7})

    frame, seq = session.current_frame()
    assert frame == JPEG
    assert seq == 1
    # ack 走 notify：回调在读线程里，等回包会把自己等死
    assert session.chrome.session.notes == [
        ("Page.screencastFrameAck", {"sessionId": 7}),
    ]


def test_screencast_frame_count_only_grows(session):
    data = base64.b64encode(JPEG).decode()
    session._on_frame({"data": data, "sessionId": 1})
    session._on_frame({"data": data, "sessionId": 2})
    assert session.current_frame()[1] == 2


def test_start_stream_registers_handler_and_asks_chrome(session):
    session._ensure_stream()
    calls = session.chrome.session.send_named("Page.startScreencast")
    assert calls and calls[0]["format"] == "jpeg"
    assert "Page.screencastFrame" in session.chrome.session.handlers


def test_quiet_socket_is_not_a_dead_connection():
    """页面静着的时候 CDP 一个字节都不发。以前读超时被当成断线：
    读线程一死，之后点到、打字、地址栏全部卡到超时。"""
    import socket
    import threading

    from ocemind.executor.accounts.cdp import WSClient

    try:
        mine, peer = socket.socketpair()
    except OSError:                       # pragma: no cover - 老系统没有
        pytest.skip("这个平台没有 socketpair")
    try:
        ws = object.__new__(WSClient)
        ws.sock = mine
        ws.sock.settimeout(0.2)
        ws._rest = b""
        ws._lock = threading.Lock()
        ws._closed = False

        assert ws.recv() is None, "安静不是错误，应该让调用方接着等"

        payload = b'{"id":1,"result":{}}'
        peer.sendall(bytes([0x81, len(payload)]) + payload)
        assert ws.recv() == payload.decode()
    finally:
        mine.close()
        peer.close()


def test_dead_session_counts_as_not_running(session):
    """读线程停了就别再报「开着」——不然界面点哪儿都没反应。"""
    class DeadSession(FakeSession):
        alive = False

    session.chrome.session = DeadSession()
    assert session.alive is False


def test_stop_clears_frames(session):
    data = base64.b64encode(JPEG).decode()
    session._on_frame({"data": data, "sessionId": 1})
    session.chrome = None               # stop() 里会去关进程，这里不给它机会
    session.stop()
    assert session.current_frame() == (b"", 0)


# ---------------------------------------------------------------- 输入

def test_resize_sets_the_page_viewport(session):
    """视口要跟界面那块画布一样大，不然字是缩出来的。"""
    session.resize(1024, 640)
    params = session.chrome.session.send_named("Emulation.setDeviceMetricsOverride")[0]
    assert (params["width"], params["height"]) == (1024, 640)
    assert params["deviceScaleFactor"] == 1


def test_resize_does_not_wake_a_closed_browser():
    """账号关了之后，一个迟到的尺寸请求不该把浏览器重新拉起来。"""
    sess = BrowserSession({"id": "a2", "platform": "bilibili",
                           "profile_dir": "unused"}, Config(home="unused"))
    sess.resize(1000, 700)
    sess.click(10, 10)
    sess.wheel(120)
    assert sess.chrome is None
    assert sess._viewport == (1000, 700, 1.0)  # 尺寸记着，下次开的时候用


def test_resize_passes_device_pixel_ratio(session):
    """高分屏按 2 倍出帧，界面再缩回 CSS 像素——窗口放大字才不糊。"""
    session.resize(900, 700, 2.0)
    params = session.chrome.session.send_named("Emulation.setDeviceMetricsOverride")[0]
    assert params["deviceScaleFactor"] == 2


def test_resize_clamps_absurd_scale(session):
    session.resize(900, 700, 8)
    params = session.chrome.session.send_named("Emulation.setDeviceMetricsOverride")[0]
    assert params["deviceScaleFactor"] == 2.0


def test_resize_ignores_absurd_sizes(session):
    session.resize(10, 10)
    params = session.chrome.session.send_named("Emulation.setDeviceMetricsOverride")[0]
    assert params["width"] >= 320 and params["height"] >= 240


def test_letter_key_carries_text(session):
    """带字符的键要用 keyDown+text，Chrome 靠这个把字喂进输入框。"""
    session.key("keyDown", "a", code="KeyA", vk=65, text="a")
    params = session.chrome.session.send_named("Input.dispatchKeyEvent")[0]
    assert params["type"] == "keyDown"
    assert params["text"] == "a" and params["unmodifiedText"] == "a"
    assert params["windowsVirtualKeyCode"] == 65
    assert params["code"] == "KeyA"


def test_function_key_uses_raw_key_down(session):
    session.key("keyDown", "Backspace", code="Backspace", vk=8)
    params = session.chrome.session.send_named("Input.dispatchKeyEvent")[0]
    assert params["type"] == "rawKeyDown"
    assert "text" not in params


def test_key_up_is_its_own_event(session):
    session.key("keyUp", "Enter", code="Enter", vk=13)
    params = session.chrome.session.send_named("Input.dispatchKeyEvent")[0]
    assert params["type"] == "keyUp"
    assert "text" not in params


def test_enter_carries_carriage_return(session):
    session.press("Enter")
    downs = [p for p in session.chrome.session.send_named("Input.dispatchKeyEvent")
             if p["type"] == "keyDown"]
    assert downs and downs[0]["text"] == "\r"
    assert downs[0]["windowsVirtualKeyCode"] == 13


def test_arrow_key_needs_no_text(session):
    session.press("ArrowLeft")
    downs = [p for p in session.chrome.session.send_named("Input.dispatchKeyEvent")
             if p["type"] in ("keyDown", "rawKeyDown")]
    assert downs[0]["windowsVirtualKeyCode"] == 37


def test_double_click_sends_click_count(session):
    session.click(100, 200, count=2)
    presses = session.chrome.session.send_named("Input.dispatchMouseEvent")
    assert [p["type"] for p in presses] == ["mousePressed", "mouseReleased"]
    assert presses[0]["clickCount"] == 2


def test_right_click_keeps_button(session):
    session.click(10, 20, button="right")
    presses = session.chrome.session.send_named("Input.dispatchMouseEvent")
    assert presses[0]["button"] == "right"


def test_wheel_passes_both_axes_and_position(session):
    session.wheel(120, -30, 300, 400)
    params = session.chrome.session.send_named("Input.dispatchMouseEvent")[0]
    assert params["type"] == "mouseWheel"
    assert (params["deltaX"], params["deltaY"]) == (-30, 120)
    assert (params["x"], params["y"]) == (300, 400)


def test_mouse_move_streams_hover(session):
    session.mouse_move(55, 66)
    params = session.chrome.session.send_named("Input.dispatchMouseEvent")[0]
    assert params["type"] == "mouseMoved"
    assert (params["x"], params["y"]) == (55, 66)


# ---------------------------------------------------------------- 接口

class FakeBrowser:
    """给路由用的假会话：画面和 alive 都能点名。"""

    def __init__(self, frame=b"", seq=0):
        self.frame = frame
        self.seq = seq
        self.alive = True
        self.shots = 0
        self.acts: list[dict] = []

    def current_frame(self):
        return self.frame, self.seq

    def screenshot(self):
        self.shots += 1
        return self.frame

    # 下面几个只记参数，路由传错字段这里就露馅
    def click(self, x, y, count=1, button="left"):
        self.acts.append({"kind": "click", "x": x, "y": y,
                          "count": count, "button": button})

    def mouse_move(self, x, y):
        self.acts.append({"kind": "move", "x": x, "y": y})

    def wheel(self, dy, dx=0, x=0, y=0):
        self.acts.append({"kind": "wheel", "dy": dy, "dx": dx, "x": x, "y": y})

    def key(self, event_type, key, code="", vk=0, text="", modifiers=0):
        self.acts.append({"kind": "key", "type": event_type, "key": key,
                          "vk": vk, "text": text, "modifiers": modifiers})

    def type_text(self, text):
        self.acts.append({"kind": "type", "text": text})

    def resize(self, width, height, scale=1.0):
        self.acts.append({"kind": "resize", "width": width, "height": height,
                          "scale": scale})

    def goto(self, url):
        self.acts.append({"kind": "goto", "url": url})

    def current_url(self):
        return "https://example.com/"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("OCEMIND_HOME", str(tmp_path / "appdata"))
    cfg = Config(home=tmp_path / "home")
    mgr = TaskManager(cfg, start_scheduler=False)
    application = api_mod.create_app(cfg, mgr)
    yield application
    mgr.shutdown()


def _walk(routes):
    """这版 FastAPI 的 include_router 是懒挂载（_IncludedRouter），
    路由还留在被包含的那个 router 里，得钻进去找。"""
    for route in routes:
        if getattr(route, "path", ""):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)


def endpoint(app, path, method="GET"):
    for route in _walk(app.routes):
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"没有路由 {method} {path}")


def wire(app, browser, monkeypatch):
    executor = app.state.executor
    monkeypatch.setattr(executor.store, "get_account", lambda aid: {"id": aid})
    monkeypatch.setattr(executor.pool, "get", lambda acc: browser)


def test_frame_returns_204_when_nothing_changed(app, monkeypatch):
    """界面拿着旧序号来问，就只回 204——空闲时这个接口几乎不耗流量。"""
    browser = FakeBrowser(frame=JPEG, seq=9)
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/frame.jpg")
    res = fn("a1", since=9)
    assert res.status_code == 204
    assert res.headers["X-Frame-Seq"] == "9"


def test_frame_returns_jpeg_and_seq(app, monkeypatch):
    browser = FakeBrowser(frame=JPEG, seq=10)
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/frame.jpg")
    res = fn("a1", since=9)
    assert res.status_code == 200
    assert res.body == JPEG
    assert res.headers["X-Frame-Seq"] == "10"


def test_frame_falls_back_when_stream_is_empty(app, monkeypatch):
    """老浏览器没有 screencast：退回掐图，别让界面空着。"""
    browser = FakeBrowser(frame=b"", seq=0)
    monkeypatch.setattr(browser, "screenshot", lambda: JPEG)
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/frame.jpg")
    res = fn("a1")
    assert res.status_code == 200 and res.body == JPEG


def test_frame_204_when_browser_is_not_running(app, monkeypatch):
    browser = FakeBrowser()
    browser.alive = False
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/frame.jpg")
    assert fn("a1").status_code == 204


def test_act_key_down_and_up(app, monkeypatch):
    browser = FakeBrowser()
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/act", "POST")
    fn("a1", body={"kind": "key", "key": "a", "code": "KeyA", "vk": 65, "text": "a"})
    fn("a1", body={"kind": "key", "type": "up", "key": "a", "code": "KeyA", "vk": 65})
    assert [a["type"] for a in browser.acts] == ["keyDown", "keyUp"]


def test_act_click_and_wheel(app, monkeypatch):
    browser = FakeBrowser()
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/act", "POST")
    fn("a1", body={"kind": "click", "x": 12.5, "y": 20, "count": 2})
    fn("a1", body={"kind": "wheel", "dx": 0, "dy": 300, "x": 5, "y": 6})
    assert browser.acts[0]["count"] == 2
    assert browser.acts[1] == {"kind": "wheel", "dy": 300, "dx": 0, "x": 5, "y": 6}


def test_act_resize(app, monkeypatch):
    browser = FakeBrowser()
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/act", "POST")
    fn("a1", body={"kind": "resize", "width": 1000, "height": 700})
    assert browser.acts == [{"kind": "resize", "width": 1000, "height": 700,
                             "scale": 1.0}]


def test_act_rejects_unknown_kind(app, monkeypatch):
    browser = FakeBrowser()
    wire(app, browser, monkeypatch)
    fn = endpoint(app, "/api/exec/accounts/{account_id}/act", "POST")
    with pytest.raises(HTTPException) as err:
        fn("a1", body={"kind": "teleport"})
    assert err.value.status_code == 400
