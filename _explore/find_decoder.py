# -*- coding: utf-8 -*-
"""
抓取课程平台播放器 JS，定位 [xiaoe] 混淆播放列表的解码逻辑。

思路：confusion_index 返回的 body 以 '[xiaoe]' 开头且是密文，
      解码函数一定在播放器 JS 里。把页面加载的所有 JS 保存下来，
      并按关键词（xiaoe / confusion / atob / decrypt / AES / unescape）检索。
"""

import argparse
import asyncio
import os
import re
import sys

from playwright.async_api import async_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUTDIR = r"D:\视频\site-js"
KEYS = ["[xiaoe]", "xiaoe]", "confusion", "atob", "decrypt",
        "CryptoJS", "AES", "unescape", "decodeURIComponent"]


def load_cookies(path):
    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) < 7:
                continue
            c = {"name": p[5], "value": p[6], "domain": p[0],
                 "path": p[2] or "/", "secure": p[3].upper() == "TRUE",
                 "httpOnly": False}
            try:
                if int(p[4]) > 0:
                    c["expires"] = int(p[4])
            except ValueError:
                pass
            out.append(c)
    return out


async def main(args):
    os.makedirs(OUTDIR, exist_ok=True)
    cookies = load_cookies(args.cookies)
    print(f"[cookies] 注入 {len(cookies)} 条")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/155.0.0.0 Safari/537.36"))
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        saved = {}
        tasks = set()

        async def handle(resp):
            try:
                u = resp.url
                ctype = (resp.headers.get("content-type") or "").lower()
                # 跳过明显的大二进制/媒体，其余全部抓来搜关键词
                if any(t in ctype for t in ("image/", "font/", "audio/",
                                            "video/", "octet-stream")):
                    return
                if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|woff2?|ttf|mp4|ts)(\?|$)",
                             u, re.I):
                    return
                try:
                    txt = await resp.text()
                except Exception:
                    return
                if len(txt) < 200:
                    return
                saved[u] = txt
            except Exception:
                pass

        def on_response(resp):
            t = asyncio.create_task(handle(resp))
            tasks.add(t)
            t.add_done_callback(tasks.discard)

        page.on("response", on_response)

        print(f"[nav] 打开 {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(4)

        # 尽量触发播放器加载：尝试点常见播放按钮
        for sel in ["video", ".vjs-big-play-button", "[class*=play]",
                    ".play-btn", ".xiao-e-play"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click(timeout=1500)
                    print(f"[play] 点击了 {sel}")
                    break
            except Exception:
                continue

        print(f"[wait] 收集脚本 {args.wait} 秒 ...")
        await asyncio.sleep(args.wait)
        await asyncio.gather(*tasks, return_exceptions=True)

        print(f"\n共捕获 {len(saved)} 个 JS 文件，写入 {OUTDIR}")
        hits = []
        for i, (u, txt) in enumerate(saved.items()):
            fn = os.path.join(OUTDIR, f"js_{i:03d}.js")
            with open(fn, "w", encoding="utf-8", errors="ignore") as f:
                f.write(f"// {u}\n{txt}")
            found = [k for k in KEYS if k in txt]
            if found:
                hits.append((u, fn, found, len(txt)))

        print("\n" + "=" * 70)
        print("含解码关键词的 JS")
        print("=" * 70)
        for u, fn, found, n in hits:
            print(f"\n  {fn}  ({n} 字节)")
            print(f"  URL: {u}")
            print(f"  命中: {', '.join(found)}")

        # 对最强信号的文件打印上下文
        for u, fn, found, n in hits:
            if "[xiaoe]" in found or "confusion" in found:
                txt = open(fn, encoding="utf-8", errors="ignore").read()
                for kw in ["[xiaoe]", "confusion"]:
                    for m in re.finditer(re.escape(kw), txt):
                        s = max(0, m.start() - 300)
                        print(f"\n--- {os.path.basename(fn)} 上下文({kw}) ---")
                        print(txt[s:m.start() + 300])
                        break

        print("\n[done]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--cookies", default=r"D:\视频\cookies_session.txt")
    ap.add_argument("--wait", type=int, default=30)
    asyncio.run(main(ap.parse_args()))
