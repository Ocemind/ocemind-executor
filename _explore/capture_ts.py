# -*- coding: utf-8 -*-
"""抓播放器实际请求的分片(.ts)与媒体 URL，用于确定分片真实基址。"""

import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


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
    cookies = load_cookies(args.cookies)
    print(f"[cookies] {len(cookies)} 条")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/155.0.0.0 Safari/537.36"))
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        seen = []

        def on_request(req):
            u = req.url
            if any(k in u for k in (".ts", ".m4s", "resolution=", ".mp4", ".m3u8")):
                seen.append((u, req.headers.get("referer", "")))

        page.on("request", on_request)

        print(f"[nav] {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(6)
        for sel in ["video", "[class*=play-btn]", "[class*=play]"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click(timeout=1500, force=True)
                    print(f"[play] 点击 {sel}")
                    break
            except Exception:
                continue

        print(f"[wait] {args.wait} 秒")
        await asyncio.sleep(args.wait)

        print("\n" + "=" * 70)
        print(f"媒体请求（{len(seen)} 条，去重后）")
        print("=" * 70)
        uniq = []
        for u, ref in seen:
            if u not in [x[0] for x in uniq]:
                uniq.append((u, ref))
        for u, ref in uniq[:25]:
            print(f"\n  {u}\n    referer: {ref}")

        ts = [u for u, _ in uniq if ".ts" in u or ".m4s" in u]
        if ts:
            print("\n" + "=" * 70)
            print("分片基址推断：")
            print("=" * 70)
            print("  样本:", ts[0])
            import urllib.parse as up
            pr = up.urlparse(ts[0])
            print("  基址:", f"{pr.scheme}://{pr.netloc}{pr.path.rsplit('/',1)[0]}/")

        if args.keep:
            input("回车关闭 ...")
        else:
            await asyncio.sleep(8)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--cookies", default=r"D:\视频\cookies_session.txt")
    ap.add_argument("--wait", type=int, default=40)
    ap.add_argument("--keep", action="store_true")
    asyncio.run(main(ap.parse_args()))
