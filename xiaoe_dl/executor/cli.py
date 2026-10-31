"""命令行入口：`python -m xiaoe_dl.executor.cli ...`

它只是 HTTP 的一层壳，能和界面同时跑，也能指向服务器上那一台。
"""
from __future__ import annotations

import argparse
import json
import sys

from .client import Client


def _print_job(job: dict, wait: bool = True) -> None:
    status = job.get("status", "")
    text = {"queued": "排队中", "running": "处理中", "done": "已完成",
            "error": "失败", "paused": "已暂停", "cancelled": "已取消"}.get(status, status)
    print(f"[{job.get('id')}] {text}  {job.get('title', '')}")
    if job.get("stage"):
        print(f"  阶段：{job['stage']}")
    if job.get("error"):
        print(f"  错误：{job['error']}")
    outputs = job.get("outputs") or []
    for out in outputs:
        print(f"  产物：{out}")
    if not outputs and job.get("path"):
        print(f"  产物：{job['path']}")
    if not wait and status in ("queued", "running"):
        print("  （后台继续跑，用 `ocemind jobs` 看进度）")


def cmd_capabilities(client: Client, args) -> int:
    for cap in client.capabilities():
        paid = "¥" if cap.get("paid") else " "
        print(f"{paid} {cap['id']:<20} {cap['name']}  ({cap['backend']})")
    return 0


def cmd_jobs(client: Client, args) -> int:
    for job in client.get("/api/v1/jobs?limit=50")["jobs"]:
        _print_job(job, wait=False)
    return 0


def cmd_submit(client: Client, args) -> int:
    params = json.loads(args.params) if args.params else {}
    inputs = {}
    if args.input:
        key = {"path": "path", "url": "url"}[args.input_kind]
        inputs[key] = args.input
    job = client.submit(args.capability, inputs, params, title=args.title)
    if args.no_wait:
        _print_job(job, wait=False)
        return 0
    print("已提交，等待完成…")
    _print_job(client.wait(job["id"], timeout=args.timeout))
    return 0


def cmd_download(client: Client, args) -> int:
    params = {}
    if args.dir:
        params["dir"] = args.dir
    if args.height:
        params["height"] = args.height
    job = client.submit("download.platform", {"url": args.url}, params)
    print(f"已加入队列：[{job['id']}]")
    if args.no_wait:
        return 0
    _print_job(client.wait(job["id"], timeout=args.timeout))
    return 0


def cmd_transcribe(client: Client, args) -> int:
    job = client.submit("transcribe.asr", {"path": args.path},
                        {"srt": args.srt})
    print(f"已加入队列：[{job['id']}]")
    if args.no_wait:
        return 0
    _print_job(client.wait(job["id"], timeout=args.timeout))
    return 0


def cmd_accounts(client: Client, args) -> int:
    accounts = client.accounts()
    if not accounts:
        print("还没有添加账号")
        return 0
    for acc in accounts:
        print(f"{acc['id']:<12} {acc.get('platform_name', acc['platform']):<8} "
              f"{acc['label']:<12} {acc.get('state_text', '')}")
    return 0


def cmd_connect(client: Client, args) -> int:
    info = client.get("/api/exec/connect")
    print("执行器地址")
    print(f"  本机      {info['local']}")
    for item in info.get("lan") or []:
        if isinstance(item, str):
            print(f"  内网      {item}")
        else:
            print(f"  {item.get('label', '内网'):<9} {item.get('url', '')}")
    print(f"  令牌      {'已生成 ' + info['token_preview'] if info['token_set'] else '未生成'}")
    print()
    print("Claude Code / Desktop 的 MCP 配置：")
    config = {
        "mcpServers": {
            "ocemind": {
                "command": sys.executable,
                "args": ["-m", "xiaoe_dl.executor.mcp"],
                "env": {"OCEMIND_URL": info["local"],
                        "OCEMIND_TOKEN": "<在界面的「连接Agent」里生成>"},
            }
        }
    }
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ocemind", description="Ocemind 内容执行器")
    parser.add_argument("--url", default="", help="执行器地址，默认 http://127.0.0.1:8765")
    parser.add_argument("--token", default="", help="访问令牌")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capabilities", help="列出能做的事")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("jobs", help="查看任务")
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("submit", help="提交任意能力任务")
    p.add_argument("capability")
    p.add_argument("--input", default="")
    p.add_argument("--input-kind", default="path", choices=["path", "url"])
    p.add_argument("--params", default="", help="JSON 参数")
    p.add_argument("--title", default="")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--timeout", type=float, default=900)
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("download", help="下载视频")
    p.add_argument("url")
    p.add_argument("--dir", default="")
    p.add_argument("--height", type=int, default=0)
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--timeout", type=float, default=1800)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("transcribe", help="把音视频转成文字")
    p.add_argument("path")
    p.add_argument("--srt", action="store_true")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--timeout", type=float, default=3600)
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("accounts", help="账号与登录状态")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("connect", help="打印接入配置")
    p.set_defaults(func=cmd_connect)

    args = parser.parse_args(argv)
    client = Client(args.url, args.token)
    try:
        return args.func(client, args)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
