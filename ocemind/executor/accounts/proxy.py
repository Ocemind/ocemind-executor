# -*- coding: utf-8 -*-
"""账号代理地址的规范化。

只做一件事：把用户随手写的东西变成一个 Chrome `--proxy-server` 认得的
字符串，认不出来就说清楚哪里不对。写错了不会当场报错——Chrome 会一声不吭
直连出去，账号就在没代理的情况下登录了，这才是最坏的结果。

Chrome 的 `--proxy-server` 不认 URL 里的用户名密码（写了也会被忽略，改成
弹一个看不见的授权框），所以带密码的代理在这里直接拒掉，免得用户以为配上了。
"""
from __future__ import annotations

from urllib.parse import urlsplit

#: Chrome 支持、这里也允许的类型。socks5h 只是别处（curl）的写法，统一成 socks5
SCHEMES = {"http", "https", "socks5"}
ALIASES = {"socks5h": "socks5"}


def normalize(text: str) -> str:
    """`127.0.0.1:7890` → `http://127.0.0.1:7890`；空串表示直连。

    抛 ValueError 时 message 直接给界面看。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise ValueError(f"代理地址看不懂：{exc}") from exc

    scheme = (parts.scheme or "").lower()
    scheme = ALIASES.get(scheme, scheme)
    if scheme not in SCHEMES:
        raise ValueError(f"不支持 {scheme} 代理，只支持 http / https / socks5")
    if parts.username or parts.password:
        raise ValueError("代理地址里不要写账号密码：Chrome 不听这个，"
                         "需要认证的代理会弹一个看不见的授权框")
    host = parts.hostname or ""
    if not host:
        raise ValueError("代理地址缺少主机名，例如 127.0.0.1:7890")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("代理端口不是数字") from exc
    if not port:
        raise ValueError("代理地址缺少端口，例如 127.0.0.1:7890")
    if not 0 < port < 65536:
        raise ValueError("代理端口要在 1–65535 之间")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("代理地址后面不要带路径")

    return f"{scheme}://{host}:{port}"


def display(proxy: str) -> str:
    """界面上显示成「127.0.0.1:8080」这种，短一点。"""
    if not proxy:
        return "直连"
    try:
        parts = urlsplit(proxy)
    except ValueError:
        return proxy
    host = parts.hostname or proxy
    port = f":{parts.port}" if parts.port else ""
    scheme = parts.scheme or "http"
    return f"{host}{port}" if scheme == "http" else f"{scheme}://{host}{port}"
