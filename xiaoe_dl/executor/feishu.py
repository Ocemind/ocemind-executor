"""飞书协同：把产出登记进多维表格，把文字产出转成云文档。

这里不直接调飞书 Open API，而是复用本机已登录的 `lark-cli`：
它管 token、管续期、管 `--as user`，我们只负责拼命令和解析结果。
好处是用户只需要在命令行登录一次，不用把 App Secret 交给这个程序。

设计边界：
- 视频 / 音频 / 图片**不上传**，只登记一条索引（本地路径 + 文件大小）。
- 文字类（md / txt / srt / csv / json）转成飞书云文档，索引里存文档链接。
- 同步失败不阻塞任务本身。任务已经完成，飞书掉线是另一件事。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import artifacts as artifact_mod

#: 索引表的列。用户可以按这个顺序在飞书里建表，顺序无所谓，名字要对上。
REQUIRED_FIELDS = [
    "标题", "项目", "序列", "能力", "类型",
    "本地路径", "大小", "状态", "云文档", "源链接", "完成时间",
]

KIND_NAMES = {
    "video": "视频", "audio": "音频", "image": "图片",
    "doc": "文档", "text": "文字", "other": "其他",
}

_STATUS_TTL = 60
_status_cache: dict = {"key": "", "ts": 0.0, "value": {}}
_columns_cache: dict[str, list[str]] = {}


class FeishuError(RuntimeError):
    pass


# ------------------------------------------------------------------ 发现 CLI

def find_cli(cfg) -> str:
    configured = str(cfg.get("feishuCli") or "").strip().strip('"')
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
    return shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or ""


def _run(cfg, args: list[str], timeout: float = 60) -> tuple[int, str, str]:
    cli = find_cli(cfg)
    if not cli:
        raise FeishuError("本机没有找到 lark-cli")
    if cli.lower().endswith((".cmd", ".bat")):
        # npm 在 Windows 上装的是 .cmd shim，CreateProcess 不能直接执行它
        cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/c", cli, *args]
    else:
        cmd = [cli, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FeishuError(f"lark-cli 跑不起来：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FeishuError(f"lark-cli 超时（{timeout:.0f}s）：{' '.join(args[:3])}") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _short(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _json_from(text: str):
    """lark-cli 有时会在 JSON 前后打日志，从文本里捞出那个 JSON。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line[:1] in ("{", "["):
            try:
                return json.loads(line)
            except ValueError:
                continue
    match = re.search(r"([\[{].*[\]}])", text, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except ValueError:
            return None
    return None


_DOC_URL = re.compile(
    r"https?://[^\s\"'<>]+/(?:docx|docs|doc|wiki|base|sheets)/[A-Za-z0-9]+"
)


def _url_from(text: str) -> str:
    match = _DOC_URL.search(text or "")
    return match.group(0) if match else ""


# ------------------------------------------------------------------- 连接状态

def status(cfg, refresh: bool = False) -> dict:
    cli = find_cli(cfg)
    if not cli:
        return {
            "installed": False, "path": "", "version": "", "logged_in": None,
            "message": "没找到 lark-cli。装好并登录一次后，这里会自动亮起来。",
            "install_hint": "npm i -g @larksuite/cli && lark-cli auth login --recommend",
        }

    key = f"{cli}|{cfg.get('feishuBaseToken')}|{cfg.get('feishuTableId')}"
    if (not refresh and _status_cache["key"] == key
            and time.time() - _status_cache["ts"] < _STATUS_TTL):
        return dict(_status_cache["value"])

    version = ""
    try:
        code, out, err = _run(cfg, ["--version"], timeout=20)
        if code == 0:
            version = _short((out or err).strip().splitlines()[0] if (out or err).strip() else "", 80)
        else:
            version = ""
    except FeishuError as exc:
        version = ""
        err = str(exc)

    logged_in = None
    message = ""
    account = ""
    try:
        code, out, err = _run(cfg, ["auth", "status"], timeout=40)
        text = f"{out}\n{err}"
        data = _json_from(text)
        if isinstance(data, dict):
            raw = data.get("data") if isinstance(data.get("data"), dict) else data
            for field in ("logged_in", "loggedIn", "is_logged_in", "isLogin"):
                if field in raw:
                    logged_in = bool(raw[field])
                    break
            account = str(raw.get("user_name") or raw.get("name")
                          or raw.get("email") or "")
        low = text.lower()
        if logged_in is None:
            if any(k in low for k in ("未登录", "not logged", "no login", "login required")):
                logged_in = False
            elif any(k in low for k in ("已登录", "logged in")):
                logged_in = True
        if code != 0:
            message = _short(err or out) or f"auth status 退出码 {code}"
        elif logged_in is False:
            message = "lark-cli 还没登录"
        elif logged_in is None:
            message = "CLI 装好了，登录态没确认（可以点「测试连接」再试一次）"
    except FeishuError as exc:
        message = str(exc)

    value = {
        "installed": True, "path": cli, "version": version,
        "logged_in": logged_in, "account": account, "message": message,
        "base_ready": bool(cfg.get("feishuBaseToken") and cfg.get("feishuTableId")),
        "install_hint": "",
    }
    _status_cache.update(key=key, ts=time.time(), value=value)
    return dict(value)


def invalidate_cache() -> None:
    _status_cache.update(key="", ts=0.0, value={})
    _columns_cache.clear()


# ------------------------------------------------------------------- 表格结构

def parse_base_input(base: str, table: str) -> tuple[str, str]:
    """允许用户直接粘多维表格链接，从里面抠出 app_token 和 table_id。"""
    base = (base or "").strip()
    table = (table or "").strip()
    if base.startswith("http"):
        if not table:
            match = re.search(r"[?&]table=([A-Za-z0-9]+)", base)
            if match:
                table = match.group(1)
        match = re.search(r"/base/([A-Za-z0-9]+)", base)
        if match:
            base = match.group(1)
    if "?table=" in base:
        base, _, rest = base.partition("?")
        match = re.search(r"table=([A-Za-z0-9]+)", rest)
        if match and not table:
            table = match.group(1)
    return base, table


def table_columns(cfg, refresh: bool = False) -> list[str]:
    base, table = parse_base_input(cfg.get("feishuBaseToken") or "",
                                   cfg.get("feishuTableId") or "")
    cache_key = f"{base}|{table}"
    if not refresh and cache_key in _columns_cache:
        return _columns_cache[cache_key]
    if not base or not table:
        return []
    args = ["base", "describe", "--base-token", base, "--table-id", table]
    code, out, err = _run(cfg, args, timeout=40)
    if code != 0 and ("unknown" in (err or "").lower() or "未知" in (err or "")):
        # 不同版本的 CLI 有的把子命令写成 `describe`，有的写成 `+describe`
        code, out, err = _run(
            cfg, ["base", "+describe", "--base-token", base, "--table-id", table],
            timeout=40,
        )
    if code != 0:
        raise FeishuError(_short(err or out) or f"base describe 退出码 {code}")
    data = _json_from(out) or {}
    names: list[str] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key in ("fields", "items", "items_list"):
                value = node.get(key)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            name = item.get("field_name") or item.get("name")
                            if name:
                                names.append(str(name))
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    if not names:
        names = re.findall(r'"field_name"\s*:\s*"([^"]+)"', out)
    names = list(dict.fromkeys(names))
    if names:
        _columns_cache[cache_key] = names
    return names


def check_table(cfg) -> dict:
    base, table = parse_base_input(cfg.get("feishuBaseToken") or "",
                                   cfg.get("feishuTableId") or "")
    if not base or not table:
        return {"ok": False, "fields": [], "missing": REQUIRED_FIELDS,
                "error": "还没填多维表格 app_token 和 table_id"}
    try:
        columns = table_columns(cfg, refresh=True)
    except FeishuError as exc:
        return {"ok": False, "fields": [], "missing": REQUIRED_FIELDS,
                "error": str(exc)}
    missing = [name for name in REQUIRED_FIELDS if name not in columns]
    if not columns:
        return {"ok": False, "fields": [], "missing": REQUIRED_FIELDS,
                "error": "连上了，但没读出字段名"}
    return {"ok": not missing, "fields": columns, "missing": missing, "error": ""}


# --------------------------------------------------------------------- 云文档

def create_doc(cfg, title: str, text: str, folder_token: str = "") -> str:
    """把一段文字建成飞书云文档，返回文档链接。"""
    fd, path = tempfile.mkstemp(suffix=".md", prefix="ocemind-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        args = ["docs", "+create", "--doc-format", "markdown",
                "--title", title or "未命名", "--content", f"@{path}"]
        if folder_token:
            args += ["--folder-token", folder_token]
        code, out, err = _run(cfg, args, timeout=120)
        if code != 0 and folder_token:
            # 不同版本的 CLI 对目录参数支持不一致：目录不支持就退回我的空间，
            # 不能因为放不进指定文件夹就整条放弃。
            retry = [a for a in args if a not in ("--folder-token", folder_token)]
            code2, out2, err2 = _run(cfg, retry, timeout=120)
            if code2 == 0:
                code, out, err = code2, out2, err2
        if code != 0:
            raise FeishuError(_short(err or out) or f"docs +create 退出码 {code}")
        url = _url_from(out) or _url_from(err)
        if not url:
            raise FeishuError("文档建好了，但输出里没有链接：" + _short(out or err))
        return url
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _read_text(path: str, limit: int = 2_000_000) -> str:
    data = Path(path).read_bytes()[:limit]
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------------- 索引记录

def record_fields(art: dict, status_text: str) -> dict:
    return {
        "标题": art.get("title") or Path(art.get("path") or "").name,
        "项目": art.get("project") or "未分类",
        "序列": art.get("sequence") or "",
        "能力": art.get("capability") or "",
        "类型": KIND_NAMES.get(art.get("file_kind") or "", "其他"),
        "本地路径": art.get("path") or "",
        "大小": int(art.get("size") or 0),
        "状态": status_text,
        "云文档": art.get("remote_url") or "",
        "源链接": art.get("source") or "",
        "完成时间": time.strftime("%Y-%m-%d %H:%M", time.localtime(art.get("created_at") or time.time())),
    }


def _batch_create(cfg, rows: list[dict]) -> None:
    base, table = parse_base_input(cfg.get("feishuBaseToken") or "",
                                   cfg.get("feishuTableId") or "")
    if not base or not table:
        raise FeishuError("还没填多维表格 app_token / table_id")
    payload = json.dumps({"create_records": rows}, ensure_ascii=False)
    code, out, err = _run(
        cfg,
        ["base", "+record-batch-create", "--base-token", base,
         "--table-id", table, "--json", payload],
        timeout=120,
    )
    if code != 0:
        raise FeishuError(_short(err or out) or f"record-batch-create 退出码 {code}")


def _fields_subset(cfg, fields: dict) -> dict:
    """只发表里真实存在的列。用户少建一列，不该让整批记录都写不进去。"""
    try:
        columns = table_columns(cfg)
    except FeishuError:
        return fields
    if not columns:
        return fields
    known = {k: v for k, v in fields.items() if k in columns}
    return known or fields


def _summary(store, arts: list[dict], errors: list[str]) -> dict:
    """同步结果以数据库里的最终状态为准，不靠调用过程中的临时变量猜。"""
    counts = {"synced": 0, "partial": 0, "failed": 0}
    for art in arts:
        row = store.get_artifact(art["id"]) or art
        state = row.get("status") or "local"
        if state in counts:
            counts[state] += 1
    return {**counts, "errors": errors}


def sync_artifacts(cfg, store, arts: list[dict]) -> dict:
    """把一批产出同步到飞书：先建云文档，再写索引记录。

    返回 {"synced": n, "partial": n, "failed": n, "errors": [...]}。
    不会抛异常给调用方——同步失败要留在产出记录里，而不是炸掉整个任务。
    """
    if not arts:
        return {"synced": 0, "partial": 0, "failed": 0, "errors": []}

    doc_mode = (cfg.get("feishuDocMode") or "text") != "off"
    folder = (cfg.get("feishuFolderToken") or "").strip()
    base, table = parse_base_input(cfg.get("feishuBaseToken") or "",
                                   cfg.get("feishuTableId") or "")
    has_table = bool(base and table)
    errors: list[str] = []
    doc_failed: set[str] = set()

    if doc_mode:
        for art in arts:
            if not artifact_mod.is_text(art.get("path") or ""):
                continue
            if art.get("remote_url"):
                continue
            try:
                text = _read_text(art["path"])
                url = create_doc(cfg, art.get("title") or "未命名", text, folder)
                store.update_artifact(art["id"], remote_url=url, error="")
                art["remote_url"] = url
                if art.get("remote_token") == "indexed":
                    # 索引行早就写过了，这次只是把上次没建成的文档补上
                    store.update_artifact(art["id"], status="synced")
            except (FeishuError, OSError) as exc:
                doc_failed.add(art["id"])
                errors.append(f"{art.get('title')}: {exc}")
                if art.get("remote_token") == "indexed":
                    store.update_artifact(art["id"], error=str(exc))

    if not has_table:
        message = "还没配置索引表，产出没有同步"
        for art in arts:
            state = "partial" if art.get("remote_url") else "failed"
            store.update_artifact(art["id"], status=state, error=message)
        errors.append(message)
        return _summary(store, arts, errors)

    rows, row_arts = [], []
    for art in arts:
        if art.get("remote_token") == "indexed":
            # 已经写过索引行，不再插一条，避免重试时重复
            continue
        if art["id"] in doc_failed:
            status_text = "部分同步"
        elif artifact_mod.is_text(art.get("path") or "") and not doc_mode:
            status_text = "已登记"
        else:
            status_text = "已同步"
        rows.append(_fields_subset(cfg, record_fields(art, status_text)))
        row_arts.append(art)

    if rows:
        now = time.time()
        try:
            _batch_create(cfg, rows)
        except FeishuError as exc:
            errors.append(f"写索引失败：{exc}")
            for art in row_arts:
                state = "partial" if art.get("remote_url") else "failed"
                store.update_artifact(art["id"], status=state, error=str(exc))
        else:
            for art in row_arts:
                state = "partial" if art["id"] in doc_failed else "synced"
                store.update_artifact(
                    art["id"], status=state, synced_at=now, remote_token="indexed",
                    error="云文档没建成" if state == "partial" else "")
    return _summary(store, arts, errors)


def sync_task(cfg, store, task: dict) -> dict:
    """任务完成后的后台同步入口。任何异常都只记在产出上，不外抛。"""
    try:
        arts = [a for a in store.artifacts_for_task(task.get("id", ""))
                if a.get("status") != "synced"]
        return sync_artifacts(cfg, store, arts)
    except Exception as exc:  # noqa: BLE001
        return {"synced": 0, "partial": 0, "failed": 0, "errors": [str(exc)]}
