# -*- coding: utf-8 -*-
"""窗口尺寸：打开多大、最小能拖到多小。

这事三处各写了一遍（页面壳的 pywebview、Tauri 壳、CSS），对不上就会出现
「窗口还能拖，版面已经散了」——所以这里既钉传参，也钉三个数是同一对数。
"""
import re
import sys
import types
from pathlib import Path

from ocemind import desktop, paths

ROOT = Path(paths.project_root())


def _fake_webview(monkeypatch):
    captured = {}

    def create_window(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    fake = types.ModuleType("webview")
    fake.create_window = create_window
    fake.start = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    return captured


def test_desktop_window_opens_1280x820_and_clamps_at_1024x680(monkeypatch):
    captured = _fake_webview(monkeypatch)
    desktop._run_window("http://127.0.0.1:1/")
    kwargs = captured["kwargs"]
    assert kwargs["width"] == 1280 and kwargs["height"] == 820, "打开尺寸"
    # pywebview 的 min_size 是外框：必须比客户区底线大一圈（标题栏 + 边框），
    # 否则窗口拖到头，网页那边还差十几个像素，反而出滚动条
    min_w, min_h = kwargs["min_size"]
    assert min_w > desktop.MIN_CLIENT_W and min_h > desktop.MIN_CLIENT_H, kwargs["min_size"]
    assert (desktop.MIN_CLIENT_W, desktop.MIN_CLIENT_H) == (1024, 680)
    assert captured["args"][1] == "http://127.0.0.1:1/"


def test_window_minimum_is_the_same_number_in_all_three_places():
    css = (ROOT / "ocemind" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    app_rule = re.search(r"#app \{(.*?)\}", css, re.S).group(1)
    css_min = (int(re.search(r"min-width: (\d+)px", app_rule).group(1)),
               int(re.search(r"min-height: (\d+)px", app_rule).group(1)))

    py_min = (desktop.MIN_CLIENT_W, desktop.MIN_CLIENT_H)

    rust = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    rust_min = tuple(int(float(v)) for v in
                     re.search(r"min_inner_size\(([\d.]+), ([\d.]+)\)", rust).groups())

    assert css_min == py_min == rust_min == (1024, 680), (css_min, py_min, rust_min)


def test_window_open_size_is_the_same_in_all_three_places():
    py = (ROOT / "ocemind" / "desktop.py").read_text(encoding="utf-8")
    py_open = tuple(int(v) for v in
                    re.search(r"width=(\d+), height=(\d+)", py).groups())
    rust = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    rust_open = tuple(int(float(v)) for v in
                      re.search(r"inner_size\(([\d.]+), ([\d.]+)\)", rust).groups())
    assert py_open == rust_open == (1280, 820), (py_open, rust_open)
