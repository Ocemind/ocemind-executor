"""多线程分片下载引擎（IDM 式 Range 分段 + 断点续传 + 限速）。

工作方式：把目标文件预分配成 <name>.ocemind.part，每个线程负责一段区间，
用 seek + write 直接写入对应偏移，互不重叠。续传信息存在同名 .ocemind.json。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
CHUNK = 64 * 1024


class RangeUnsupported(Exception):
    pass


class _RateLimiter:
    def __init__(self, bytes_per_sec: int) -> None:
        self.rate = bytes_per_sec
        self._lock = threading.Lock()
        self._allowance = float(bytes_per_sec)
        self._last = time.monotonic()

    def consume(self, n: int) -> None:
        if not self.rate:
            return
        with self._lock:
            now = time.monotonic()
            self._allowance = min(self.rate, self._allowance + (now - self._last) * self.rate)
            self._last = now
            if self._allowance >= n:
                self._allowance -= n
                return
            deficit = n - self._allowance
            self._allowance = 0.0
        time.sleep(deficit / self.rate)


def _filename_from_headers(headers: dict, url: str) -> str:
    cd = headers.get("content-disposition", "") or headers.get("Content-Disposition", "")
    if cd:
        m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.I)
        if m:
            return unquote(m.group(1).strip().strip('"'))
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        if m:
            return unquote(m.group(1).strip())
    name = os.path.basename(urlparse(url).path.rstrip("/"))
    return name or "download.bin"


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "download.bin"


class SegmentedDownload:
    #: 下载中间文件后缀：数据分片 / 续传状态。
    PART_SUFFIX = ".ocemind.part"
    STATE_SUFFIX = ".ocemind.json"

    def __init__(
        self,
        url: str,
        dest: Path,
        connections: int = 8,
        rate_bytes: int = 0,
        headers: dict | None = None,
        proxies: dict | None = None,
        min_chunk: int = 2 * 1024 * 1024,
    ) -> None:
        self.url = url
        self.dest = Path(dest)
        self.connections = max(1, int(connections or 1))
        self.min_chunk = max(256 * 1024, int(min_chunk))
        self.extra_headers = dict(headers or {})
        self.proxies = proxies
        self.limiter = _RateLimiter(rate_bytes)
        self.stop_event = threading.Event()
        self._saver_stop = threading.Event()
        self._user_stop = False
        self.range_unsupported = False
        self.error: str | None = None
        self.total = 0
        self.supports_range = False
        self.etag = ""
        self.server_filename = self.dest.name
        self.content_type = ""
        self.final_url = url
        self._done = 0
        self._lock = threading.Lock()
        self._parts: list[tuple[int, int]] = []
        self._part_done: dict[int, int] = {}
        self.session = self._make_session()

        self.part_path = self.dest.parent / (self.dest.name + self.PART_SUFFIX)
        self.state_path = self.dest.parent / (self.dest.name + self.STATE_SUFFIX)

    # ---------- 基础设施 ----------

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Encoding": "identity",  # 关键：保证 Range 字节数与文件大小一致
        })
        s.headers.update(self.extra_headers)
        if self.proxies:
            s.proxies.update(self.proxies)
        return s

    def downloaded(self) -> int:
        with self._lock:
            return self._done

    def stop(self) -> None:
        self._user_stop = True
        self.stop_event.set()

    def _add_done(self, n: int) -> None:
        with self._lock:
            self._done += n

    # ---------- 元信息 ----------

    def prepare(self) -> None:
        """探测大小 / 是否支持 Range / 最终文件名。网络抖动时重试 3 次。"""
        resp = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = self.session.head(self.url, allow_redirects=True, timeout=20)
                usable = resp.status_code < 400 and int(resp.headers.get("content-length") or 0) > 0
                if not usable:
                    resp = self.session.get(
                        self.url, allow_redirects=True, stream=True, timeout=20,
                        headers={**self.extra_headers, "Range": "bytes=0-0"},
                    )
                    resp.close()
                last_error = None
                break
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError(f"连接失败: {last_error}")
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code} {resp.reason}")

        self.final_url = resp.url
        size = int(resp.headers.get("content-length") or 0)
        accept_ranges = (resp.headers.get("accept-ranges", "") or "").lower() == "bytes"

        if resp.status_code == 206:
            cr = resp.headers.get("content-range", "")
            m = re.search(r"/(\d+)$", cr)
            if m:
                size = int(m.group(1))
            accept_ranges = True

        if not size:
            raise RuntimeError("服务器未返回文件大小，无法下载")

        self.total = size
        self.supports_range = accept_ranges
        self.etag = resp.headers.get("etag", "")
        self.server_filename = sanitize_filename(_filename_from_headers(resp.headers, self.final_url))
        self.content_type = resp.headers.get("content-type", "")

    # ---------- 状态 ----------

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if state.get("size") != self.total or state.get("url") != self.final_url:
            return
        for idx, done in (state.get("parts") or {}).items():
            self._part_done[int(idx)] = int(done)
        with self._lock:
            self._done = sum(self._part_done.values())

    def _save_state(self) -> None:
        try:
            with self._lock:
                parts = {str(k): v for k, v in self._part_done.items()}
            payload = {
                "url": self.final_url,
                "size": self.total,
                "etag": self.etag,
                "parts": parts,
            }
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _cleanup_state(self) -> None:
        for p in (self.state_path, self.part_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    # ---------- 主流程 ----------

    def run(self) -> dict:
        try:
            self.prepare()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"探测失败: {exc}"}

        self.dest.parent.mkdir(parents=True, exist_ok=True)

        if self.supports_range and self.total >= self.min_chunk and self.connections > 1:
            self._run_segmented()
            if self.range_unsupported and not self._user_stop:
                # 服务器嘴上说支持、实际忽略 Range：清掉重来，退回单线程
                self.stop_event.clear()
                self._cleanup_state()
                self._part_done.clear()
                with self._lock:
                    self._done = 0
                self.supports_range = False
                self._run_single()
        else:
            self._run_single()

        # 先判错误：分片失败会同时置 stop_event，不能当成「暂停」
        if self.error:
            self._save_state()
            return {"ok": False, "error": self.error}
        if self.stop_event.is_set():
            self._save_state()
            return {"ok": False, "cancelled": True, "error": ""}

        try:
            os.replace(self.part_path, self.dest)
        except OSError as exc:
            return {"ok": False, "error": f"重命名失败: {exc}"}
        self._cleanup_state()
        return {"ok": True, "path": str(self.dest), "size": self.total}

    # ---------- 分片 ----------

    def _run_segmented(self) -> None:
        n = self.connections
        chunk = max(self.min_chunk, -(-self.total // n))
        self._parts = []
        start = 0
        while start < self.total and len(self._parts) < n:
            end = min(self.total - 1, start + chunk - 1)
            self._parts.append((start, end))
            start = end + 1

        if self.part_path.exists():
            self._load_state()
        else:
            self._part_done.clear()
            with self._lock:
                self._done = 0

        for idx in list(self._part_done):
            if idx >= len(self._parts):
                self._part_done.pop(idx, None)

        if self.part_path.exists():
            with open(self.part_path, "r+b") as fh:
                if fh.seek(0, os.SEEK_END) != self.total:
                    fh.truncate(self.total)
        else:
            with open(self.part_path, "wb") as fh:
                fh.truncate(self.total)

        self._saver_stop = threading.Event()
        saver = threading.Thread(target=self._saver_loop, daemon=True)
        saver.start()

        threads = []
        for idx, (s, e) in enumerate(self._parts):
            done = self._part_done.get(idx, 0)
            th = threading.Thread(target=self._worker, args=(idx, s, e, done), daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        self._saver_stop.set()
        saver.join(timeout=3)
        self._save_state()

    def _worker(self, idx: int, start: int, end: int, done: int) -> None:
        pos = start + done
        attempts = 0
        while pos <= end and not self.stop_event.is_set():
            try:
                hdrs = {**self.extra_headers, "Range": f"bytes={pos}-{end}"}
                with self.session.get(self.final_url, headers=hdrs, stream=True, timeout=(15, 60)) as resp:
                    if resp.status_code == 200:
                        raise RangeUnsupported("服务器忽略了 Range")
                    if resp.status_code != 206:
                        resp.raise_for_status()
                    with open(self.part_path, "r+b", buffering=0) as fh:
                        fh.seek(pos)
                        for block in resp.iter_content(CHUNK):
                            if self.stop_event.is_set():
                                self._part_done[idx] = pos - start
                                return
                            if not block:
                                continue
                            if pos + len(block) > end + 1:
                                block = block[: end + 1 - pos]
                            fh.write(block)
                            pos += len(block)
                            self._part_done[idx] = pos - start
                            self._add_done(len(block))
                            self.limiter.consume(len(block))
                attempts = 0
            except RangeUnsupported:
                self.range_unsupported = True
                self.stop_event.set()
                return
            except Exception as exc:  # noqa: BLE001
                attempts += 1
                self._part_done[idx] = pos - start
                if attempts > 4:
                    self.error = f"分片 {idx + 1} 失败: {exc}"
                    self.stop_event.set()
                    return
                time.sleep(min(2 ** attempts, 8))

    def _saver_loop(self) -> None:
        while not self.stop_event.wait(2.0):
            if self._saver_stop.is_set():
                break
            self._save_state()

    # ---------- 单线程 ----------

    def _run_single(self) -> None:
        pos = 0
        # 有 sidecar 说明上一次是分片写入，文件里可能有空洞，不能按长度续传
        if self.part_path.exists() and self.supports_range and not self.state_path.exists():
            pos = self.part_path.stat().st_size
            if pos >= self.total:
                return
            with self._lock:
                self._done = pos
        with open(self.part_path, "r+b" if self.part_path.exists() else "wb", buffering=0) as fh:
            fh.seek(pos)
            fh.truncate(pos)
            attempts = 0
            while pos < self.total and not self.stop_event.is_set():
                try:
                    hdrs = dict(self.extra_headers)
                    if pos:
                        hdrs["Range"] = f"bytes={pos}-"
                    with self.session.get(self.final_url, headers=hdrs, stream=True, timeout=(15, 60)) as resp:
                        if pos and resp.status_code == 200:
                            # 服务器不支持续传，从头来
                            fh.seek(0)
                            fh.truncate(0)
                            with self._lock:
                                self._done = 0
                            pos = 0
                        resp.raise_for_status()
                        for block in resp.iter_content(CHUNK):
                            if self.stop_event.is_set():
                                return
                            if not block:
                                continue
                            fh.write(block)
                            pos += len(block)
                            self._add_done(len(block))
                            self.limiter.consume(len(block))
                    attempts = 0
                except Exception as exc:  # noqa: BLE001
                    attempts += 1
                    if attempts > 4:
                        self.error = f"下载失败: {exc}"
                        return
                    time.sleep(min(2 ** attempts, 8))


def probe_http(url: str, headers: dict | None = None, proxies: dict | None = None) -> dict:
    """轻量探测直链：文件名 / 大小 / 是否支持分片。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Encoding": "identity"})
    s.headers.update(headers or {})
    if proxies:
        s.proxies.update(proxies)
    resp = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = s.head(url, allow_redirects=True, timeout=15)
            size = int(resp.headers.get("content-length") or 0)
            if resp.status_code >= 400 or not size:
                with s.get(url, stream=True, timeout=15, allow_redirects=True) as r:
                    size = int(r.headers.get("content-length") or 0)
                    resp = r
            last_error = None
            break
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    if last_error is not None or resp is None:
        return {"ok": False, "error": str(last_error or "探测失败")}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code} {resp.reason}"}
    size = int(resp.headers.get("content-length") or 0)
    accept = (resp.headers.get("accept-ranges", "") or "").lower() == "bytes"
    return {
        "ok": True,
        "final_url": resp.url,
        "size": size,
        "supports_range": accept,
        "filename": sanitize_filename(_filename_from_headers(resp.headers, resp.url)),
        "content_type": resp.headers.get("content-type", ""),
    }
