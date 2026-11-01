# -*- coding: utf-8 -*-
"""
课程平台课程真实 m3u8 抓取 —— Phase 1

目标：不重新登录、不手动导 cookie、不依赖 F12，
      自动从已登录的 Chrome Profile 2 中捕获课程平台播放器真正请求的 m3u8。

原理：
  1. 把已登录的 Profile 2 复制成一份独立的调试快照（不影响日常 Chrome，
     也满足新版 Chrome 对 --remote-debugging-port 的非默认 user-data-dir 限制）。
  2. 用 Playwright launch_persistent_context 直接以该快照启动 Chrome，
     天然带上登录态（比 connect_over_cdp 更可靠，不会拿到无 cookie 的干净 context）。
  3. 监听所有 .m3u8 网络响应，读取内容，剔除 confusion_index / 分片过少的假流。
  4. 导出当前登录态 cookies.txt，输出可直接喂给 yt-dlp 的命令。

用法：
  python sniff.py --url "课程网页URL" [--wait 25] [--cookies-out D:/视频/cookies_site.txt]

注意：
  - 每次运行前，先关闭上一次由本脚本打开的“调试 Chrome”窗口（否则 user-data-dir 被占用）。
  - 打开课程页面后，要在浏览器里点一下“播放”，让视频真正开始播放，才能抓到 m3u8。
"""

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from urllib.parse import urlparse

from playwright.async_api import async_playwright

# ---------- 环境配置（已核对本机路径） ----------
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_CHROME_UDD = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                           "Google", "Chrome", "User Data")
REAL_PROFILE = os.path.join(_CHROME_UDD, "Profile 2")
REAL_LOCAL_STATE = os.path.join(_CHROME_UDD, "Local State")
DEBUG_DIR = r"D:\视频\ocemind-debug"
DEBUG_PROFILE = os.path.join(DEBUG_DIR, "Profile 2")

# 复制 Profile 时跳过的缓存目录（省时省空间，且不破坏登录态）
EXCLUDE_DIRS = (
    "Cache", "Code Cache", "GPUCache", "Service Worker", "blob_storage",
    "GrShaderCache", "ShaderCache", "DawnCache", "Media Cache",
    "DawnGraphiteCache", "DawnWebGPUCache", "GraphiteDawnCache",
    "component_crx_cache", "Extension State",
)


def ensure_debug_profile() -> None:
    """首次运行：把已登录的 Profile 2 复制成调试快照。"""
    if os.path.exists(os.path.join(DEBUG_PROFILE, "Cookies")):
        return

    print("[setup] 首次运行：复制已登录的 Profile 2 -> 调试快照（可能需几十秒）...")
    os.makedirs(DEBUG_DIR, exist_ok=True)

    cmd = ["robocopy", REAL_PROFILE, DEBUG_PROFILE, "/E",
           "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]
    for d in EXCLUDE_DIRS:
        cmd += ["/XD", d]
    ret = subprocess.run(cmd, check=False).returncode
    if ret >= 8:  # robocopy: 0=无变化 1=复制成功 >=8=出错
        print(f"[setup] robocopy 失败，返回码 {ret}（可能目录被占用，请先完全退出 Chrome 再试）")
        sys.exit(1)

    # Local State 里存着 cookie 的解密密钥，必须一起复制，否则登录态失效
    shutil.copy2(REAL_LOCAL_STATE, os.path.join(DEBUG_DIR, "Local State"))
    print("[setup] 快照就绪。")


def classify(body, url: str) -> str:
    """返回该 m3u8 的分类标签。body 为 None 表示没读到内容。"""
    if not body or "#EXTM3U" not in body:
        return "无内容/非播放列表"
    extinf = body.count("#EXTINF")
    is_master = "#EXT-X-STREAM-INF" in body
    if "confusion" in url:
        return "假流(confusion_index)"
    if extinf < 3:
        return "假流(分片过少)"
    if is_master:
        return "master 变体列表"
    return "真实分片列表 ✅"


async def export_cookies(context, path: str) -> None:
    """把当前 context 的 cookie 导出成 yt-dlp 用的 Netscape 格式。"""
    cs = await context.cookies()
    lines = ["# Netscape HTTP Cookie File", ""]
    for c in cs:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = c.get("expires", -1) or -1
        exp = "0" if exp < 0 else str(int(exp))
        lines.append("\t".join([domain, flag, c.get("path", "/"), secure, exp,
                                c.get("name", ""), c.get("value", "")]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[cookies] 已导出 {len(cs)} 条 cookie -> {path}")


async def main(url: str, wait: int, cookies_out: str) -> None:
    ensure_debug_profile()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=DEBUG_DIR,
            executable_path=CHROME,
            headless=False,
            args=["--profile-directory=Profile 2", "--no-first-run",
                  "--no-default-browser-check"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        found = []
        tasks = set()

        async def handle(resp):
            try:
                u = resp.url
                ctype = (resp.headers.get("content-type") or "").lower()
                if ".m3u8" not in u and "mpegurl" not in ctype:
                    return
                try:
                    body = await resp.text()
                except Exception:
                    body = None
                req = resp.request
                found.append({
                    "url": u,
                    "body": body,
                    "referer": req.headers.get("referer", "") if req else "",
                    "ua": req.headers.get("user-agent", "") if req else "",
                })
            except Exception:
                pass

        def on_response(resp):
            # Playwright 事件回调是同步的，这里把异步读取调度到事件循环上
            t = asyncio.create_task(handle(resp))
            tasks.add(t)
            t.add_done_callback(tasks.discard)

        page.on("response", on_response)

        print(f"[nav] 打开课程页面: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print("[nav] 页面已打开。请在浏览器窗口里点“播放”，让视频开始播放。")
        print(f"[nav] 正在监听 {wait} 秒 ...")
        await asyncio.sleep(wait)
        await asyncio.gather(*tasks, return_exceptions=True)  # 收尾未完成的读取

        await export_cookies(context, cookies_out)

        # ---- 汇总 ----
        print("\n" + "=" * 64)
        print("捕获结果")
        print("=" * 64)
        seen = set()
        for it in found:
            u = it["url"]
            if u in seen:
                continue
            seen.add(u)
            label = classify(it["body"], u)
            n_ext = it["body"].count("#EXTINF") if it["body"] else 0
            print(f"\n[{label}]  EXTINF={n_ext}")
            print(f"  {u}")

        real = [it for it in found
                if classify(it["body"], it["url"]) in ("真实分片列表 ✅", "master 变体列表")]
        if real:
            leaf = [it for it in real if "✅" in classify(it["body"], it["url"])]
            it = leaf[-1] if leaf else real[-1]
            referer = it["referer"] or (urlparse(url).scheme + "://" + urlparse(url).netloc + "/")
            ua = it["ua"] or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")
            print("\n" + "=" * 64)
            print("下载命令（Phase 2 可直接用）：")
            print("=" * 64)
            print(f'yt-dlp --cookies "{cookies_out}" '
                  f'--referer "{referer}" '
                  f'--user-agent "{ua}" '
                  f'-o "D:/视频/%(title)s.%(ext)s" '
                  f'"{it["url"]}"')
        else:
            print("\n[!] 未捕获到真实 m3u8。可能原因：没点播放、等待时间太短、"
                  "或播放器用了别的协议（如 .mp4 直链/DRM）。")

        print("\n[done] 调试浏览器窗口保持打开，看完后可手动关闭。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="课程平台课程真实 m3u8 抓取（Phase 1）")
    ap.add_argument("--url", required=True, help="课程网页 URL")
    ap.add_argument("--wait", type=int, default=25, help="打开后监听秒数，默认 25")
    ap.add_argument("--cookies-out", default=r"D:\视频\cookies_site.txt")
    args = ap.parse_args()
    asyncio.run(main(args.url, args.wait, args.cookies_out))
