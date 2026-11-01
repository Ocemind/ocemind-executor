# -*- coding: utf-8 -*-
"""把客户端的开源工具（ffmpeg / yt-dlp）同步到国内镜像目录。

背景
----
安装包不带 ffmpeg、yt-dlp：它们更新节奏和我们发版对不上，打进去就得跟着
重发。客户端首次运行时自己去装（见 `ocemind/toolchain.py`）。但 GitHub
在国内不稳，所以客户端优先问「镜像」：

    <镜像根>/index.json
    <镜像根>/ffmpeg/windows-x86_64/ffmpeg.zip
    <镜像根>/ytdlp/any/yt-dlp.zip

这个脚本负责生成上面这套文件：从 GitHub Release 拉最新版、算 SHA-256、
写 index.json。服务器上挂个定时任务跑一次，镜像就一直跟着上游走。

用法
----
    # 只在本地产出目录（默认，最安全，不碰任何服务器）
    python tools/sync_tools_mirror.py --out D:/mirror/tools

    # 顺带推到服务器静态目录（显式给了目标才动手）
    python tools/sync_tools_mirror.py --out ./mirror --upload user@host:/srv/www/tools

    # 只同步某个平台 / 只校验现有文件
    python tools/sync_tools_mirror.py --out ./mirror --platform windows-x86_64
    python tools/sync_tools_mirror.py --out ./mirror --verify

带宽与令牌
----------
默认 `--skip-existing`：本地已有且 index 里记录一致的文件不重复下载
（ffmpeg 有一百多 MB，每天重下没必要）。GitHub API 匿名够用，限流了就设
环境变量 GITHUB_TOKEN。

安全
----
只从 GitHub 官方 Release 取文件；上传用 rsync/scp，不删服务器上的东西
（不带 --delete），避免误伤同目录下别的站点文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocemind.toolchain import FALLBACK_URLS, UA  # noqa: E402

API = "https://api.github.com"

#: 工具 -> GitHub 仓库（只用来取版本号，文件地址仍以 Release 实际资产为准）
REPOS = {
    "ffmpeg": "BtbN/FFmpeg-Builds",
    "ytdlp": "yt-dlp/yt-dlp",
}

#: 平台键 -> Release 资产文件名（先精确匹配，再按片段匹配）
ASSET_SUFFIX = {
    "ffmpeg": {
        "windows-x86_64": "ffmpeg-master-latest-win64-lgpl.zip",
        "linux-x86_64": "ffmpeg-master-latest-linux64-lgpl.tar.xz",
        "linux-aarch64": "ffmpeg-master-latest-linuxarm64-lgpl.tar.xz",
    },
    # yt-dlp 的 Release 里同时有 exe、linux 二进制等，必须是名字正好叫
    # "yt-dlp" 的那个 zipapp
    "ytdlp": {"any": "yt-dlp"},
}


# ----------------------------------------------------------------- 网络
def _request(url: str, *, timeout: int = 60, stream=False):
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _release(tool: str) -> dict:
    """取最新 Release。失败返回 {}，后面退回静态 latest/download 地址。"""
    try:
        with _request(f"{API}/repos/{REPOS[tool]}/releases/latest") as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        print(f"  ! 读 {tool} 的 Release 信息失败（{exc}），改用 latest/download 地址")
        return {}


def _asset_for(tool: str, platform: str, release: dict) -> dict:
    """在 Release 里挑出这个平台要的包。

    返回 {url, name, version, digest, fingerprint}：
    - digest 是 GitHub 给的 "sha256:..."，有它就能不下载也判断有没有换包；
    - fingerprint 退而求其次用资产的 updated_at（BtbN 的 tag 恒为 latest）。
    """
    tag = (release.get("tag_name") or "").strip()
    wanted = ASSET_SUFFIX.get(tool, {}).get(platform, "")
    assets = release.get("assets") or []
    asset = {}
    if wanted:
        picked = ([a for a in assets if a.get("name") == wanted]
                  or [a for a in assets if wanted in (a.get("name") or "")])
        if picked:
            asset = picked[0]
    if asset:
        digest = (asset.get("digest") or "").split(":")[-1].lower()
        return {"url": asset.get("browser_download_url", ""),
                "name": asset.get("name") or "",
                "version": tag,
                "digest": digest,
                "fingerprint": asset.get("updated_at") or tag}
    url = FALLBACK_URLS.get(tool, {}).get(platform, "")
    return {"url": url, "name": Path(url).name if url else "", "version": tag,
            "digest": "", "fingerprint": tag}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with _request(url, timeout=120) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done * 100 // total:3d}%  {done / 1048576:.1f} MiB", end="")
    print()
    os.replace(tmp, dest)


# ----------------------------------------------------------------- 各平台
def sync_tool(tool: str, platform: str, out: Path, old: dict,
              skip_existing: bool) -> dict | None:
    print(f"[{tool}] {platform}")
    release = _release(tool)
    info = _asset_for(tool, platform, release)
    if not info["url"]:
        print("  - 上游没有这个平台的包，跳过")
        return None
    filename = info["name"] or f"{tool}-{platform}"

    rel_dir = Path(tool) / platform
    dest = out / rel_dir / filename
    entry_url = (rel_dir / filename).as_posix()

    old_entry = (old.get(tool) or {}).get(platform) or {}
    if (skip_existing and dest.is_file() and old_entry.get("url") == entry_url
            and _same_upstream(old_entry, dest, info)):
        print(f"  = 已是最新（{info['version'] or '同版本'}）")
        return dict(old_entry, version=info["version"] or old_entry.get("version", ""),
                    fingerprint=info["fingerprint"])

    print(f"  ↓ {info['url']}")
    try:
        _download(info["url"], dest)
    except (urllib.error.URLError, OSError) as exc:
        print(f"  ! 下载失败：{exc}")
        return None

    sha = _sha256(dest)
    size = dest.stat().st_size
    print(f"  ✓ {filename}  {size / 1048576:.1f} MiB  sha256={sha[:16]}…")
    return {"url": entry_url, "sha256": sha, "size": size,
            "version": info["version"], "fingerprint": info["fingerprint"],
            "source": info["url"]}


def _same_upstream(old_entry: dict, dest: Path, info: dict) -> bool:
    """本地文件是否就是上游现在的这一版。

    优先比 GitHub 给的 digest；没有就比资产的 updated_at；都没有就老老实实
    重下（宁可多下一次，也不要拿着旧包当最新）。
    """
    if info["digest"]:
        return old_entry.get("sha256") == info["digest"]
    if info["fingerprint"]:
        return old_entry.get("fingerprint") == info["fingerprint"]
    return False


def build_index(out: Path, manifest: dict) -> Path:
    manifest["schema"] = 1
    manifest["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["tools"] = {k: v for k, v in manifest.get("tools", {}).items() if v}
    path = out / "index.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    print(f"已写入 {path}（{sum(len(v) for v in manifest['tools'].values())} 个条目）")
    return path


def verify(out: Path) -> int:
    """按 index.json 核对磁盘文件，发布前跑一下。"""
    index = out / "index.json"
    if not index.is_file():
        print(f"没有 {index}")
        return 2
    manifest = json.loads(index.read_text(encoding="utf-8"))
    bad = 0
    for tool, entries in (manifest.get("tools") or {}).items():
        for platform, entry in entries.items():
            path = out / entry["url"]
            if not path.is_file():
                print(f"✗ 缺失 {path}")
                bad += 1
                continue
            sha = _sha256(path)
            if sha != entry.get("sha256"):
                print(f"✗ 校验不符 {path}")
                bad += 1
            else:
                print(f"✓ {path}")
    return 1 if bad else 0


def upload(out: Path, target: str) -> int:
    """rsync -a --no-delete 到目标。不留 --delete：同目录可能还有别的东西。"""
    if not shutil.which("rsync"):
        print("没有 rsync，用 scp（会覆盖同名文件，不删除其它文件）")
        files = [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()]
        cmd = ["scp", "-r", *files, target]
        cwd = out
    else:
        cmd = ["rsync", "-a", "--info=progress2", str(out) + "/", target]
        cwd = None
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd)


# ----------------------------------------------------------------- 入口
def default_platforms() -> list[str]:
    return ["windows-x86_64", "linux-x86_64", "linux-aarch64"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同步 ffmpeg / yt-dlp 到国内镜像目录")
    ap.add_argument("--out", required=True, help="镜像根目录（脚本会往里写文件）")
    ap.add_argument("--platform", default="",
                    help="逗号分隔，默认 windows-x86_64,linux-x86_64,linux-aarch64")
    ap.add_argument("--tool", default="ffmpeg,ytdlp", help="逗号分隔")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="忽略本地已有文件，全部重新下载")
    ap.add_argument("--verify", action="store_true", help="只校验，不下载")
    ap.add_argument("--upload", default="", help="rsync 目标，如 user@host:/srv/www/tools")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不下载不写盘")
    args = ap.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    if args.verify:
        return verify(out)

    tools = [t.strip() for t in args.tool.split(",") if t.strip()]
    platforms = ([p.strip() for p in args.platform.split(",") if p.strip()]
                 or default_platforms())

    old: dict = {}
    index = out / "index.json"
    if index.is_file():
        try:
            old = json.loads(index.read_text(encoding="utf-8")).get("tools") or {}
        except ValueError:
            old = {}

    if args.dry_run:
        for tool in tools:
            release = _release(tool)
            for platform in (["any"] if tool == "ytdlp" else platforms):
                info = _asset_for(tool, platform, release)
                print(f"{tool} {platform}: {info['name'] or '(无)'} <- {info['url']}")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"tools": {}}
    for tool in tools:
        plats = ["any"] if tool == "ytdlp" else platforms
        got = {}
        for platform in plats:
            entry = sync_tool(tool, platform, out, old,
                              skip_existing=not args.no_skip_existing)
            if entry:
                got[platform] = entry
        if got:
            manifest["tools"][tool] = got

    build_index(out, manifest)
    if args.upload:
        return upload(out, args.upload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
