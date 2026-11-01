# -*- coding: utf-8 -*-
"""
不动自己实现的解密，直接「借用」播放器自己的解密结果。

原理：播放器解出明文后，源码里紧接着做了两件非常独特的事：
    var o = /#EXT-X-KEY:[^\n]*URI="(.*)"[^\n]*\n/i;
    var a = r.match(o);                    <-- 钩这里
    ...
    r.indexOf("#EXTM3U") < 0               <-- 和这里
所以只要在页面脚本运行前打好补丁，这两个钩子任一中招，
就把当时的字符串（= 明文 m3u8）存到 window 上。
"""

import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOOK_JS = r"""
(() => {
  // 先抓原始引用，钩子内部一律走原始实现，避免自我递归
  const origMatch  = String.prototype.match;
  const origIndexOf = String.prototype.indexOf;
  const origSplit  = String.prototype.split;
  const origSearch = String.prototype.search;
  const hasTag = (s) => origIndexOf.call(s, '#EXTM3U') !== -1;

  const store = (s) => {
    try {
      if (typeof s === 'string' && hasTag(s)) {
        if (!window.__ocemind_pl || s.length > window.__ocemind_pl.length) {
          window.__ocemind_pl = s;
        }
      }
    } catch (e) {}
  };

  String.prototype.match = function (re) {
    try {
      if (re && re.source && origIndexOf.call(re.source, 'EXT-X-KEY') !== -1) store(this);
    } catch (e) {}
    return origMatch.apply(this, arguments);
  };

  String.prototype.indexOf = function (needle) {
    try { if (needle === '#EXTM3U') store(this); } catch (e) {}
    return origIndexOf.apply(this, arguments);
  };

  String.prototype.search = function (re) {
    try {
      if (re && re.source && origIndexOf.call(re.source, 'EXTM3U') !== -1) store(this);
    } catch (e) {}
    return origSearch.apply(this, arguments);
  };

  String.prototype.split = function (sep) {
    try {
      if (sep === '\n' && typeof this === 'string' && this.length > 200
          && origIndexOf.call(this, '#EXTM3U') === 0) store(this);
    } catch (e) {}
    return origSplit.apply(this, arguments);
  };
})();
"""


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

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled",
                  "--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/155.0.0.0 Safari/537.36"))
        await ctx.add_init_script(HOOK_JS)
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()

        page.on("console", lambda m: print("   [console]", m.text[:200])
                if m.type == "error" else None)

        print(f"[nav] {args.url}")
        await page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(6)

        for sel in ["video", "[class*=play-btn]", "[class*=play]",
                    ".vjs-big-play-button"]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click(timeout=1500, force=True)
                    print(f"[play] 点击 {sel}")
                    break
            except Exception:
                continue

        print(f"[wait] {args.wait} 秒 —— 若未自动播放请在浏览器里手动点播放")
        await asyncio.sleep(args.wait)

        pl = await page.evaluate("() => window.__ocemind_pl || null")
        if pl:
            print("\n" + "=" * 70)
            print(f"✅ 捕获到明文播放列表：{len(pl)} 字符")
            print("=" * 70)
            print(pl[:1200])
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(pl)
            print(f"\n[saved] {args.out}")
        else:
            print("\n[!] 未捕获到明文播放列表。可能播放器未真正开始播放。")

        if args.keep:
            input("按回车关闭 ...")
        else:
            await asyncio.sleep(8)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--cookies", default=r"D:\视频\cookies_session.txt")
    ap.add_argument("--wait", type=int, default=45)
    ap.add_argument("--out", default=r"D:\视频\decoded_playlist.m3u8")
    ap.add_argument("--keep", action="store_true")
    asyncio.run(main(ap.parse_args()))
