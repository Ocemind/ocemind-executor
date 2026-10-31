"""能力适配器的统一契约。

每个引擎只做一件事，统一返回：

    {"ok": bool, "outputs": [绝对路径...], "error": str, "cancelled": bool}

进度通过 ctx.progress(...) 上报。没有可知总量的能力（ASR 边转边出），
传 progress=None，前端显示不确定态——**不画假百分比**。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Ctx:
    job: dict
    config: object
    workdir: Path
    _stop: threading.Event = field(default_factory=threading.Event)
    _progress: object = None
    _usage: object = None

    @property
    def inputs(self) -> dict:
        return self.job.get("inputs", {})

    @property
    def params(self) -> dict:
        return self.job.get("params", {})

    def should_stop(self) -> bool:
        return self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()

    def progress(self, value: float | None = None, stage: str = "") -> None:
        if self._progress:
            self._progress({"progress": value, "stage": stage})

    def record_usage(self, provider: str, capability: str,
                     units: float, cost: float, detail: str = "") -> None:
        """记账。单价 × 用量，用于月度预算门控。"""
        if self._usage:
            self._usage(provider, capability, units, cost, detail)


class Engine:
    """子类实现 run()。id 用于注册表与幂等键。"""

    id: str = ""
    kinds: tuple[str, ...] = ()

    def run(self, ctx: Ctx) -> dict:  # pragma: no cover - 抽象
        raise NotImplementedError


def ok(*outputs: str) -> dict:
    return {"ok": True, "outputs": [str(o) for o in outputs], "error": ""}


def fail(msg: str) -> dict:
    return {"ok": False, "outputs": [], "error": str(msg)}


def cancelled() -> dict:
    return {"ok": False, "outputs": [], "error": "", "cancelled": True}
