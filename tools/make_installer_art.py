# -*- coding: utf-8 -*-
"""生成 NSIS 安装器用的位图。

    python tools/make_installer_art.py

NSIS（MUI2）只认 24 位 BMP，尺寸也是写死的：

    installer-header.bmp    150 × 57    向导右上角的横幅
    installer-sidebar.bmp   164 × 314   欢迎页 / 完成页左侧的竖图

颜色取自图标本身：品牌红 #E52030、金 #F0A820。图标换了就重跑一次这个
脚本，别手工改 BMP——手工改出来的调色板和原图对不上。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "desktop" / "src-tauri" / "icons"

RED = (229, 32, 48)
GOLD = (240, 168, 32)
INK = (32, 33, 36)
MUTED = (110, 112, 118)
PAPER = (250, 249, 248)

HEADER = (150, 57)
SIDEBAR = (164, 314)

#: 微软雅黑；TTC 里下标 1 是粗体
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyhbd.ttc", 1),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _logo(size: int) -> Image.Image:
    icon = Image.open(ICONS / "icon.png").convert("RGBA")
    return icon.resize((size, size), Image.LANCZOS)


def draw_header() -> Image.Image:
    w, h = HEADER
    img = Image.new("RGB", HEADER, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, h - 2, w, h], fill=RED)          # 底部一条品牌色
    logo = _logo(h - 14)
    img.paste(logo, (10, 7), logo)
    d.text((10 + logo.width + 8, 12), "Ocemind", font=_font(15), fill=RED)
    d.text((10 + logo.width + 8, 31), "内容执行器", font=_font(12), fill=MUTED)
    return img


def draw_sidebar() -> Image.Image:
    w, h = SIDEBAR
    img = Image.new("RGB", SIDEBAR, RED)
    d = ImageDraw.Draw(img)
    # 底部渐变到金色，呼应图标那一笔
    for y in range(h):
        t = max(0.0, (y - h * 0.72) / (h * 0.28))
        if t > 0:
            d.line([0, y, w, y], fill=tuple(
                round(a + (b - a) * t) for a, b in zip(RED, GOLD)))

    # 白底圆角卡片托住图标（图标本身是红金，直接放红底上看不清）
    card = 104
    x0, y0 = (w - card) // 2, 64
    d.rounded_rectangle([x0, y0, x0 + card, y0 + card], radius=20, fill="white")
    logo = _logo(card - 16)
    img.paste(logo, (x0 + 8, y0 + 8), logo)

    d.text((w // 2, y0 + card + 22), "Ocemind", font=_font(19),
           fill="white", anchor="ma")
    d.text((w // 2, y0 + card + 48), "内容执行器", font=_font(13),
           fill=(255, 255, 255), anchor="ma")
    d.text((w // 2, h - 26), "下载 · 转写 · 处理 · 发布", font=_font(10),
           fill=(255, 255, 255), anchor="ma")
    return img


def save(img: Image.Image, name: str) -> Path:
    path = ICONS / name
    img.convert("RGB").save(path, "BMP")
    return path


def main() -> int:
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    for img, name in ((draw_header(), "installer-header.bmp"),
                      (draw_sidebar(), "installer-sidebar.bmp")):
        path = save(img, name)
        print(f"[✓] {path}  ({img.width}×{img.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
