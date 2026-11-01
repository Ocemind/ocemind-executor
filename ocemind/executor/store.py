"""SQLite 存储：任务、账号、发布记录、用量。

为什么用 SQLite 而不是 JSON：账号状态要按时间聚合（"几天没登录"），
发布记录要按账号查询，用量要按月汇总。JSON 全量重写在这种场景下会越写越慢。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    inputs      TEXT NOT NULL DEFAULT '{}',
    outputs     TEXT NOT NULL DEFAULT '[]',
    progress    REAL,
    stage       TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    idem        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_idem   ON jobs(idem);

CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,
    label         TEXT NOT NULL,
    profile_dir   TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_ok_at    REAL,
    last_check_at REAL,
    cookie_expiry REAL,
    status        TEXT NOT NULL DEFAULT 'never',
    note          TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_records (
    id            TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    job_id        TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    state         TEXT NOT NULL,       -- draft / approved / running / verified / failed
    remote_ref    TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pub_account ON publish_records(account_id);

CREATE TABLE IF NOT EXISTS usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    provider    TEXT NOT NULL,
    capability  TEXT NOT NULL,
    units       REAL NOT NULL,      -- 音频秒数 / 图片张数
    cost        REAL NOT NULL,      -- 人民币
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);

CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    project     TEXT NOT NULL DEFAULT '',
    sequence    TEXT NOT NULL DEFAULT '',
    capability  TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    file_kind   TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'local',  -- local / partial / synced / failed
    remote_url  TEXT NOT NULL DEFAULT '',
    remote_token TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    synced_at   REAL,
    UNIQUE(task_id, path)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project);
CREATE INDEX IF NOT EXISTS idx_artifacts_status  ON artifacts(status);
CREATE INDEX IF NOT EXISTS idx_artifacts_task    ON artifacts(task_id);
"""


def _now() -> float:
    return time.time()


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- jobs
    def upsert_job(self, job: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs (id, kind, status, params, inputs, outputs,
                                     progress, stage, error, idem, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, params=excluded.params,
                     inputs=excluded.inputs, outputs=excluded.outputs,
                     progress=excluded.progress, stage=excluded.stage,
                     error=excluded.error, updated_at=excluded.updated_at""",
                (job["id"], job["kind"], job["status"],
                 json.dumps(job.get("params", {}), ensure_ascii=False),
                 json.dumps(job.get("inputs", {}), ensure_ascii=False),
                 json.dumps(job.get("outputs", []), ensure_ascii=False),
                 job.get("progress"), job.get("stage", ""),
                 job.get("error", ""), job.get("idem", ""),
                 job.get("created_at", _now()), _now()),
            )
            self._conn.commit()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict:
        job = dict(row)
        for key in ("params", "inputs", "outputs"):
            try:
                job[key] = json.loads(job[key] or ("{}" if key != "outputs" else "[]"))
            except ValueError:
                job[key] = {} if key != "outputs" else []
        return job

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def find_by_idem(self, idem: str, active_only: bool = True) -> dict | None:
        sql = "SELECT * FROM jobs WHERE idem=? AND idem<>''"
        if active_only:
            sql += " AND status NOT IN ('done','failed','cancelled')"
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, (idem,)).fetchone()
        return self._row_to_job(row) if row else None

    def incomplete_jobs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','running','paused')"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.commit()

    # ------------------------------------------------------------ accounts
    def upsert_account(self, acc: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO accounts (id, platform, label, profile_dir, enabled,
                                         last_ok_at, last_check_at, cookie_expiry,
                                         status, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     platform=excluded.platform, label=excluded.label,
                     profile_dir=excluded.profile_dir, enabled=excluded.enabled,
                     last_ok_at=excluded.last_ok_at, last_check_at=excluded.last_check_at,
                     cookie_expiry=excluded.cookie_expiry, status=excluded.status,
                     note=excluded.note""",
                (acc["id"], acc["platform"], acc["label"], acc["profile_dir"],
                 int(acc.get("enabled", 1)), acc.get("last_ok_at"),
                 acc.get("last_check_at"), acc.get("cookie_expiry"),
                 acc.get("status", "never"), acc.get("note", ""),
                 acc.get("created_at", _now())),
            )
            self._conn.commit()

    def list_accounts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accounts ORDER BY platform, label"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_account(self, account_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_account(self, account_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            self._conn.commit()

    def touch_account(self, account_id: str, *, status: str | None = None,
                      last_ok: bool = False, cookie_expiry: float | None = None) -> None:
        with self._lock:
            sets = ["last_check_at=?"]
            args: list = [_now()]
            if status is not None:
                sets.append("status=?")
                args.append(status)
            if last_ok:
                sets.append("last_ok_at=?")
                args.append(_now())
            if cookie_expiry is not None:
                sets.append("cookie_expiry=?")
                args.append(cookie_expiry)
            args.append(account_id)
            self._conn.execute(
                f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", args
            )
            self._conn.commit()

    # ------------------------------------------------------------- publish
    def upsert_publish(self, rec: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO publish_records
                   (id, account_id, job_id, platform, snapshot_hash, state,
                    remote_ref, result, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     state=excluded.state, remote_ref=excluded.remote_ref,
                     result=excluded.result, updated_at=excluded.updated_at""",
                (rec["id"], rec["account_id"], rec.get("job_id", ""),
                 rec["platform"], rec["snapshot_hash"], rec["state"],
                 rec.get("remote_ref", ""),
                 json.dumps(rec.get("result", {}), ensure_ascii=False),
                 rec.get("created_at", _now()), _now()),
            )
            self._conn.commit()

    def get_publish(self, rec_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM publish_records WHERE id=?", (rec_id,)
            ).fetchone()
        if not row:
            return None
        rec = dict(row)
        try:
            rec["result"] = json.loads(rec["result"] or "{}")
        except ValueError:
            rec["result"] = {}
        return rec

    def list_publish(self, account_id: str = "") -> list[dict]:
        sql = "SELECT * FROM publish_records"
        args: tuple = ()
        if account_id:
            sql += " WHERE account_id=?"
            args = (account_id,)
        sql += " ORDER BY created_at DESC LIMIT 200"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- usage
    def add_usage(self, provider: str, capability: str, units: float,
                  cost: float, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (ts, provider, capability, units, cost, detail) "
                "VALUES (?,?,?,?,?,?)",
                (_now(), provider, capability, units, cost, detail),
            )
            self._conn.commit()

    def month_cost(self, ts: float | None = None) -> float:
        now = ts or _now()
        start = now - 30 * 86400
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost),0) AS c FROM usage WHERE ts>=?", (start,)
            ).fetchone()
        return float(row["c"] if row else 0.0)

    # ------------------------------------------------------------ artifacts
    def add_artifact(self, art: dict) -> dict:
        """登记一条产出。同一个任务里的同一个文件只登记一次。

        重跑任务会生成新文件（同名也可能换了内容），这里按 (task_id, path)
        去重：旧记录保留原来的云文档链接，不会被清掉。
        """
        import uuid

        art_id = art.get("id") or uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO artifacts
                   (id, task_id, project, sequence, capability, title, file_kind,
                    path, size, source, status, remote_url, remote_token,
                    error, created_at, synced_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (art_id, art.get("task_id", ""), art.get("project", ""),
                 art.get("sequence", ""), art.get("capability", ""),
                 art.get("title", ""), art.get("file_kind", ""),
                 art.get("path", ""), int(art.get("size") or 0),
                 art.get("source", ""), art.get("status", "local"),
                 art.get("remote_url", ""), art.get("remote_token", ""),
                 art.get("error", ""), art.get("created_at", _now()),
                 art.get("synced_at")),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE task_id=? AND path=?",
                (art.get("task_id", ""), art.get("path", "")),
            ).fetchone()
        return dict(row) if row else art

    def get_artifact(self, artifact_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_artifacts(self, limit: int = 200, project: str = "",
                       status: str = "") -> list[dict]:
        sql = "SELECT * FROM artifacts"
        where, args = [], []
        if project:
            where.append("project=?")
            args.append(project)
        if status == "unsynced":
            where.append("status IN ('local','partial','failed')")
        elif status:
            where.append("status=?")
            args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def artifacts_for_task(self, task_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_artifact(self, artifact_id: str, **fields) -> None:
        allowed = {"project", "sequence", "title", "status", "remote_url",
                   "remote_token", "error", "synced_at"}
        sets = [f"{key}=?" for key in fields if key in allowed]
        if not sets:
            return
        args = [fields[key] for key in fields if key in allowed]
        args.append(artifact_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE artifacts SET {', '.join(sets)} WHERE id=?", args
            )
            self._conn.commit()

    def projects(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT project, COUNT(*) AS n FROM artifacts
                   WHERE project<>'' GROUP BY project ORDER BY MAX(created_at) DESC"""
            ).fetchall()
        return [r["project"] for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
