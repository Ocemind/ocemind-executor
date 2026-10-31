# -*- coding: utf-8 -*-
"""界面静态资源的路径不变量。

这套界面有两个加载入口：FastAPI 自己挂在 /static，Tauri 直接把
`xiaoe_dl/web` 当 frontendDist 按相对路径取。两边都要求 index.html 里的
`static/...` 真的指向 `web/static/...`。资源一挪位置，网页版和桌面版会
同时变成一页没有样式的裸 HTML，且不会报错——所以钉住这条。
"""
import re
from pathlib import Path

from xiaoe_dl import paths

WEB = Path(paths.project_root()) / "xiaoe_dl" / "web"


def test_index_html_references_existing_assets():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="(static/[^"]+)"', html)
    assert refs, "index.html 里应该引用了 static/ 下的资源"
    for ref in refs:
        assert (WEB / ref).is_file(), f"index.html 引用了不存在的资源：{ref}"


def test_static_assets_live_under_static():
    """资源在 web/static 下，Tauri 的 frontendDist 和 FastAPI 的挂载点才对得上。"""
    for name in ("style.css", "app.js", "bridge.js"):
        assert (WEB / "static" / name).is_file(), name
