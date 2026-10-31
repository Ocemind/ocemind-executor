"""本地引擎一键安装。

只是把 `python -m pip install <包>` 跑在后台，并把输出留下来给人看。
能做的是"省掉开终端"，不是"猜着装"——包名写死在 backends.LOCAL_ENGINES
里，界面上只能装这几个，不能传任意命令进来。
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import threading

from . import backends

_LOCK = threading.Lock()
_STATE: dict = {
    "running": False,
    "engine": "",
    "package": "",
    "log": [],
    "ok": None,
    "error": "",
}


def _catalog() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for engines in backends.LOCAL_ENGINES.values():
        for engine in engines:
            out[engine["id"]] = dict(engine)
    return out


def status() -> dict:
    with _LOCK:
        return {"running": _STATE["running"], "engine": _STATE["engine"],
                "package": _STATE["package"], "ok": _STATE["ok"],
                "error": _STATE["error"], "log": list(_STATE["log"][-80:])}


def _run(engine: dict) -> None:
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--no-input", engine["package"]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800)
        with _LOCK:
            tail = (proc.stdout or proc.stderr or "").strip().splitlines()
            _STATE["log"] = tail[-80:]
            _STATE["ok"] = proc.returncode == 0
            if not _STATE["ok"]:
                _STATE["error"] = (tail[-1] if tail else f"pip 退出码 {proc.returncode}")
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["ok"] = False
            _STATE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        importlib.invalidate_caches()
        with _LOCK:
            _STATE["running"] = False


def start(engine_id: str) -> dict:
    """开始安装。已经在装就直接返回当前状态，不排队、不并发。"""
    catalog = _catalog()
    engine = catalog.get(engine_id)
    if engine is None:
        raise ValueError(f"不支持一键安装的引擎: {engine_id}")
    with _LOCK:
        if _STATE["running"]:
            raise ValueError(f"正在安装 {_STATE['engine']}，等它跑完")
        _STATE.update({"running": True, "engine": engine_id,
                       "package": engine["package"], "log": [],
                       "ok": None, "error": ""})
    threading.Thread(target=_run, args=(engine,), daemon=True).start()
    return status()
