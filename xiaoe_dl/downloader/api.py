"""HTTP 接口层（本地 127.0.0.1 服务）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import tools
from .config import Config
from .http_downloader import probe_http
from .manager import TaskManager
from .ytdlp_engine import probe as probe_video

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class AddTaskBody(BaseModel):
    url: str
    kind: str = "video"
    quality: int = 0
    dir: str = ""
    connections: int = 0
    title: str = ""


class ProbeBody(BaseModel):
    url: str


class SettingsBody(BaseModel):
    values: dict


def _reveal(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    if p.is_dir():
        os.startfile(str(p))  # noqa: S606
    else:
        subprocess.Popen(f'explorer /select,"{p}"')


def create_app(cfg: Config, manager: TaskManager) -> FastAPI:
    app = FastAPI(title="TDM Lite", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/system")
    def system():
        versions = tools.tool_versions(cfg.get("ytdlpPath") or "", cfg.get("ffmpegPath") or "")
        return {"settings": dict(cfg.data), "tools": versions}

    @app.get("/api/tasks")
    def list_tasks():
        return {"tasks": manager.list()}

    @app.post("/api/tasks")
    def add_task(body: AddTaskBody):
        try:
            task = manager.add(body.url, body.kind, body.quality, body.dir,
                               body.connections, body.title)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": manager.public(task)}

    @app.post("/api/tasks/{task_id}/{action}")
    def task_action(task_id: str, action: str):
        if action == "pause":
            manager.pause(task_id)
        elif action == "resume":
            manager.resume(task_id)
        elif action == "cancel":
            manager.cancel(task_id)
        elif action == "retry":
            manager.retry(task_id)
        elif action == "remove":
            manager.remove(task_id)
        elif action == "open":
            task = manager.get(task_id)
            if task and task.get("path"):
                _reveal(task["path"])
        else:
            raise HTTPException(status_code=400, detail="未知操作")
        return {"ok": True}

    @app.post("/api/probe")
    def probe(body: ProbeBody):
        info = probe_video(body.url, cfg)
        if not info.get("ok"):
            direct = probe_http(body.url, proxies=cfg.proxies())
            if direct.get("ok"):
                return {"ok": True, "kind": "file", "title": direct["filename"],
                        "filename": direct["filename"], "size": direct["size"],
                        "supports_range": direct["supports_range"]}
            return JSONResponse({"ok": False, "error": info.get("error")}, status_code=200)
        return {"ok": True, "kind": "video", **info}

    @app.get("/api/settings")
    def get_settings():
        return dict(cfg.data)

    @app.post("/api/settings")
    def set_settings(body: SettingsBody):
        return cfg.update(body.values)

    @app.post("/api/reveal")
    def reveal(body: SettingsBody):  # 复用 {values: {path: ...}}
        _reveal(body.values.get("path", ""))
        return {"ok": True}

    return app
