# -*- coding: utf-8 -*-
"""把图标 PNG 四周的空白裁掉，再生成整套尺寸。

    python tools/make_icons.py [源 PNG]

原图的画布比图形大一圈（四周各留几十像素透明边），任务栏、开始菜单里
看着就比别人小一圈。这里按不透明像素的包围盒裁掉多余边距，补成正方形
后重出 32 / 128 / 256 三张 PNG 和多帧 ICO。

就地覆盖 `desktop/src-tauri/icons/icon.png`，重复跑不会越裁越小——图形
已经顶到边缘时包围盒等于整张图，等于什么都没做。

图标换了记得接着跑 `python tools/make_installer_art.py`，安装器配图里
嵌的是同一张图。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "desktop" / "src-tauri" / "icons"

#: 主图边长。512 够 256 的 ICO 帧用，再大只是白占体积
CANVAS = 512

#: 低于这个 alpha 当背景。抗锯齿的边缘会留几个半透明像素，乘进来图形就裁不干净
ALPHA_FLOOR = 8

#: 裁完留 2% 呼吸位。一点不留的话图形顶到画布边缘，任务栏和开始菜单往
#: 图标上套圆角/缩放时会啃掉一圈
PADDING = 0.02

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

PNGS = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
}


def content_square(im: Image.Image) -> Image.Image:
    """按可见像素裁掉四周的透明边，补成正方形（图形居中，不拉伸）。"""
    alpha = im.getchannel("A").point(lambda v: 255 if v > ALPHA_FLOOR else 0)
    box = alpha.getbbox()
    if not box:
        return im
    piece = im.crop(box)
    # 图形本身不是正方形（这张图宽 440、高 374），按长边取方，短边两侧留白，
    # 这样比例不变；真按包围盒硬拉成正方形会把圈拉成椭圆的
    side = max(piece.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(piece, ((side - piece.width) // 2, (side - piece.height) // 2))
    return square


def render(im: Image.Image) -> Image.Image:
    """把正方形内容摆进 CANVAS 画布，四周按 PADDING 留白。"""
    inner = round(CANVAS * (1 - PADDING * 2))
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(im.resize((inner, inner), Image.LANCZOS),
                 ((CANVAS - inner) // 2, (CANVAS - inner) // 2))
    return canvas


def main() -> int:
    # GBK 控制台会把「×」「→」变成乱码，先切成 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ICONS / "icon.png"
    if not src.is_file():
        print(f"[!] 找不到 {src}")
        return 1

    before = Image.open(src).convert("RGBA")
    cropped = content_square(before)
    master = render(cropped)

    ICONS.mkdir(parents=True, exist_ok=True)
    master.save(ICONS / "icon.png")
    for name, size in PNGS.items():
        master.resize((size, size), Image.LANCZOS).save(ICONS / name)
    master.save(ICONS / "icon.ico", format="ICO", sizes=ICO_SIZES)

    print(f"[*] {src.name} {before.size[0]}×{before.size[1]} → 图形 "
          f"{cropped.size[0]}×{cropped.size[1]}，输出 {CANVAS}×{CANVAS} 及全套尺寸")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
