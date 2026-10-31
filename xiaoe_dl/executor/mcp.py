"""MCP 服务器（stdio）。

`python -m xiaoe_dl.executor.mcp`，读环境变量 OCEMIND_URL / OCEMIND_TOKEN。
工具故意做少：不暴露"随便执行命令"，也不给 Agent 直接批准的权力。

MCP 的 stdio 传输是「一行一个 JSON」，不是 Content-Length 分帧。
"""
from __future__ import annotations

import json
import sys

from .client import Client

PROTOCOL = "2025-06-18"
SERVER = {"name": "ocemind", "version": "1.0.0"}

TOOLS = [
    {
        "name": "list_capabilities",
        "description": "列出执行器当前支持的 13 项能力及其参数。调用任何任务前先看这个。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_job",
        "description": "提交一个任务。capability 用 list_capabilities 返回的 id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability": {"type": "string"},
                "inputs": {"type": "object",
                           "description": "输入，如 {\"path\": \"D:/a.mp4\"} 或 {\"url\": \"...\"}"},
                "params": {"type": "object", "description": "参数，如 {\"srt\": true}"},
                "title": {"type": "string"},
            },
            "required": ["capability"],
        },
    },
    {
        "name": "get_job",
        "description": "查询任务状态与产物路径。",
        "inputSchema": {"type": "object",
                        "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"]},
    },
    {
        "name": "wait_job",
        "description": "阻塞等待任务结束（默认最多 10 分钟），返回最终状态。",
        "inputSchema": {"type": "object",
                        "properties": {"job_id": {"type": "string"},
                                       "timeout": {"type": "number"}},
                        "required": ["job_id"]},
    },
    {
        "name": "list_accounts",
        "description": "列出平台账号及登录状态（ok / expiring / expired / never）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "publish_prepare",
        "description": ("准备发布：校验标准并生成待批准记录，返回 snapshot_hash。"
                        "内容不合规会直接失败，不要试图绕过。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "meta": {"type": "object"},
            },
            "required": ["account_id", "files", "meta"],
        },
    },
    {
        "name": "publish_approve",
        "description": ("确认发布。必须带上与 prepare 一致的 snapshot_hash。"
                        "只有用户明确同意后才能调用。"),
        "inputSchema": {
            "type": "object",
            "properties": {"record_id": {"type": "string"},
                           "snapshot_hash": {"type": "string"}},
            "required": ["record_id", "snapshot_hash"],
        },
    },
]


def _text(payload) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def call_tool(client: Client, name: str, args: dict) -> dict:
    if name == "list_capabilities":
        return _text(client.capabilities())
    if name == "list_accounts":
        return _text(client.accounts())
    if name == "submit_job":
        job = client.submit(args["capability"], args.get("inputs") or {},
                            args.get("params") or {}, args.get("title") or "")
        return _text(job)
    if name == "get_job":
        return _text(client.job(args["job_id"]))
    if name == "wait_job":
        return _text(client.wait(args["job_id"], timeout=float(args.get("timeout") or 600)))
    if name == "publish_prepare":
        return _text(client.post("/api/v1/publish", {
            "account_id": args["account_id"], "files": args["files"],
            "meta": args["meta"]}))
    if name == "publish_approve":
        return _text(client.post(f"/api/v1/publish/{args['record_id']}/approve",
                                 {"snapshot_hash": args["snapshot_hash"]}))
    raise ValueError(f"未知工具: {name}")


def handle(client: Client, message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
        }}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(client, params.get("name", ""),
                               params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"isError": True,
                               "content": [{"type": "text", "text": str(exc)}]}}
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"未实现的方法: {method}"}}


def main() -> int:
    client = Client()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle(client, message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
