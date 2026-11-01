# -*- coding: utf-8 -*-
"""账号浏览器目录的清理。

每个账号一个独立的 Chrome profile（`profiles/<平台>-<id>`），登录态就在里面。
一个账号第一次打开通常 30–40 MB，绝大多数是缓存：着色器缓存、组件下载、
崩溃转储、Service Worker 缓存。删掉这些不影响登录，删掉 profile 本身就会
把登录态一起删了——所以这里**只碰缓存子目录**，而且正在用的账号一律跳过。

账号删掉了但目录还在（孤儿目录）是另一回事：那个账号已经不存在了，整份
都能删。这些目录要单独点「清理」，不混在缓存清理里，免得以为只是清了缓存。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

#: 相对每个 profile 根目录，属于「删了不影响登录」的部分
CACHE_DIRS = (
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnCache",
    "Default/DawnGraphiteCache",
    "Default/GraphiteDawnCache",
    "Default/ScriptCache",
    "Default/Media Cache",
    "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache",
    "Default/Shared Dictionary",
    "GrShaderCache",
    "GPUCache",
    "ShaderCache",
    "DawnCache",
    "GraphiteDawnCache",
    "Crashpad",
    "BrowserMetrics",
    "component_crx_cache",
    "extensions_crx_cache",
    "OptimizationHints",
)

MARKER = "gc.json"


def profiles_root(cfg) -> Path:
    return Path(cfg.home) / "profiles"


def dir_size(path: Path) -> int:
    """目录总字节数。权限问题、正在被写的文件，都当 0 处理，不为统计失败。"""
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _cache_size(profile: Path) -> int:
    return sum(dir_size(profile / rel) for rel in CACHE_DIRS)


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _rows(cfg, accounts: list[dict], running_ids: list[str] | None) -> list[dict]:
    """列出所有 profile：账号记录里的（可能在别处）+ 数据目录下没人认领的。

    不能只看 `profiles/` 一个目录：软件改过名、数据目录搬过家之后，账号记录
    里的绝对路径还指着老地方，而老地方那份才是登录态最新的。两边都要算进来，
    否则「账号在 A 目录、B 目录躺着一份旧的」会被误判成没有账号。
    """
    root = profiles_root(cfg)
    running = set(running_ids or [])
    claimed: dict[str, tuple[Path, dict | None]] = {}
    for acc in accounts:
        raw = acc.get("profile_dir") or ""
        if raw:
            claimed[_key(raw)] = (Path(raw), acc)

    pairs = list(claimed.values())
    if root.is_dir():
        for prof in sorted(p for p in root.iterdir() if p.is_dir()):
            if _key(prof) not in claimed:
                pairs.append((prof, None))

    rows = []
    for prof, acc in pairs:
        if not prof.is_dir():
            continue
        rows.append({
            "name": prof.name,
            "path": str(prof),
            "size": dir_size(prof),
            "cache": _cache_size(prof),
            "account_id": (acc or {}).get("id", ""),
            "label": (acc or {}).get("label", ""),
            "platform": (acc or {}).get("platform", ""),
            "orphan": acc is None,
            "running": bool(acc and acc.get("id") in running),
        })
    return rows


def scan(cfg, accounts: list[dict], running_ids: list[str] | None = None) -> dict:
    """看看各账号目录占多少、其中多少能清。只读，不动文件。"""
    rows = _rows(cfg, accounts, running_ids)
    return {
        "root": str(profiles_root(cfg)),
        "total": sum(r["size"] for r in rows),
        "cache": sum(r["cache"] for r in rows),
        "orphan_total": sum(r["size"] for r in rows if r["orphan"]),
        "profiles": rows,
        "last": last_result(cfg),
    }


def clean(cfg, accounts: list[dict], running_ids: list[str] | None = None,
          orphans: bool = False) -> dict:
    """清缓存；`orphans=True` 时顺带删掉已经不对应任何账号的整份目录。"""
    before = scan(cfg, accounts, running_ids)
    running = set(running_ids or [])
    removed: list[str] = []
    errors: list[str] = []

    for row in before["profiles"]:
        prof = Path(row["path"])
        if row["running"]:
            # 正在跑的浏览器里删缓存目录，Windows 会锁文件，删一半最容易出事
            continue
        if row["orphan"]:
            if not orphans:
                continue
            try:
                shutil.rmtree(prof)
                removed.append(row["name"])
            except OSError as exc:
                errors.append(f"{row['name']}: {exc}")
            continue
        for rel in CACHE_DIRS:
            target = prof / rel
            if not target.exists():
                continue
            try:
                shutil.rmtree(target)
                removed.append(f"{row['name']}/{rel}")
            except OSError as exc:
                errors.append(f"{row['name']}/{rel}: {exc}")

    after = scan(cfg, accounts, running_ids)
    result = {
        "at": time.time(),
        "freed": max(0, before["total"] - after["total"]),
        "before": before["total"],
        "after": after["total"],
        "removed": removed,
        "errors": errors,
        "orphans": len([r for r in before["profiles"] if r["orphan"]]),
    }
    _save(cfg, result)
    return result


def _marker(cfg) -> Path:
    return Path(cfg.home) / MARKER


def _save(cfg, result: dict) -> None:
    try:
        _marker(cfg).write_text(
            json.dumps({k: result[k] for k in ("at", "freed", "before", "after",
                                              "errors", "orphans")},
                       ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


def last_result(cfg) -> dict | None:
    try:
        return json.loads(_marker(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def human_size(n: int) -> str:
    v, unit = float(n or 0), "B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            break
        v /= 1024
    return f"{v:.0f} {unit}" if unit == "B" or v >= 100 else f"{v:.1f} {unit}"
