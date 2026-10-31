"""按 Release 里真实存在的资产，重建 updater 使用的 latest.json。

为什么不用 tauri-action 自带的 latest.json：它会用 productName 拼出文件名去匹配
已上传的资产，而 GitHub 会把资产名里的空格换成 `.`、删掉非 ASCII 字符——
"Ocemind 内容执行器_2.3.0_x64_zh-CN.msi" 实际存成
"Ocemind._2.3.0_x64_zh-CN.msi"，两者对不上，Windows 条目就被静默丢掉了。

这里改成从 Releases API 读真实资产名，名字怎么变都无所谓。

用法（CI 里由 manifest job 调用，上传交给 gh CLI）：
    GITHUB_REPOSITORY=owner/repo python tools/make_latest_json.py v2.3.0 [输出路径]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"


def _request(url: str, *, method: str = "GET", data: bytes | None = None,
             accept: str = "application/vnd.github+json") -> bytes:
    headers = {
        "Accept": accept,
        "User-Agent": "ocemind-release",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # 仓库是公开的，只读接口匿名就能读；带上令牌只是为了避免限流。
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:800]
        raise SystemExit(f"{method} {url} -> HTTP {exc.code}: {body}") from exc


def _json(url: str, **kwargs) -> dict:
    return json.loads(_request(url, **kwargs))


def _pick(assets: list[dict], suffix: str) -> dict:
    for asset in assets:
        if asset["name"].endswith(suffix):
            return asset
    raise SystemExit(f"Release 里找不到以 {suffix} 结尾的资产：{[a['name'] for a in assets]}")


def _signature(asset: dict) -> str:
    """下载 .sig 资产的内容——minisign 签名原文，就是 latest.json 要的 signature。"""
    return _request(asset["url"], accept="application/octet-stream").decode().strip()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: make_latest_json.py <tag> [输出路径]")

    tag = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "latest.json"
    repo = os.environ["GITHUB_REPOSITORY"]
    release = _json(f"{API}/repos/{repo}/releases/tags/{tag}")
    assets = release["assets"]

    platforms = {}
    for suffix, keys in (
        (".msi", ("windows-x86_64", "windows-x86_64-msi")),
        (".AppImage", ("linux-x86_64", "linux-x86_64-appimage")),
    ):
        try:
            bundle = _pick(assets, suffix)
            sig = _pick(assets, suffix + ".sig")
        except SystemExit as exc:
            # 某个平台这轮没出包就跳过，不影响另一个平台的自动更新。
            print(f"跳过 {suffix}: {exc}", file=sys.stderr)
            continue
        entry = {
            "signature": _signature(sig),
            "url": f"https://github.com/{repo}/releases/download/{tag}/{bundle['name']}",
        }
        for key in keys:
            platforms[key] = entry

    if not platforms:
        raise SystemExit("一个可更新平台的资产都没找到，latest.json 不生成")

    manifest = {
        "version": tag.lstrip("v"),
        "notes": "",
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
