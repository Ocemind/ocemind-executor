"""执行器门面：界面、HTTP API、CLI、MCP 共用这一层。

所有业务判断都在这里，外面四个入口都只是薄壳。这样"三个入口能力不一致"
这种事从结构上就不会发生——它们调用的是同一批方法。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from . import backends, local_install
from .accounts.browser import BrowserPool, read_cookie_expiry
from .accounts.platforms import all_platforms, platform_info
from .engines import registry
from .engines.engine_map import verify_answer
from .publish.engine import snapshot_hash
from .store import Store

EXPIRING_DAYS = 3          # 剩余少于这个天数就标黄
STALE_DAYS = 7             # 超过这个天数没成功登录过，即使 cookie 没过期也提示


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_fingerprint(path: str) -> str:
    """文件指纹只看大小和修改时间——逐字节哈希几 GB 的视频太慢。"""
    try:
        st = os.stat(path)
        return f"{Path(path).name}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return f"{path}:missing"


def build_input_fingerprint(inputs: dict) -> str:
    parts = []
    for key in sorted(inputs):
        value = inputs[key]
        if key in ("path", "sample", "video", "srt", "cover") and isinstance(value, str) and value:
            parts.append(f"{key}={file_fingerprint(value)}")
        elif key in ("files", "paths") and isinstance(value, list):
            parts.append(f"{key}=" + ",".join(file_fingerprint(str(v)) for v in value))
        else:
            parts.append(f"{key}={canonical(value)}")
    return "|".join(parts)


def make_idem(capability_id: str, inputs: dict, params: dict) -> str:
    """同一个东西反复提交，拿到的是同一个任务。"""
    blob = f"{capability_id}\n{build_input_fingerprint(inputs)}\n{canonical(params)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Executor:
    def __init__(self, cfg, manager) -> None:
        self.cfg = cfg
        self.manager = manager
        self.store = Store(Path(cfg.home) / "executor.db")
        self.pool = BrowserPool(cfg)
        manager.store = self.store
        manager.pool = self.pool

    def close(self) -> None:
        try:
            self.pool.shutdown()
        finally:
            self.store.close()

    # ---------------------------------------------------------- 能力清单
    def capabilities(self) -> list[dict]:
        """注册表是底稿，这里补上"实际会用哪个后端"。

        界面和 Agent 看到的 `backend` 是解析后的结果：装了本地 OCR 就显示
        "本地"，没装才显示"云端"。不能只按注册表写死的字段说话。
        """
        caps = registry.all_capabilities()
        info = backends.summary(self.cfg)
        for cap in caps:
            got = info.get(cap["id"])
            if not got:
                continue
            cap["backend"] = got["backend"]
            cap["provider"] = got.get("provider_name") or ""
            cap["model"] = got.get("model") or ""
            cap["ready"] = got["ready"]
            cap["backendProblem"] = got.get("problem") or ""
            cap["localAvailable"] = any(e["available"] for e in got.get("localOptions") or [])
            if got["backend"] == "local":
                cap["paid"] = False
        return caps

    def capability_health(self) -> dict:
        problems = {c["id"]: c["backendProblem"] for c in self.capabilities()
                    if c.get("backendProblem")}
        return {"missing_engines": verify_answer(), "backend_problems": problems}

    # ------------------------------------------------------ 模型与接口
    def models(self) -> dict:
        return {
            "providers": backends.providers_view(self.cfg),
            "capabilities": list(backends.summary(self.cfg).values()),
            "localInstall": local_install.status(),
        }

    def set_model(self, capability_id: str, patch: dict) -> dict:
        backends.set_choice(self.cfg, capability_id, patch)
        return backends.summary(self.cfg)[capability_id]

    def install_local(self, engine_id: str) -> dict:
        return local_install.start(engine_id)

    def local_status(self) -> dict:
        engines = []
        for kind in backends.LOCAL_ENGINES:
            for engine in backends.local_engines(kind):
                engines.append(dict(engine, kind=kind))
        return {"engines": engines, "install": local_install.status()}

    # -------------------------------------------------------------- 任务
    def submit(self, capability_id: str, inputs: dict | None = None,
               params: dict | None = None, title: str = "",
               project: str = "", sequence: str = "",
               source: str = "manual", order_id: str = "") -> dict:
        if registry.get(capability_id) is None:
            raise ValueError(f"未知能力: {capability_id}")
        inputs = dict(inputs or {})
        params = dict(params or {})
        idem = make_idem(capability_id, inputs, params)
        task = self.manager.add_capability(capability_id, inputs, params,
                                           title=title, idem=idem,
                                           project=project, sequence=sequence,
                                           source=source, order_id=order_id)
        return self.manager.public(task)

    def get_job(self, job_id: str) -> dict | None:
        task = self.manager.get(job_id)
        return self.manager.public(task) if task else None

    def list_jobs(self, limit: int = 200) -> list[dict]:
        tasks = self.manager.list()
        tasks.sort(key=lambda t: t.get("created_at") or 0, reverse=True)
        return tasks[:limit]

    def cancel(self, job_id: str) -> None:
        self.manager.cancel(job_id)

    def wait(self, job_id: str, timeout: float = 600) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            job = self.get_job(job_id)
            if not job:
                return None
            if job.get("status") in ("done", "error", "cancelled", "paused"):
                return job
            time.sleep(0.5)
        return self.get_job(job_id)

    # -------------------------------------------------------------- 产出
    def artifacts(self, project: str = "", status: str = "",
                  limit: int = 200, ungrouped: bool = False) -> dict:
        return {
            "artifacts": self.store.list_artifacts(limit, project, status,
                                                   ungrouped=ungrouped),
            "projects": self.store.projects(),
        }

    def claim_artifacts(self, ids: list[str], project: str,
                        sequence: str = "") -> dict:
        """认领：给手动跑出来的产出补上项目和序列。

        人工临时干活时常常没想好归哪个项目，「先干活、后认领」——文件已经
        在磁盘上，这里只补元数据，不搬文件（搬了老链接和飞书索引都会失效）。
        """
        project = (project or "").strip()
        if not project:
            raise ValueError("项目不能为空")
        sequence = (sequence or "").strip()
        done, missing = 0, []
        for art_id in ids:
            art = self.store.get_artifact(art_id)
            if not art:
                missing.append(art_id)
                continue
            fields = {"project": project}
            if sequence:
                fields["sequence"] = sequence
            self.store.update_artifact(art_id, **fields)
            done += 1
        return {"claimed": done, "missing": missing,
                "artifacts": self.store.list_artifacts(200),
                "projects": self.store.projects()}

    def index_history(self) -> dict:
        """补登记功能上线前完成的任务。产物已经在磁盘上，不重新跑。"""
        from . import artifacts as artifact_mod
        count = artifact_mod.index_history(self.store, self.list_jobs(limit=2000))
        return {"indexed": count,
                "artifacts": self.store.list_artifacts(200),
                "projects": self.store.projects()}

    def sync_artifacts(self, task_id: str = "",
                       ids: list[str] | None = None) -> dict:
        """手动同步。同步失败留在记录里，不抛给界面炸一屏。"""
        from . import feishu
        if ids:
            arts = [a for a in (self.store.get_artifact(i) for i in ids) if a]
        elif task_id:
            arts = self.store.artifacts_for_task(task_id)
        else:
            arts = self.store.list_artifacts(500, status="unsynced")
        if not arts:
            return {"synced": 0, "partial": 0, "failed": 0,
                    "errors": [], "artifacts": self.store.list_artifacts(200)}
        result = feishu.sync_artifacts(self.cfg, self.store, arts)
        result["artifacts"] = self.store.list_artifacts(200)
        result["projects"] = self.store.projects()
        return result

    # ------------------------------------------------------------ 飞书协同
    def feishu_status(self, refresh: bool = False) -> dict:
        from . import feishu
        info = feishu.status(self.cfg, refresh=refresh)
        info["enabled"] = bool(self.cfg.get("feishuEnabled"))
        info["docMode"] = self.cfg.get("feishuDocMode") or "text"
        info["cli"] = self.cfg.get("feishuCli") or ""
        base, table = feishu.parse_base_input(
            self.cfg.get("feishuBaseToken") or "",
            self.cfg.get("feishuTableId") or "")
        info["baseToken"] = base
        info["tableId"] = table
        info["folderToken"] = self.cfg.get("feishuFolderToken") or ""
        info["fields"] = feishu.REQUIRED_FIELDS
        return info

    def feishu_save(self, patch: dict) -> dict:
        from . import feishu
        allowed = {"feishuEnabled", "feishuCli", "feishuBaseToken",
                   "feishuTableId", "feishuFolderToken", "feishuDocMode"}
        values = {k: v for k, v in patch.items() if k in allowed}
        if "feishuEnabled" in values:
            values["feishuEnabled"] = bool(values["feishuEnabled"])
        if values.get("feishuDocMode") not in (None, "text", "off"):
            values["feishuDocMode"] = "text"
        if "feishuBaseToken" in values or "feishuTableId" in values:
            base, table = feishu.parse_base_input(
                values.get("feishuBaseToken", self.cfg.get("feishuBaseToken") or ""),
                values.get("feishuTableId", self.cfg.get("feishuTableId") or ""))
            values["feishuBaseToken"] = base
            values["feishuTableId"] = table
        self.cfg.update(values)
        feishu.invalidate_cache()
        return self.feishu_status(refresh=True)

    def feishu_test(self) -> dict:
        from . import feishu
        info = self.feishu_status(refresh=True)
        table = feishu.check_table(self.cfg) if info.get("baseToken") else {
            "ok": False, "fields": [], "missing": feishu.REQUIRED_FIELDS,
            "error": "还没填多维表格 app_token / table_id"}
        info["table"] = table
        return info

    # -------------------------------------------------------------- 账号
    def platforms(self) -> list[dict]:
        return all_platforms()

    def accounts(self) -> list[dict]:
        now = time.time()
        result = []
        for acc in self.store.list_accounts():
            item = dict(acc)
            item.update(self._account_state(acc, now))
            item["platform_name"] = platform_info(acc["platform"]).get("name", acc["platform"])
            item["running"] = acc["id"] in self.pool.running()
            result.append(item)
        return result

    def _account_state(self, acc: dict, now: float) -> dict:
        expiry = acc.get("cookie_expiry")
        remain_days = ((expiry - now) / 86400) if expiry else None
        last_ok = acc.get("last_ok_at")
        stale_days = ((now - last_ok) / 86400) if last_ok else None

        if expiry and expiry <= now:
            state, text = "expired", "登录已失效"
        elif remain_days is not None and remain_days < EXPIRING_DAYS:
            state, text = "expiring", f"即将过期 · 剩余 {max(0, remain_days):.0f} 天"
        elif stale_days is not None and stale_days >= STALE_DAYS:
            state, text = "stale", f"已 {stale_days:.0f} 天没登录"
        elif remain_days is not None:
            # 有明确的过期时间，就算没记录过登录也算可用
            state, text = "ok", f"正常 · {max(0, remain_days):.0f} 天"
        elif last_ok:
            state, text = "ok", "正常"
        else:
            state, text = "never", "未登录"
        return {"state": state, "state_text": text,
                "remain_days": remain_days, "stale_days": stale_days}

    def add_account(self, platform: str, label: str) -> dict:
        if platform not in {p["key"] for p in all_platforms()}:
            raise ValueError(f"未知平台: {platform}")
        account_id = uuid.uuid4().hex[:10]
        profile = Path(self.cfg.home) / "profiles" / f"{platform}-{account_id}"
        profile.mkdir(parents=True, exist_ok=True)
        acc = {
            "id": account_id, "platform": platform,
            "label": (label or "").strip() or platform_info(platform)["name"],
            "profile_dir": str(profile), "enabled": 1,
            "last_ok_at": None, "last_check_at": None,
            "cookie_expiry": None, "status": "never", "note": "",
            "created_at": time.time(),
        }
        self.store.upsert_account(acc)
        return acc

    def delete_account(self, account_id: str) -> None:
        self.pool.close(account_id)
        self.store.delete_account(account_id)

    def open_account(self, account_id: str, url: str = "") -> dict:
        acc = self.store.get_account(account_id)
        if not acc:
            raise ValueError("账号不存在")
        sess = self.pool.get(acc)
        sess.ensure_started(url or sess.home_url)
        self.store.touch_account(account_id, status="opened")
        return {"ok": True, "url": sess.current_url()}

    def close_account(self, account_id: str) -> dict:
        self.pool.close(account_id)
        return {"ok": True}

    def check_account(self, account_id: str, go_online: bool = False) -> dict:
        """两个信号取严的：本地 cookie 过期时间，以及（可选）真实页面探活。"""
        acc = self.store.get_account(account_id)
        if not acc:
            raise ValueError("账号不存在")
        expiry = read_cookie_expiry(Path(acc["profile_dir"]))
        state = self._account_state({**acc, "cookie_expiry": expiry}, time.time())

        if go_online:
            try:
                sess = self.pool.get(acc)
                sess.ensure_started(platform_info(acc["platform"]).get("login", ""))
                html = str(sess.evaluate("document.documentElement.innerText.slice(0,4000)") or "")
                markers = platform_info(acc["platform"]).get("logged_out_markers") or []
                if any(m and m in html for m in markers):
                    state = {"state": "expired", "state_text": "页面显示未登录",
                             "remain_days": state["remain_days"], "stale_days": state["stale_days"]}
                    expiry = 0.0
                else:
                    self.store.touch_account(account_id, status="ok", last_ok=True)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc), **state}

        self.store.touch_account(account_id, status=state["state"],
                                 cookie_expiry=expiry if expiry else None)
        return {"ok": True, **state, "cookie_expiry": expiry}

    # -------------------------------------------------------------- 发布
    def publish_create(self, account_id: str, files: list[str],
                       meta: dict) -> dict:
        acc = self.store.get_account(account_id)
        if not acc:
            raise ValueError("账号不存在")
        from .publish import standards

        problems = standards.check(meta, standards.load(self.cfg))
        if problems:
            raise ValueError("不符合发布标准：" + "；".join(problems))

        digest = snapshot_hash(files, meta)
        record = {
            "id": uuid.uuid4().hex[:12],
            "account_id": account_id,
            "job_id": "",
            "platform": acc["platform"],
            "snapshot_hash": digest,
            "state": "draft",
            "remote_ref": "",
            "result": {"files": files, "meta": meta},
            "created_at": time.time(),
        }
        self.store.upsert_publish(record)
        return record

    def publish_approve(self, record_id: str, digest: str) -> dict:
        rec = self.store.get_publish(record_id)
        if not rec:
            raise ValueError("发布记录不存在")
        if rec["snapshot_hash"] != digest:
            raise ValueError("快照不一致：文件或内容在批准之后改过了，请重新准备")
        if rec["state"] not in ("draft", "failed"):
            raise ValueError(f"当前状态不能发布：{rec['state']}")

        payload = rec.get("result") or {}
        files = payload.get("files") or []
        meta = payload.get("meta") or {}
        label = meta.get("title") or (Path(files[0]).stem if files else "发布")
        task = self.manager.add_capability(
            "publish.browser",
            {"files": files, "account_id": rec["account_id"]},
            {"meta": meta, "account_id": rec["account_id"], "verify": True},
            title=f"发布 · {label}",
            idem=make_idem("publish.browser", {"files": files},
                           {"account_id": rec["account_id"], "meta": meta}),
        )
        rec["job_id"] = task["id"]
        rec["state"] = "approved"
        self.store.upsert_publish(rec)
        return {"record": rec, "job": self.manager.public(task)}

    def publish_list(self, account_id: str = "") -> list[dict]:
        return self.store.list_publish(account_id)

    # -------------------------------------------------------------- 用量
    def usage(self) -> dict:
        limit = float(self.cfg.get("monthlyBudget") or 0)
        spent = self.store.month_cost()
        return {"spent": round(spent, 2), "budget": limit,
                "remaining": round(max(0.0, limit - spent), 2) if limit > 0 else None}

    # -------------------------------------------------------------- 连接
    def connect_info(self, port: int | None = None) -> dict:
        """给 Agent 看的连接信息。端口必须跟着当前服务走：
        桌面版由 Tauri 壳分配随机端口，写死 8765 会让复制出去的配置连不上。"""
        port = int(port or 8765)
        return {
            "local": f"http://127.0.0.1:{port}",
            "lan": _lan_addresses(port),
            "token_set": bool(self.cfg.get("apiToken")),
            "token_preview": _mask(self.cfg.get("apiToken") or ""),
        }


def _mask(token: str) -> str:
    if not token:
        return ""
    return token[:4] + "…" + token[-4:] if len(token) > 10 else "…"


def _lan_addresses(port: int = 8765) -> list[dict]:
    """列出这台机器上别的 Agent 可能连到的地址。

    按用途标一下，用户不用自己去猜哪个是 Tailscale、哪个是 docker 网桥。
    """
    import socket

    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    items = [{"label": _ip_kind(ip), "url": f"http://{ip}:{port}", "ip": ip}
             for ip in found if ip != "0.0.0.0"]
    order = {"Tailscale": 0, "内网": 1, "本机网段": 2, "其他": 3}
    items.sort(key=lambda item: (order.get(item["label"], 9), item["ip"]))
    return items


def _ip_kind(ip: str) -> str:
    parts = ip.split(".")
    if len(parts) != 4:
        return "其他"
    first, second = int(parts[0]), int(parts[1])
    if first == 100 and 64 <= second <= 127:
        return "Tailscale"
    if first == 10 or (first == 192 and second == 168) or (first == 172 and 16 <= second <= 31):
        # docker / WSL 的网桥也落在这个范围里，但至少是内网可达的
        return "内网"
    return "其他"
