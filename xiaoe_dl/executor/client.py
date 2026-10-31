"""CLI / MCP 共用的 HTTP 客户端。

刻意不依赖 requests：这两个入口要能在只有标准库的环境里跑起来。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def _saved_token() -> str:
    home = os.environ.get("XIAOE_DL_HOME")
    if not home:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        home = os.path.join(appdata, "xiaoe-dl")
    try:
        data = json.loads((Path(home) / "settings.json").read_text(encoding="utf-8"))
        return str(data.get("apiToken") or "")
    except (OSError, ValueError):
        return ""


class Client:
    def __init__(self, url: str = "", token: str = "", timeout: float = 60) -> None:
        self.url = (url or os.environ.get("OCEMIND_URL")
                    or "http://127.0.0.1:8765").rstrip("/")
        self.token = (token or os.environ.get("OCEMIND_TOKEN")
                      or _saved_token())
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, payload=None):
        body = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=body,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = (exc.read() or b"").decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            raise RuntimeError(f"{exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"连不上执行器 {self.url}（{exc.reason}）。"
                f"先把 Ocemind 内容执行器打开，或用 --url 指向服务器地址") from exc

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload=None):
        return self._request("POST", path, payload or {})

    def delete(self, path: str):
        return self._request("DELETE", path)

    # ------------------------------------------------------------ 便捷方法
    def capabilities(self):
        return self.get("/api/v1/capabilities")["capabilities"]

    def submit(self, capability: str, inputs=None, params=None, title=""):
        return self.post("/api/v1/jobs", {"capability": capability,
                                          "inputs": inputs or {},
                                          "params": params or {},
                                          "title": title})["job"]

    def job(self, job_id: str):
        return self.get(f"/api/v1/jobs/{job_id}")

    def wait(self, job_id: str, timeout: float = 900, interval: float = 1.0):
        import time

        end = time.time() + timeout
        while time.time() < end:
            job = self.job(job_id)
            if job.get("status") in ("done", "error", "cancelled", "paused"):
                return job
            time.sleep(interval)
        return self.job(job_id)

    def accounts(self):
        return self.get("/api/v1/accounts")["accounts"]
