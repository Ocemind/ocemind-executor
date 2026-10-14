# -*- coding: utf-8 -*-
"""
小鹅通课程真实 m3u8 抓取 —— Phase 1（cookie 注入版）

为什么不用 --cookies-from-browser / 复制 Profile / CDP 连默认目录：
  - Chrome 127+ 的 App-Bound Encryption 使 cookie 解密密钥绑定 profile 原始路径，
    复制 profile 到别处 -> cookie 全部解不出来（实测只剩 3 条）。
  - Chrome 136+ 禁止在默认 User Data 目录上开 --remote-debugging-port，
    而专用小鹅通 profile 就在默认目录，CDP 连不上。

本方案：读取你已经用插件导出的、已验证可用的 cookies.txt，
        用 Playwright 注入到抓包浏览器 -> 等价于已登录 -> 监听真实 m3u8。

用法：
  python grab.py --url "课程URL" [--wait 40] [--cookies D:/视频/cookies.txt]
                 [--download]
"""

import argparse
import asyncio
import os
import re
import sys
from urllib.parse import urlparse

from playwright.async_api import async_playwright

# 控制台按 UTF-8 输出，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_netscape_cookies(path: str) -> list:
    """把 Netscape cookies.txt 转成 Playwright 的 cookie 列表。"""
    cookies = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, cpath, secure, expires, name, value = parts[:7]
            c = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cpath or "/",
                "secure": secure.upper() == "TRUE",
                "httpOnly": False,
            }
            # Playwright: expires 必须为正整数或省略（会话 cookie）
            try:
                exp = int(expires)
                if exp > 0:
                    c["expires"] = exp
            except ValueError:
                pass
            cookies.append(c)
    return cookies


def classify(body, url: str) -> str:
    if not body or "#EXTM3U" not in body:
        return "非播放列表"
    is_master = "#EXT-X-STREAM-INF" in body
    n = body.count("#EXTINF")
    if "confusion" in url:
        return f"假流(confusion_index, 分片={n})"
    if n < 3 and not is_master:
        return f"假流(分片过少={n})"
    if is_master:
        return "master 变体列表 ✅"
    return f"真实分片列表 ✅ (分片={n})"


async def main(args) -> None:
    cookies = load_netscape_cookies(args.cookies)
    print(f"[cookies] 从 {args.cookies} 载入 {len(cookies)} 条，准备注入")
    if not cookies:
        print("[!] 没有可用于注入的 cookie，登录态将丢失。")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/155.0.0.0 Safari/537.36"),
        )
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        m3u8_hits = []
        api_hits = []
        tasks = set()

        async def handle(resp):
            try:
                u = resp.url
                ctype = (resp.headers.get("content-type") or "").lower()
                is_m3u8 = ".m3u8" in u or "mpegurl" in ctype
                is_api = ("application/json" in ctype
                          or "/api/" in u or "json" in ctype)
                if not is_m3u8 and not is_api:
                    return
                try:
                    body = await resp.text()
                except Exception:
                    return
                if not body:
                    return
                if is_m3u8:
                    m3u8_hits.append({"url": u, "body": body,
                                      "referer": resp.request.headers.get("referer", "")})
                else:
                    # 只关心提到视频地址的 JSON，降噪
                    if re.search(r"m3u8|play_?url|video_?url|\.flv|\.mp4|pull_url",
                                 body, re.I):
                        snippet = body[:2000]
                        api_hits.append({"url": u, "body": snippet})
            except Exception:
                pass

        def on_response(resp):
            t = asyncio.create_task(handle(resp))
            tasks.add(t)
            t.add_done_callback(tasks.discard)

        page.on("response", on_response)

        print(f"[nav] 打开: {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(3)

        title = await page.title()
        cur = page.url
        print(f"[nav] 标题: {title}")
        print(f"[nav] 当前地址: {cur}")
        if "login" in cur.lower() or "登录" in title:
            print("[!] 疑似被踢到登录页，cookie 可能不足或已过期。")

        print(f"[nav] 播放监听中，共 {args.wait} 秒 —— 请在浏览器里点『播放』")
        await asyncio.sleep(args.wait)
        await asyncio.gather(*tasks, return_exceptions=True)

        # ---------------- 汇总 ----------------
        print("\n" + "=" * 70)
        print("A. m3u8 捕获")
        print("=" * 70)
        seen = set()
        real = []
        for it in m3u8_hits:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            label = classify(it["body"], it["url"])
            print(f"\n[{label}]\n  {it['url']}")
            if "✅" in label:
                real.append(it)

        print("\n" + "=" * 70)
        print(f"B. 提及视频地址的接口响应（{len(api_hits)} 条，最多列 8 条）")
        print("=" * 70)
        for it in api_hits[:8]:
            print(f"\n  URL: {it['url']}")
            print("  BODY:", it["body"].replace("\n", " ")[:400])

        # ---------------- 导出 cookie + 下载命令 ----------------
        out_cookies = args.cookies_out
        cs = await ctx.cookies()
        with open(out_cookies, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n\n")
            for c in cs:
                dom = c.get("domain", "")
                flag = "TRUE" if dom.startswith(".") else "FALSE"
                sec = "TRUE" if c.get("secure") else "FALSE"
                exp = c.get("expires", -1)
                exp = "0" if (not isinstance(exp, (int, float)) or exp < 0) else str(int(exp))
                f.write("\t".join([dom, flag, c.get("path", "/"), sec, exp,
                                   c.get("name", ""), c.get("value", "")]) + "\n")
        print(f"\n[cookies] 已导出当前会话 {len(cs)} 条 -> {out_cookies}")

        if real:
            leaf = [it for it in real if it["body"].count("#EXTINF") >= 3]
            it = leaf[-1] if leaf else real[-1]
            referer = it.get("referer") or (urlparse(args.url).scheme + "://"
                                            + urlparse(args.url).netloc + "/")
            ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
            print("\n" + "=" * 70)
            print("C. 下载命令")
            print("=" * 70)
            cmd = (f'yt-dlp --cookies "{out_cookies}" '
                   f'--referer "{referer}" --user-agent "{ua}" '
                   f'-o "D:/视频/%(title)s.%(ext)s" "{it["url"]}"')
            print(cmd)
            if args.download:
                print("\n[dl] 开始下载 ...")
                import subprocess
                subprocess.run(cmd, shell=True)
        else:
            print("\n[!] 未捕获到真实 m3u8 —— 看上面 B 段的接口响应，"
                  "真实地址通常藏在那里。")

        print("\n[done] 浏览器保持打开，看完手动关闭即可。")
        if not args.download:
            await asyncio.sleep(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="小鹅通真实 m3u8 抓取（cookie 注入）")
    ap.add_argument("--url", required=True)
    ap.add_argument("--wait", type=int, default=40, help="点播放后的监听秒数")
    ap.add_argument("--cookies", default=r"D:\视频\cookies.txt",
                    help="输入：已验证可用的 cookies.txt")
    ap.add_argument("--cookies-out", default=r"D:\视频\cookies_session.txt",
                    help="输出：本次会话导出的 cookies")
    ap.add_argument("--download", action="store_true", help="抓到后立即用 yt-dlp 下载")
    asyncio.run(main(ap.parse_args()))
