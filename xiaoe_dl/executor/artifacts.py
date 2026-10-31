"""产出登记：任务完成的文件，在这里变成一条可追踪的记录。

为什么不在任务里直接存：一个任务可能产出多个文件（转写 + 字幕 + 截图），
而用户关心的是「这一个文档/这一段字幕进了哪个项目、有没有进飞书」。
文件是文件的粒度，任务是任务的粒度，两者不该挤在同一行里。

登记只写本地 SQLite，不联网；要不要同步到飞书由 `feishu.py` 另外决定。
"""
from __future__ import annotations

import os
from pathlib import Path

#: 这些后缀是文字，值得转成飞书云文档；视频、音频、图片只登记索引。
TEXT_EXT = {".md", ".markdown", ".txt", ".srt", ".vtt", ".csv", ".json", ".jsonl"}

_KINDS = {
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".ico"},
    "doc": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".epub"},
}


def _sniff_kind(path: str) -> str:
    """没有扩展名的文件，靠开头几个字节猜类型。

    抖音这类直链保存下来不带扩展名，光看文件名只能归成 other，
    产出列表就按视频/音频筛不出来了。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return ""
    if not head:
        return ""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        # M4A 和 MP4 共用 ftyp 结构，只能看 brand
        return "audio" if head[8:12] in (b"M4A ", b"M4B ", b"mp4a") else "video"
    if head[:4] == b"RIFF":
        return "audio" if head[8:12] == b"WAVE" else "video"
    if head[:4] in (b"OggS", b"fLaC") or head[:3] == b"ID3":
        return "audio"
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "audio"  # MP3 / AAC 帧同步
    if head[:4] == b"\x1aE\xdf\xa3":
        return "video"  # Matroska / WebM
    if (head[:4] == b"\x89PNG" or head[:3] == b"\xff\xd8\xff"
            or head[:4] in (b"GIF8", b"II*\x00") or head[:2] == b"BM"):
        return "image"
    if head[:4] == b"%PDF":
        return "doc"
    return ""


def file_kind(path: str) -> str:
    ext = Path(path or "").suffix.lower()
    for kind, exts in _KINDS.items():
        if ext in exts:
            return kind
    if ext in TEXT_EXT:
        return "text"
    if not ext:
        return _sniff_kind(path) or "other"
    return "other"


def is_text(path: str) -> bool:
    return Path(path or "").suffix.lower() in TEXT_EXT


def output_paths(task: dict) -> list[str]:
    """任务产出的文件列表。下载任务只有 path，能力任务可能有一串 outputs。"""
    paths: list[str] = []
    raw = task.get("outputs") or []
    if isinstance(raw, str):
        raw = [raw]
    for item in raw:
        if isinstance(item, str) and item.strip():
            paths.append(item.strip())
    single = (task.get("path") or "").strip()
    if single:
        paths.append(single)
    seen, result = set(), []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def rows_for_task(task: dict) -> list[dict]:
    """把任务转成待登记的产出行。只收本地真实存在的文件。

    不存在的路径不登记：任务可能删了临时文件，或者产物在远端，
    留一条空壳记录只会让索引看起来比实际多。
    """
    rows = []
    project = (task.get("project") or "").strip()
    sequence = (task.get("sequence") or "").strip()
    capability = task.get("capability") or task.get("kind") or ""
    source = (task.get("url") or "").strip()
    for path in output_paths(task):
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        rows.append({
            "task_id": task.get("id", ""),
            "project": project,
            "sequence": sequence,
            "capability": capability,
            "title": os.path.basename(path),
            "file_kind": file_kind(path),
            "path": path,
            "size": size,
            "source": source,
            "status": "local",
            "created_at": task.get("updated_at") or task.get("created_at"),
        })
    return rows


def index_task(store, task: dict) -> list[dict]:
    """登记一个已完成任务的产物，返回这个任务现在的产出行。"""
    if store is None:
        return []
    for row in rows_for_task(task):
        store.add_artifact(row)
    return store.artifacts_for_task(task.get("id", ""))


def index_history(store, tasks: list[dict]) -> int:
    """补登记功能上线前就已经完成的任务。幂等，可以反复点。

    返回的是「这次新增了几条」，不是「扫过几条」——重复点不会假装又登记了一遍。
    """
    existing = {(r["task_id"], r["path"])
                for r in store.list_artifacts(limit=1_000_000)}
    count = 0
    for task in tasks:
        if task.get("status") != "done":
            continue
        for row in rows_for_task(task):
            key = (row["task_id"], row["path"])
            if key in existing:
                continue
            store.add_artifact(row)
            existing.add(key)
            count += 1
    return count
