"""课程任务引擎：把 `ocemind.core.run_one` 包成和另两个引擎一样的形状。

另两个引擎（`VideoDownloader` / `SegmentedDownload`）都是「run() 返回 dict、
stop() 立即生效」。这里照抄这个约定，任务队列就不用为课程写第三套逻辑。

和 Tk 界面的**唯一有意差异**：绝不隐式拉起浏览器登录。队列里跑到一半弹窗
是灾难（尤其是批量任务）。需要登录时在界面上显式点「登录一次」（见
`course_auth.py`），这里只吃已经存在的 cookies.txt。
"""
from __future__ import annotations

import os
import tempfile
import threading
import types
import uuid

from ocemind import auth, core


def tmp_dir_for(url: str, task_id: str = "") -> str:
    """每个任务一个临时目录。

    原来 CLI 用 `ocemind_<alive_id 后 8 位>`：同一门课开两个任务（比如一个 720P
    一个原画）就会互相覆盖分片。加上 task_id 才互不干扰。
    """
    _, alive_id = core.parse_course_url(url or "")
    tail = (alive_id or "x")[-8:]
    tag = (task_id or uuid.uuid4().hex)[:8]
    return os.path.join(tempfile.gettempdir(), f"ocemind_{tail}_{tag}")


class CourseDownloader:
    """一次课程下载。`run()` 返回 `{ok, path} / {ok, error} / {cancelled}`。"""

    def __init__(self, cfg, task_id: str = "", on_progress=None) -> None:
        self.cfg = cfg
        self.task_id = task_id
        self.on_progress = on_progress
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set()

    def _notify(self, event: dict) -> None:
        if self.on_progress:
            self.on_progress(event)

    def run(self, url: str, out_dir: str) -> dict:
        app_id, alive_id = core.parse_course_url(url or "")
        if not (app_id and alive_id):
            return {"ok": False,
                    "error": "链接里没解析出 app_id / alive_id，不是课程链接？"}

        try:
            _path, cookies = auth.get_cookie(
                app_id=app_id,
                mode="manual",
                manual_path=self.cfg.get("courseCookieFile") or None,
            )
        except auth.AuthError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"读取登录态失败：{exc}"}

        args = types.SimpleNamespace(
            line=self.cfg.get("courseLine") or None,
            out="",
            dir=out_dir,
            force=False,
            test=None,
            workers=int(self.cfg.get("courseWorkers") or 24),
            keep_ts=bool(self.cfg.get("courseKeepTs")),
        )

        seen = {"path": "", "error": "", "cancelled": False}

        def relay(event: dict) -> None:
            stage = event.get("stage")
            if stage == "done":
                seen["path"] = event.get("path") or ""
            elif stage == "error":
                seen["error"] = event.get("error") or ""
            elif stage == "cancelled":
                seen["cancelled"] = True
            self._notify(event)

        try:
            ok = core.run_one(
                url, app_id, alive_id, cookies, args,
                on_progress=relay,
                stop_event=self._stop,
                tmpdir=tmp_dir_for(url, self.task_id),
                ffmpeg=self.cfg.get("ffmpegPath") or None,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if ok:
            return {"ok": True, "path": seen["path"]}
        if self._stop.is_set() or seen["cancelled"]:
            return {"ok": False, "cancelled": True}
        return {"ok": False, "error": seen["error"] or "下载失败"}
