# -*- coding: utf-8 -*-
"""命令行入口。

    python run.py --url "<课程链接>"
    python run.py --list urls.txt --dir "D:\\视频\\交易训练营"
"""
import argparse
import sys

from xiaoe_dl.auth import (AuthError, api_verifier, get_cookie,
                           playwright_available)
from xiaoe_dl.core import parse_course_url, run_one


def build_parser():
    ap = argparse.ArgumentParser(
        prog="Ocemind（课程命令行）",
        description="课程回放下载 —— 只要给课程链接，自动出 MP4。"
                    "不带参数运行 run.py 会打开图形界面。")
    ap.add_argument("--url", action="append", default=[],
                    help="课程/回放链接，可重复传多次")
    ap.add_argument("--list", help="txt 文件，一行一个课程链接")
    ap.add_argument("--app-id")
    ap.add_argument("--alive-id")
    ap.add_argument("--cookies", default=None,
                    help="cookie 文件路径（默认自动找）")
    ap.add_argument("--login", action="store_true",
                    help="登录态失效时打开浏览器让你登一次（默认不弹浏览器）")
    ap.add_argument("--no-browser", action="store_true",
                    help="绝不打开浏览器，只用手上的 cookie 文件。"
                         "脚本/定时任务里用这个，免得卡在等人操作")
    ap.add_argument("--out", help="输出 mp4 路径（单个课程时用）")
    ap.add_argument("--dir", default=r"D:\视频", help="批量默认输出目录")
    ap.add_argument("--line", default=None,
                    help="线路序号(1/2/3)或 cloud(tx/hw/byte)")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--keep-ts", action="store_true")
    ap.add_argument("--force", action="store_true", help="覆盖已存在文件")
    ap.add_argument("--test", type=int, default=0, help="只下前 N 个分片做验证")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)

    urls = list(args.url)
    if args.list:
        with open(args.list, encoding="utf-8", errors="ignore") as f:
            urls += [l.strip() for l in f
                     if l.strip() and not l.startswith("#")]
    if not urls and not (args.app_id and args.alive_id):
        ap.error("请用 --url / --list 提供课程链接")

    # 先定下 app_id：登录态要按 app 挑，而且它决定了浏览器该打开哪个店铺的页面。
    # 批量时会按第一个链接的 app 去准备登录态（同一个店铺的课通常一批下）。
    app_id_hint, alive_id_hint = args.app_id, args.alive_id
    if urls:
        u_app, u_alive = parse_course_url(urls[0])
        app_id_hint = app_id_hint or u_app
        alive_id_hint = alive_id_hint or u_alive

    # --no-browser 时走 manual：只用文件，一份都没有就报错，
    # 而不是静默去开浏览器。脚本/定时任务要的就是这个。
    mode = "manual" if args.no_browser else "auto"

    def _say(m):
        print(f"[*] {m}")

    try:
        args.cookies, cookies = get_cookie(
            app_id=app_id_hint,
            mode=mode,
            manual_path=args.cookies,
            interactive_ok=args.login,
            verifier=(api_verifier(app_id_hint, alive_id_hint)
                      if (args.login and alive_id_hint) else None),
            on_status=_say)
    except AuthError as e:
        sys.exit(f"[!] {e}")

    print(f"[*] cookie 文件：{args.cookies}（{len(cookies)} 条）")
    if args.login and not playwright_available():
        print("[*] 提示：当前环境没有 playwright，--login 起不了作用")

    if not urls:
        ok = run_one(None, args.app_id, args.alive_id, cookies, args)
        sys.exit(0 if ok else 1)

    total = len(urls)
    if total > 1 and args.out:
        sys.exit("[!] 批量模式不要用 --out，用 --dir 指定目录")
    print(f"[*] 共 {total} 个课程")

    ok_n = 0
    for i, u in enumerate(urls, 1):
        if args.app_id and args.alive_id:
            a, b = args.app_id, args.alive_id
        else:
            a, b = parse_course_url(u)
        if run_one(u, a, b, cookies, args, i, total):
            ok_n += 1
    print(f"\n[*] 全部结束：成功 {ok_n}/{total}")
    sys.exit(0 if ok_n == total else 1)
