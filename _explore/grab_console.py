# -*- coding: utf-8 -*-
"""
从课程平台播放器的 console 输出中直接取得解密后的 m3u8。

原理（来自反编译播放器 JS）：
  var r = f["confusion"].decode(raw, key);
  console.log("====================调用混淆解密方法=================", key);
  console.log("rsp", r, ...);      <-- r 就是明文播放列表

所以不需要自己实现解密算法，只要把 console 抓下来即可。
"""

import argparse
import asyncio
import re
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
    print(f"[cookies] 注入 {len(cookies)} 条")

    logs = []

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

        def on_console(msg):
            try:
                logs.append({"type": msg.type, "text": msg.text})
            except Exception:
                pass

        page.on("console", on_console)

        print(f"[nav] 打开 {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(6)

        # 自动尝试播放
        try:
            await page.evaluate("""() => {
                const v = document.querySelector('video');
                if (v) { v.muted = true; v.play().catch(()=>{}); return !!v; }
                return false;
            }""")
        except Exception:
            pass
        for sel in [".vjs-big-play-button", "[class*=play-btn]", "[class*=play]",
                    "video"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click(timeout=1500, force=True)
                    print(f"[play] 点击 {sel}")
                    break
            except Exception:
                continue

        print(f"[wait] 监听 console {args.wait} 秒 —— 必要时请在浏览器里手动点播放")
        await asyncio.sleep(args.wait)

        # -------------- 分析 console --------------
        with open(args.dump, "w", encoding="utf-8") as f:
            for l in logs:
                f.write(f"[{l['type']}] {l['text']}\n")
        print(f"\n[console] 共 {len(logs)} 条，已存 {args.dump}")

        # 1) 找密钥
        keys = [l["text"] for l in logs if "调用混淆解密方法" in l["text"]]
        for k in keys:
            print(f"\n[KEY] {k[:300]}")

        # 2) 找明文播放列表
        playlists = [l["text"] for l in logs if "#EXTM3U" in l["text"]]
        if playlists:
            best = max(playlists, key=len)
            print("\n" + "=" * 70)
            print(f"找到解密后的播放列表（{len(best)} 字符）")
            print("=" * 70)
            print(best[:1500])
            if len(best) > 1500:
                print("... (截断)")
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(best)
            print(f"\n[out] 已写入 {args.out}")
        else:
            print("\n[!] console 里没有明文播放列表。相关线索：")
            for l in logs:
                t = l["text"]
                if re.search(r"confusion|解密|rsp|playlist|m3u8", t, re.I):
                    print("   ", t[:200])

        if args.keep:
            print("\n[done] 浏览器保持打开。按回车关闭 ...")
            input()
        else:
            print("\n[done] 10 秒后自动关闭浏览器")
            await asyncio.sleep(10)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--cookies", default=r"D:\视频\cookies_session.txt")
    ap.add_argument("--wait", type=int, default=45)
    ap.add_argument("--dump", default=r"D:\视频\ocemind_console.log")
    ap.add_argument("--out", default=r"D:\视频\decoded_playlist.m3u8")
    ap.add_argument("--keep", action="store_true")
    asyncio.run(main(ap.parse_args()))
