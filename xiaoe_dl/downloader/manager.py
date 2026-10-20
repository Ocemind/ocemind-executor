"""任务队列：并发调度、进度采样、暂停/续传、持久化。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from xiaoe_dl import core

from . import config as config_mod
from .course_engine import CourseDownloader, tmp_dir_for
from .http_downloader import SegmentedDownload, probe_http
from .ytdlp_engine import VideoDownloader

KINDS = ("video", "file", "course")

STATUS_TEXT = {
    "queued": "排队中",
    "downloading": "下载中",
    "merging": "合并中",
    "paused": "已暂停",
    "done": "已完成",
    "error": "失败",
    "cancelled": "已取消",
}

FILE_EXTS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".exe", ".msi", ".dmg",
    ".pdf", ".apk", ".iso", ".img", ".bin", ".whl", ".gguf", ".safetensors", ".pt",
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".png", ".jpg", ".jpeg", ".webp",
    ".mkv", ".mp4", ".avi", ".mov", ".webm", ".csv", ".xlsx", ".docx", ".pptx",
}


def _now() -> float:
    return time.time()


def _unique_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        cand = directory / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    return directory / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"


class TaskManager:
    def __init__(self, cfg: config_mod.Config, start_scheduler: bool = True) -> None:
        self.cfg = cfg
        self.lock = threading.RLock()
        self.tasks: dict[str, dict] = {}
        self.active: set[str] = set()
        self._dirty = False
        self._last_save = 0.0
        self._running = True
        self._load()
        # 测试里要能只测数据层，不让后台线程把手一动就真去下载
        if start_scheduler:
            threading.Thread(target=self._scheduler, daemon=True).start()

    # ---------------- 持久化 ----------------

    def _load(self) -> None:
        path = self.cfg.tasks_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("status") in ("downloading", "merging"):
                item["status"] = "paused"
            item["speed"] = 0
            item["eta"] = None
            self.tasks[item["id"]] = item

    def _save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        if not force and time.time() - self._last_save < 3:
            return
        with self.lock:
            payload = []
            for task in self.tasks.values():
                payload.append({k: v for k, v in task.items() if not k.startswith("_")})
            path = self.cfg.tasks_path
            tmp = path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
                self._dirty = False
                self._last_save = time.time()
            except OSError:
                pass

    # ---------------- 对外接口 ----------------

    def add(self, url: str, kind: str, quality: int, directory: str,
            connections: int, title: str = "") -> dict:
        url = (url or "").strip()
        if not url:
            raise ValueError("链接为空")
        if "://" not in url:
            url = "https://" + url
        directory = directory or self.cfg.get("defaultDir")
        kind = kind if kind in KINDS else self._guess_kind(url)
        course_key = self._course_key(url, directory) if kind == "course" else ""
        if kind == "course" and not course_key:
            # 小鹅通域名下的普通视频页（v_ 开头）不是回放。放它进队列只会
            # 让任务跑一趟再报错，不如当场说清楚。
            raise ValueError(
                "这条链接里没有回放 ID（l_ 开头），小鹅通下载只支持课程回放链接")
        if course_key:
            with self.lock:
                for other in self.tasks.values():
                    if other.get("course_key") != course_key:
                        continue
                    if other.get("status") in ("done", "cancelled"):
                        continue
                    raise ValueError(
                        f"这门课已经在队列里了：{other.get('title') or other['id']}")
        task = {
            "id": uuid.uuid4().hex[:12],
            "url": url,
            "kind": kind,
            "status": "queued",
            "title": title or url,
            "filename": "",
            "dir": directory,
            "size": 0,
            "downloaded": 0,
            "speed": 0,
            "eta": None,
            "progress": 0.0,
            "connections": int(connections or self.cfg.get("defaultConnections")),
            "quality": int(quality or 0),
            "error": "",
            "path": "",
            "thumbnail": "",
            "duration": None,
            "uploader": "",
            "is_playlist": False,
            "entry_count": 1,
            # 课程任务：进度按分片数算，字节数那套采样对它不适用
            "segments_done": 0,
            "segments_total": 0,
            "stage": "",
            "course_key": course_key,
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self.lock:
            self.tasks[task["id"]] = task
            self._dirty = True
        self._save(force=True)
        return task

    def get(self, task_id: str) -> dict | None:
        with self.lock:
            return self.tasks.get(task_id)

    @staticmethod
    def public(task: dict) -> dict:
        """去掉 _engine/_control 等运行时字段，只留可序列化的部分。"""
        return {k: v for k, v in task.items() if not k.startswith("_")}

    def list(self) -> list[dict]:
        with self.lock:
            return [self.public(t) for t in self.tasks.values()]

    @staticmethod
    def _handle(task: dict):
        """视频任务是 _control，直链任务是 _engine。"""
        return task.get("_control") or task.get("_engine")

    def pause(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        with self.lock:
            task["_pause_requested"] = True
            if task["status"] in ("queued",):
                task["status"] = "paused"
            control = self._handle(task)
        if control:
            control.stop()
        self._dirty = True

    def resume(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        with self.lock:
            if task["status"] not in ("paused", "error", "cancelled"):
                return
            task["_pause_requested"] = False
            task["_cancel_requested"] = False
            task["error"] = ""
            task["status"] = "queued"
        self._dirty = True

    def cancel(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        with self.lock:
            task["_cancel_requested"] = True
            task["_pause_requested"] = False
            control = self._handle(task)
            if task["status"] in ("queued", "paused", "error"):
                self._remove_files(task)
                task["status"] = "cancelled"
                task["speed"] = 0
                task["eta"] = None
        if control:
            control.stop()
        self._dirty = True

    def retry(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        with self.lock:
            if task_id in self.active:
                return
            task["_cancel_requested"] = False
            task["_pause_requested"] = False
            task["downloaded"] = 0
            task["progress"] = 0.0
            task["speed"] = 0
            task["eta"] = None
            task["error"] = ""
            task["segments_done"] = 0
            task["stage"] = ""
            task["status"] = "queued"
        self._dirty = True

    def remove(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        with self.lock:
            task["_cancel_requested"] = True
            control = self._handle(task)
        if control:
            control.stop()
        for _ in range(20):
            if task_id not in self.active:
                break
            time.sleep(0.1)
        if task.get("kind") == "course":
            # 课程临时目录是按任务 id 建的，移除任务后不会有人再来续传
            self._remove_files(task)
        with self.lock:
            self.tasks.pop(task_id, None)
        self._dirty = True
        self._save(force=True)

    # ---------------- 调度 ----------------

    def _scheduler(self) -> None:
        while self._running:
            time.sleep(0.4)
            try:
                self._sample_speeds()
                max_conc = int(self.cfg.get("maxConcurrent") or 3)
                with self.lock:
                    queued = [t for t in self.tasks.values() if t["status"] == "queued"]
                    slots = max_conc - len(self.active)
                for task in queued[: max(0, slots)]:
                    self._start(task["id"])
                self._save()
            except Exception:  # noqa: BLE001
                pass

    def _sample_speeds(self) -> None:
        now = _now()
        with self.lock:
            for tid in list(self.active):
                task = self.tasks.get(tid)
                if not task:
                    continue
                if task.get("kind") == "course":
                    # 课程进度来自分片回调。这里若照常按字节数采样，
                    # 会把刚写好的百分比用 0 覆盖回去，进度条来回跳。
                    continue
                engine = task.get("_engine")
                if engine is not None and task.get("kind") == "file":
                    downloaded = engine.downloaded()
                    total = engine.total or task.get("size") or 0
                    task["downloaded"] = downloaded
                    if total:
                        task["size"] = total
                else:
                    downloaded = task.get("downloaded") or 0
                    total = task.get("size") or 0
                last_bytes = task.get("_last_bytes", 0)
                last_time = task.get("_last_time") or now
                dt = now - last_time
                if dt >= 0.4:
                    speed = max(0.0, (downloaded - last_bytes) / dt)
                    prev = task.get("speed") or 0
                    task["speed"] = speed if prev == 0 else prev * 0.6 + speed * 0.4
                    task["_last_bytes"] = downloaded
                    task["_last_time"] = now
                speed = task.get("speed") or 0
                if total:
                    task["progress"] = round(min(100.0, downloaded * 100.0 / total), 2)
                    task["eta"] = int((total - downloaded) / speed) if speed > 1000 else None
                else:
                    task["eta"] = None

    def _start(self, task_id: str) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task["status"] != "queued":
                return
            task["status"] = "downloading"
            task["error"] = ""
            task["_last_bytes"] = task.get("downloaded") or 0
            task["_last_time"] = _now()
            self.active.add(task_id)
        threading.Thread(target=self._run, args=(task_id,), daemon=True).start()

    def _run(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        result: dict
        try:
            if task["kind"] == "course":
                # 课程链接交给 yt-dlp 只会白跑一趟，别回退
                result = self._run_course(task)
            elif task["kind"] == "video":
                result = self._run_video(task)
                if not result.get("ok") and not result.get("cancelled"):
                    msg = (result.get("error") or "").lower()
                    if "unsupported url" in msg or "no suitable" in msg:
                        result = self._run_file(task)
            else:
                result = self._run_file(task)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            with self.lock:
                task.pop("_engine", None)
                task.pop("_control", None)
                self.active.discard(task_id)

        with self.lock:
            task["speed"] = 0
            task["eta"] = None
            task["updated_at"] = _now()
            if result.get("ok"):
                task["status"] = "done"
                path = result.get("path") or ""
                task["path"] = path
                if path:
                    task["filename"] = os.path.basename(path)
                    try:
                        task["size"] = os.path.getsize(path)
                        task["downloaded"] = task["size"]
                    except OSError:
                        pass
                task["progress"] = 100.0
            elif result.get("cancelled"):
                if task.get("_cancel_requested"):
                    self._remove_files(task)
                    task["status"] = "cancelled"
                else:
                    task["status"] = "paused"
            else:
                task["status"] = "error"
                task["error"] = result.get("error") or "未知错误"
        self._dirty = True
        self._save(force=True)

    # ---------------- 引擎 ----------------

    def _run_video(self, task: dict) -> dict:
        engine = VideoDownloader(self.cfg)
        with self.lock:
            task["_control"] = engine

        def on_progress(info: dict) -> None:
            with self.lock:
                status = info.get("status")
                if status == "merging":
                    task["status"] = "merging"
                    task["speed"] = 0
                    return
                if status == "downloading":
                    task["status"] = "downloading"
                if info.get("tmpfilename"):
                    task["_tmp_path"] = info["tmpfilename"]
                if info.get("filename") and not task.get("filename"):
                    task["filename"] = os.path.basename(info["filename"])
                downloaded = info.get("downloaded") or 0
                total = info.get("total") or task.get("size") or 0
                if downloaded:
                    task["downloaded"] = downloaded
                if total:
                    task["size"] = total
                    task["progress"] = round(min(100.0, downloaded * 100.0 / total), 2)
                speed = info.get("speed")
                if speed:
                    task["speed"] = float(speed)
                eta = info.get("eta")
                if eta is not None:
                    task["eta"] = int(eta)

        return engine.run(
            task["url"], Path(task["dir"]), int(task.get("quality") or 0), on_progress,
        )

    def _run_file(self, task: dict) -> dict:
        directory = Path(task["dir"])
        directory.mkdir(parents=True, exist_ok=True)

        info = probe_http(task["url"], headers={}, proxies=self.cfg.proxies())
        if not info.get("ok"):
            return {"ok": False, "error": f"探测失败: {info.get('error')}"}

        filename = task.get("filename") or info.get("filename") or "download.bin"
        candidate = directory / filename
        # 续传时沿用原文件名，避免 _unique_path 生成 (1) 导致分片文件对不上
        if candidate.exists() or Path(str(candidate) + ".tdmpart").exists():
            dest = candidate
        else:
            dest = _unique_path(directory, filename)
        with self.lock:
            task["filename"] = dest.name
            task["size"] = info.get("size") or 0
            task["progress"] = 0.0
            task["downloaded"] = 0
            task["_last_bytes"] = 0
            task["_last_time"] = _now()

        rate = int(self.cfg.get("maxRateKB") or 0) * 1024
        min_chunk = int(float(self.cfg.get("minChunkMB") or 2) * 1024 * 1024)
        engine = SegmentedDownload(
            url=task["url"],
            dest=dest,
            connections=int(task.get("connections") or 8),
            rate_bytes=rate,
            proxies=self.cfg.proxies(),
            min_chunk=min_chunk,
        )
        with self.lock:
            task["_engine"] = engine
        result = engine.run()
        if result.get("ok"):
            with self.lock:
                task["filename"] = Path(result["path"]).name
        return result

    def _run_course(self, task: dict) -> dict:
        def on_progress(event: dict) -> None:
            stage = event.get("stage")
            with self.lock:
                if stage == "resolving":
                    task["stage"] = "解析中"
                elif stage == "title":
                    title = event.get("title")
                    if title:
                        task["title"] = title
                elif stage == "output":
                    path = event.get("path")
                    if path:
                        task["filename"] = os.path.basename(path)
                elif stage == "segments":
                    task["segments_total"] = int(event.get("total") or 0)
                    task["stage"] = "下载分片"
                elif stage == "downloading":
                    done = int(event.get("done") or 0)
                    total = int(event.get("total") or 0)
                    task["segments_done"] = done
                    if total:
                        task["segments_total"] = total
                        task["progress"] = round(min(100.0, done * 100.0 / total), 2)
                elif stage == "merging":
                    task["status"] = "merging"
                    task["stage"] = "合并中"
                    task["segments_done"] = task["segments_total"]
                elif stage == "done":
                    task["stage"] = "完成"

        engine = CourseDownloader(self.cfg, task["id"], on_progress=on_progress)
        with self.lock:
            task["_control"] = engine
        return engine.run(task["url"], task["dir"] or self.cfg.get("defaultDir"))

    # ---------------- 杂项 ----------------

    def _remove_files(self, task: dict) -> None:
        if task.get("kind") == "course":
            # 只清临时分片。已经合并好的 mp4 是用户的东西，取消任务也不能删。
            import shutil

            try:
                shutil.rmtree(tmp_dir_for(task.get("url", ""), task.get("id", "")),
                              ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            return
        directory = Path(task.get("dir") or "")
        name = task.get("filename") or ""
        if name:
            for suffix in (".tdmpart", ".tdmlite.json", ".part", ".ytdl"):
                try:
                    (directory / (name + suffix)).unlink(missing_ok=True)
                except OSError:
                    pass
        # 视频任务：yt-dlp 的临时文件路径由进度回调带回来
        tmp = task.get("_tmp_path") or ""
        if tmp:
            for suffix in ("", ".part", ".ytdl", ".temp"):
                try:
                    Path(tmp + suffix).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _guess_kind(url: str) -> str:
        if core.is_course_url(url):
            return "course"
        path = urlparse(url).path.lower()
        suffix = os.path.splitext(path)[1]
        return "file" if suffix in FILE_EXTS else "video"

    @staticmethod
    def _course_key(url: str, directory: str) -> str:
        """同一门课 + 同一个输出目录 = 同一条链路，不能重复排队。

        没有这个键的话，用户连点两下「添加」就会有两个任务同时往同一个
        xxx.mp4 里写，最后谁赢看运气。
        """
        app_id, alive_id = core.parse_course_url(url or "")
        if not (app_id and alive_id):
            return ""
        folder = os.path.normcase(os.path.abspath(directory or ""))
        return f"{app_id}:{alive_id}:{folder}"

    def shutdown(self) -> None:
        self._running = False
        with self.lock:
            controls = [t.get("_control") for t in self.tasks.values() if t.get("_control")]
            engines = [t.get("_engine") for t in self.tasks.values() if t.get("_engine")]
        for c in controls + engines:
            try:
                c.stop()
            except Exception:  # noqa: BLE001
                pass
        self._save(force=True)
