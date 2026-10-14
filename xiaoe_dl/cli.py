# -*- coding: utf-8 -*-
"""命令行入口。

    python run.py --url "<课程链接>"
    python run.py --list urls.txt --dir "D:\\视频\\交易训练营"
"""
import argparse
import sys

from xiaoe_dl.core import parse_course_url, run_one
from xiaoe_dl.cookies import load_cookies, resolve_cookies


def build_parser():
    ap = argparse.ArgumentParser(
        prog="小鹅通下载器",
        description="小鹅通回放下载器 —— 只要给课程链接，自动出 MP4")
    ap.add_argument("--url", action="append", default=[],
                    help="课程/回放链接，可重复传多次")
    ap.add_argument("--list", help="txt 文件，一行一个课程链接")
    ap.add_argument("--app-id")
    ap.add_argument("--alive-id")
    ap.add_argument("--cookies", default=None,
                    help="cookie 文件路径（默认自动找）")
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

    args.cookies = resolve_cookies(args.cookies)
    _, cookies = load_cookies(args.cookies)
    print(f"[*] cookie 文件：{args.cookies}（{len(cookies)} 条）")

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
