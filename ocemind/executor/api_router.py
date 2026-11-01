"""执行器的两组接口。

- `/api/exec/*`：给本机界面用，不受令牌限制（它只监听 127.0.0.1）
- `/api/v1/*`：给 Agent 用，必须带 Bearer 令牌，**看不到设置，也删不了账号**

分开是刻意的：Agent 能做的事，不等于人能做的事。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from .engines import registry
from .facade import Executor


def build_ui_router(executor: Executor) -> APIRouter:
    router = APIRouter(prefix="/api/exec")

    # ---------------------------------------------------------- 能力/用量
    @router.get("/capabilities")
    def capabilities():
        return {"capabilities": executor.capabilities(),
                **executor.capability_health()}

    @router.get("/usage")
    def usage():
        return executor.usage()

    # ------------------------------------------------------ 模型与接口
    @router.get("/models")
    def models():
        return executor.models()

    @router.post("/models/{capability_id}")
    def set_model(capability_id: str, body: dict = Body(...)):
        try:
            return executor.set_model(capability_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/local")
    def local():
        return executor.local_status()

    @router.post("/local/install")
    def install_local(body: dict = Body(...)):
        try:
            return executor.install_local(str(body.get("engine") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -------------------------------------------------------------- 任务
    @router.get("/jobs")
    def jobs():
        return {"jobs": executor.list_jobs()}

    @router.post("/jobs")
    def submit(body: dict = Body(...)):
        capability = body.get("capability") or body.get("kind") or ""
        if registry.get(capability) is None:
            raise HTTPException(status_code=400, detail=f"未知能力: {capability}")
        try:
            job = executor.submit(capability, body.get("inputs") or {},
                                  body.get("params") or {},
                                  title=body.get("title") or "",
                                  project=body.get("project") or "",
                                  sequence=body.get("sequence") or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job": job}

    @router.get("/jobs/{job_id}")
    def job(job_id: str):
        found = executor.get_job(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="任务不存在")
        return found

    @router.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        executor.cancel(job_id)
        return {"ok": True}

    # -------------------------------------------------------------- 产出
    @router.get("/artifacts")
    def artifacts(project: str = "", status: str = "", limit: int = 200,
                  ungrouped: int = 0):
        return executor.artifacts(project=project, status=status, limit=limit,
                                  ungrouped=bool(ungrouped))

    @router.post("/artifacts/claim")
    def claim_artifacts(body: dict = Body(...)):
        """认领：给人工跑出来的产出补项目 / 序列。"""
        try:
            return executor.claim_artifacts(
                [str(i) for i in (body.get("ids") or [])],
                str(body.get("project") or ""),
                str(body.get("sequence") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/artifacts/index")
    def index_history():
        return executor.index_history()

    @router.post("/artifacts/sync")
    def sync_artifacts(body: dict = Body(default={})):
        return executor.sync_artifacts(
            task_id=str(body.get("task_id") or ""),
            ids=[str(i) for i in (body.get("ids") or [])],
        )

    # ------------------------------------------------------------ 飞书协同
    @router.get("/feishu")
    def feishu_status():
        return executor.feishu_status()

    @router.post("/feishu")
    def feishu_save(body: dict = Body(...)):
        return executor.feishu_save(body)

    @router.post("/feishu/test")
    def feishu_test():
        return executor.feishu_test()

    # -------------------------------------------------------------- 账号
    @router.get("/platforms")
    def platforms():
        return {"platforms": executor.platforms()}

    @router.get("/accounts")
    def accounts():
        return {"accounts": executor.accounts()}

    @router.post("/accounts")
    def add_account(body: dict = Body(...)):
        try:
            return executor.add_account(body.get("platform", ""),
                                        body.get("label", ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/accounts/{account_id}")
    def delete_account(account_id: str):
        executor.delete_account(account_id)
        return {"ok": True}

    @router.post("/accounts/{account_id}/open")
    def open_account(account_id: str, body: dict = Body(default={})):
        try:
            return executor.open_account(account_id, body.get("url", ""))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"浏览器启动失败：{exc}") from exc

    @router.post("/accounts/{account_id}/close")
    def close_account(account_id: str):
        return executor.close_account(account_id)

    @router.post("/accounts/{account_id}/check")
    def check_account(account_id: str, body: dict = Body(default={})):
        try:
            return executor.check_account(account_id, bool(body.get("online")))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/frame.jpg")
    def frame(account_id: str):
        acc = executor.store.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        sess = executor.pool.get(acc)
        if not sess.alive:
            # 没打开就返回 204，前端显示"点开始"而不是一张黑图
            return Response(status_code=204)
        data = sess.screenshot()
        if not data:
            return Response(status_code=204)
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @router.post("/accounts/{account_id}/act")
    def act(account_id: str, body: dict = Body(...)):
        acc = executor.store.get_account(account_id)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        sess = executor.pool.get(acc)
        kind = body.get("kind")
        try:
            if kind == "click":
                sess.click(float(body.get("x") or 0), float(body.get("y") or 0))
            elif kind == "type":
                sess.type_text(str(body.get("text") or ""))
            elif kind == "key":
                sess.press(str(body.get("key") or "Enter"))
            elif kind == "scroll":
                sess.scroll(int(body.get("delta") or 0))
            elif kind == "goto":
                sess.goto(str(body.get("url") or ""))
            else:
                raise HTTPException(status_code=400, detail="未知操作")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True, "url": sess.current_url()}

    # -------------------------------------------------------------- 发布
    @router.get("/publish")
    def publish_list(account_id: str = ""):
        return {"records": executor.publish_list(account_id)}

    @router.post("/publish")
    def publish_create(body: dict = Body(...)):
        try:
            return executor.publish_create(body.get("account_id", ""),
                                           body.get("files") or [],
                                           body.get("meta") or {})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/publish/{record_id}/approve")
    def publish_approve(record_id: str, body: dict = Body(...)):
        try:
            return executor.publish_approve(record_id, body.get("snapshot_hash", ""))
        except ValueError as exc:
            status = 409 if "快照" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    # -------------------------------------------------------------- 连接
    @router.get("/connect")
    def connect(request: Request):
        return executor.connect_info(request.url.port)

    @router.post("/connect/token")
    def rotate_token():
        token = "ocm_" + secrets.token_urlsafe(24)
        executor.cfg.update({"apiToken": token})
        return {"token": token}

    @router.post("/connect/test")
    def test_connect(request: Request):
        """真的走一遍 v1 接口，而不是只回一句"连接正常"。"""
        import urllib.request

        token = executor.cfg.get("apiToken") or ""
        if not token:
            return {"ok": False, "error": "还没有生成令牌"}
        port = request.url.port or 8765
        url = f"http://127.0.0.1:{port}/api/v1/capabilities"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                import json as _json

                data = _json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "count": len(data.get("capabilities") or [])}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    return router


def build_v1_router(executor: Executor) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def require(request: Request) -> None:
        token = executor.cfg.get("apiToken") or ""
        header = request.headers.get("authorization", "")
        if not token:
            raise HTTPException(status_code=401, detail="执行器还没有生成访问令牌")
        if header != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="令牌无效")

    @router.get("/capabilities")
    def capabilities(request: Request):
        require(request)
        return {"capabilities": executor.capabilities()}

    @router.get("/jobs")
    def jobs(request: Request, limit: int = 50):
        require(request)
        return {"jobs": executor.list_jobs(limit)}

    @router.post("/jobs")
    def submit(request: Request, body: dict = Body(...)):
        require(request)
        capability = body.get("capability") or body.get("kind") or ""
        if registry.get(capability) is None:
            raise HTTPException(status_code=400, detail=f"未知能力: {capability}")
        if capability == "publish.browser":
            raise HTTPException(status_code=403,
                                detail="发布必须走 /publish 并由人批准，不能直接提交")
        try:
            return {"job": executor.submit(capability, body.get("inputs") or {},
                                           body.get("params") or {},
                                           title=body.get("title") or "",
                                           project=body.get("project") or "",
                                           sequence=body.get("sequence") or "",
                                           source=str(body.get("source") or "agent"),
                                           order_id=str(body.get("order_id") or ""))}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs/{job_id}")
    def job(request: Request, job_id: str):
        require(request)
        found = executor.get_job(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="任务不存在")
        return found

    @router.post("/jobs/{job_id}/cancel")
    def cancel(request: Request, job_id: str):
        require(request)
        executor.cancel(job_id)
        return {"ok": True}

    @router.get("/accounts")
    def accounts(request: Request):
        require(request)
        return {"accounts": [
            {k: a[k] for k in ("id", "platform", "platform_name", "label", "state",
                               "state_text", "running") if k in a}
            for a in executor.accounts()
        ]}

    @router.post("/publish")
    def publish_create(request: Request, body: dict = Body(...)):
        require(request)
        try:
            return executor.publish_create(body.get("account_id", ""),
                                           body.get("files") or [],
                                           body.get("meta") or {})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/publish/{record_id}/approve")
    def publish_approve(request: Request, record_id: str, body: dict = Body(...)):
        require(request)
        try:
            return executor.publish_approve(record_id, body.get("snapshot_hash", ""))
        except ValueError as exc:
            status = 409 if "快照" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @router.get("/files")
    def files(request: Request, path: str):
        """只能取输出目录里的文件，读不到别的任何东西。"""
        require(request)
        target = Path(path).resolve()
        roots = [Path(executor.cfg.get("defaultDir") or "").resolve(),
                 (Path(executor.cfg.home) / "transcripts").resolve(),
                 (Path(executor.cfg.home) / "documents").resolve(),
                 (Path(executor.cfg.home) / "audio").resolve(),
                 (Path(executor.cfg.home) / "images").resolve()]
        if not any(str(target).startswith(str(r)) for r in roots if str(r) != "."):
            raise HTTPException(status_code=403, detail="只能读取输出目录内的文件")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return Response(content=target.read_bytes(),
                        media_type="application/octet-stream",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{os.path.basename(target)}"'})

    return router
