# -*- coding: utf-8 -*-
"""发布记录的状态。

原来只有 草稿 → 批准 → 已验证/失败 四档，但有三种结果既不是成功也不是失败：

- **审核中**：提交按钮点了，回读却还没看到作品。平台要转码、要审，几分钟到
  几小时都正常。报「失败」会让人以为没发出去又去重发一遍。
- **需人工**：流程停在预览页（没让人点发布），或者平台的发布按钮没配选择器。
  这时候东西都在页面上填好了，就差人点那一下，不是错误。
- **定时待发**：批准了但还没到点（第 9 条）。

状态由任务结果里带的 `publish_state` 决定，不靠猜错误文案——文案会改，
状态不会。
"""
from __future__ import annotations

DRAFT = "draft"
APPROVED = "approved"
RUNNING = "running"
VERIFIED = "verified"
REVIEWING = "reviewing"
MANUAL = "manual"
SCHEDULED = "scheduled"
FAILED = "failed"

#: 还能再批准一次的状态。审核中不在这里——已经提交了，再点一次就是发两条
RETRYABLE = (DRAFT, FAILED, MANUAL)

LABELS = {
    DRAFT: "待确认",
    APPROVED: "已批准",
    RUNNING: "发布中",
    VERIFIED: "已确认",
    REVIEWING: "审核中",
    MANUAL: "需人工",
    SCHEDULED: "定时待发",
    FAILED: "失败",
}

#: 界面上给个颜色：ok 绿 / warn 黄 / bad 红 / 无（中性）
TONES = {
    VERIFIED: "ok",
    REVIEWING: "warn",
    MANUAL: "warn",
    SCHEDULED: "warn",
    FAILED: "bad",
}

HINTS = {
    REVIEWING: "已经提交，平台还在审核。过一会儿点「复查」再去后台看一眼，别重复发",
    MANUAL: "内容已经填好停在预览页，去账号浏览器里点发布，或重新批准一次由执行器提交",
    SCHEDULED: "到点会自动开始，不用守着",
    VERIFIED: "内容管理页里能看到这条，发成功了",
    FAILED: "失败了，看下面的原因；改好后可以重新批准",
}


def label(state: str) -> str:
    return LABELS.get(state, state or "未知")


def tone(state: str) -> str:
    return TONES.get(state, "")


def hint(state: str) -> str:
    return HINTS.get(state, "")


def derive(job_status: str, engine_state: str = "", error: str = "") -> str:
    """任务状态 + 引擎自己报的状态 → 发布记录状态。

    `engine_state` 是引擎在结果里留的 `publish_state`，比错误文案可靠。
    """
    if job_status == "queued":
        return APPROVED
    if job_status in ("running", "merging"):
        return RUNNING
    if job_status == "done":
        return engine_state or VERIFIED
    # paused = 软件重启把它打断了，上传到一半，要重新走一遍
    if job_status in ("error", "cancelled", "failed", "paused"):
        # 引擎说了是审核中/需人工，就别跟着任务状态一起报失败
        if engine_state in (REVIEWING, MANUAL):
            return engine_state
        return FAILED
    return engine_state or APPROVED


def summary(records: list[dict]) -> dict:
    """给界面一句话：待发几条、审核几条、需人工几条。"""
    counts: dict[str, int] = {}
    for rec in records:
        state = rec.get("state") or DRAFT
        counts[state] = counts.get(state, 0) + 1
    return counts
