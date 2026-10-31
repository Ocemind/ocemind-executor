"""HTTP 接口层（本地 127.0.0.1 服务）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from xiaoe_dl import __version__, auth, core, douyin, paths
from xiaoe_dl.cookies import (cookie_status, find_cookie_path, find_ko_token,
                              load_cookies, parse_netscape)
from xiaoe_dl.links import first_url

from . import tools
from .config import Config
from .course_auth import LoginSession
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
    project: str = ""
    sequence: str = ""


class ProbeBody(BaseModel):
    url: str


class SettingsBody(BaseModel):
    values: dict


class CookieImportBody(BaseModel):
    text: str = ""
    path: str = ""


class LoginBody(BaseModel):
    app_id: str = ""
    alive_id: str = ""


def course_auth_status(cfg: Config) -> dict:
    """当前课程登录态。只读本地文件，不联网。"""
    path = find_cookie_path(cfg.get("courseCookieFile") or None)
    count, message = cookie_status(path) if path else (None, None)
    return {
        "path": path or "",
        "count": count or 0,
        "status": message or "",
        "has_file": bool(path),
        "canonical": os.path.join(paths.app_dir(), "cookies.txt"),
        "playwright": auth.playwright_available(),
    }


def _needs_cookie(cfg: Config, app_id: str) -> bool:
    path = find_cookie_path(cfg.get("courseCookieFile") or None)
    if not path:
        return True
    try:
        return find_ko_token(load_cookies(path)[1], app_id) is None
    except Exception:  # noqa: BLE001
        return True


def _reveal(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    if p.is_dir():
        os.startfile(str(p))  # noqa: S606
    else:
        subprocess.Popen(f'explorer /select,"{p}"')


def create_app(cfg: Config, manager: TaskManager) -> FastAPI:
    app = FastAPI(title="Ocemind 内容执行器", docs_url=None, redoc_url=None)
    # Tauri 壳把界面放在 tauri.localhost（Windows）/ tauri://localhost（Linux），
    # 接口仍在 127.0.0.1，所以只放行本机和 Tauri 自己的源。别写成 "*"。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=(r"^(https?://(tauri\.localhost|127\.0\.0\.1|localhost)"
                            r"(:\d+)?|tauri://localhost)$"),
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=600,
    )
    login = LoginSession()

    # 执行器：账号浏览器 + 能力任务 + Agent 接口。下载器原有的路由一个没动。
    from xiaoe_dl.executor.api_router import build_ui_router, build_v1_router
    from xiaoe_dl.executor.facade import Executor

    executor = Executor(cfg, manager)
    app.state.executor = executor
    app.include_router(build_ui_router(executor))
    app.include_router(build_v1_router(executor))

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    # 静态资源放在 web/static：这样 index.html 里的 `static/...` 在两种
    # 加载方式下都成立——FastAPI 挂在 /static，Tauri 直接按 frontendDist
    # 的相对路径取 web/static/...。
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/health")
    def health():
        """Tauri 壳用它判断本地服务是否已经起来。不碰磁盘、不返回设置。"""
        return {"ok": True, "service": "ocemind", "version": __version__}

    @app.post("/api/shutdown")
    def shutdown(request: Request):
        """让 Tauri 壳能请服务自己退出，走 uvicorn 的正常收尾。

        只接受带本次启动随机串的请求；端口在 127.0.0.1，别的本机进程
        没有这个串也调不动。真正退出前会关账号浏览器、停任务队列。
        """
        token = os.environ.get("OCEMIND_SHUTDOWN_TOKEN", "")
        if not token or request.headers.get("X-Ocemind-Shutdown") != token:
            raise HTTPException(status_code=403, detail="forbidden")
        server = getattr(request.app.state, "server", None)
        if server is None:
            raise HTTPException(status_code=503, detail="server not ready")
        server.should_exit = True
        return {"ok": True}

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
                               body.connections, body.title,
                               project=body.project, sequence=body.sequence)
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
        raw = (body.url or "").strip()
        url = first_url(raw) or raw
        if core.is_course_url(url):
            # 探测框每 650 ms 触发一次，这里绝不能有网络等待：
            # 只做本地 cookie 检查，标题等下载开始后由 title 事件补上。
            app_id, alive_id = core.parse_course_url(url)
            if not alive_id:
                # 课程页 / 视频页的普通链接取不到回放 ID，加进队列也下不了。
                return {"ok": False,
                        "error": "这条链接里没有回放 ID（l_ 开头），加密下载只支持课程回放链接"}
            return {
                "ok": True,
                "kind": "course",
                "title": "",
                "app_id": app_id or "",
                "alive_id": alive_id or "",
                "needs_cookie": _needs_cookie(cfg, app_id or ""),
                "cookie": course_auth_status(cfg),
                "heights": [],
            }
        if douyin.is_douyin_url(url):
            # 抖音不走直链兜底：分享页本身是一张网页，探测成 file 会把
            # HTML 当成视频加进队列
            info = douyin.probe_url(url)
            if not info.get("ok"):
                return JSONResponse({"ok": False, "error": info.get("error")},
                                    status_code=200)
            return info
        info = probe_video(url, cfg)
        if not info.get("ok"):
            direct = probe_http(url, proxies=cfg.proxies())
            if direct.get("ok"):
                return {"ok": True, "kind": "file", "title": direct["filename"],
                        "filename": direct["filename"], "size": direct["size"],
                        "supports_range": direct["supports_range"]}
            return JSONResponse({"ok": False, "error": info.get("error")}, status_code=200)
        return {"ok": True, "kind": "video", **info}

    # ---------------- 课程登录态 ----------------

    @app.get("/api/course/auth")
    def course_auth():
        return course_auth_status(cfg)

    @app.post("/api/course/cookies/import")
    def import_cookies(body: CookieImportBody):
        text = body.text or ""
        if not text and body.path:
            try:
                text = Path(body.path).read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                raise HTTPException(status_code=400, detail=f"读不了这个文件：{exc}") from exc
        parsed = parse_netscape(text)
        if not parsed:
            raise HTTPException(
                status_code=400,
                detail="没解析出任何 cookie。需要浏览器扩展导出的 Netscape 格式 cookies.txt")
        dest = os.path.join(paths.app_dir(), "cookies.txt")
        try:
            auth.save_cookies(parsed, dest)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc
        status = course_auth_status(cfg)
        status["imported"] = len(parsed)
        return status

    @app.post("/api/course/login")
    def course_login(body: LoginBody):
        try:
            return login.start(body.app_id, body.alive_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/course/login/status")
    def course_login_status():
        return login.status()

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
