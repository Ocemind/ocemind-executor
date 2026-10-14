# -*- coding: utf-8 -*-
"""
cookie 文件的位置解析、读取、按域过滤和状态汇报。

单独成模块的原因：这些是纯函数/纯 IO，测试它们不该被迫 import tkinter GUI，
也不该被迫 import 整个下载管线（那会连带拖进 requests）。

关键事实（实测得出，别改）：
  **只有 `ko_token` 这一条 cookie 是必需的。** 其余都是埋点/统计类，
  少一条不影响调通接口。`ko_token` 是短期凭证，过期后接口返回 code=11302。
"""
import os
import sys
import time as _time
from typing import NamedTuple


class Cookie(NamedTuple):
    """一条 Netscape 格式的 cookie。

    保留 domain 是为了能按域过滤——将来自动登录会带来多个
    `{app_id}.h5.xiaoeknow.com` 子域的 cookie，混在一条 Cookie 头里服务端行为未定义。
    """
    domain: str
    path: str
    secure: bool
    expires: int          # unix 秒；0 或负表示会话级 cookie
    name: str
    value: str


def app_dir():
    """配置/cookie 的规范存放位置。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "xiaoe-dl")


def cookie_candidates():
    """按优先级列出 cookie 文件位置。"""
    here = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    return [
        os.path.join(app_dir(), "cookies.txt"),   # 规范位置
        os.path.join(here, "cookies.txt"),        # 便携：放脚本/exe 旁边
        os.path.join(app_dir(), "cookies_session.txt"),
        r"D:\视频\cookies.txt",                    # 老位置，兼容
        r"D:\视频\cookies_session.txt",
    ]


def resolve_cookies(path=None):
    """没指定就用第一份存在的 cookie 文件。"""
    if path:
        return path
    cands = cookie_candidates()
    for p in cands:
        if os.path.exists(p):
            return p
    raise SystemExit(
        "[!] 找不到 cookies.txt，找过这些位置：\n    "
        + "\n    ".join(cands)
        + f"\n    请把导出的 cookies.txt 放到：{os.path.join(app_dir(), 'cookies.txt')}")


def parse_netscape(text):
    """解析 Netscape cookie 文件文本 -> list[Cookie]。

    格式是 7 个 TAB 分隔的字段：
        domain  flag  path  secure  expires  name  value

    跳过空行和 `#` 注释。字段数不足 7 的行直接丢弃（浏览器导出的文件里
    偶尔会有 `#HttpOnly_` 前缀行，那种行首是 `#` 但实际是有效数据——
    这里要特殊处理）。
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # `#HttpOnly_` 是有效数据行，不是注释
        if line.startswith("#"):
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            else:
                continue
        p = line.split("\t")
        if len(p) < 7:
            continue
        try:
            expires = int(p[4])
        except ValueError:
            expires = 0
        out.append(Cookie(
            domain=p[0],
            path=p[2],
            secure=p[3].strip().upper() == "TRUE",
            expires=expires,
            name=p[5],
            value=p[6],
        ))
    return out


def load_cookies(path):
    """读 cookie 文件。

    返回 `(cookie头字符串, list[Cookie])`。

    注意 cookie 头是把**所有域**的 cookie 拼在一起——这在只有一个 app 子域时没问题，
    但自动登录的 profile 一旦碰过多个 `{app_id}.h5.xiaoeknow.com`，
    就会出现多条同名 `ko_token`。那种场景要用 `cookie_header_for_app()` 按域过滤。
    """
    with open(path, encoding="utf-8", errors="ignore") as f:
        cookies = parse_netscape(f.read())
    header = "; ".join(f"{c.name}={c.value}" for c in cookies)
    return header, cookies


def _domain_matches(domain, app_id):
    """这条 cookie 是否属于该 app（或 xiaoeknow 根域）。"""
    d = (domain or "").lstrip(".").lower()
    if not d:
        return False
    if d == "xiaoeknow.com":
        return True
    if app_id:
        a = app_id.lower()
        if d in (f"{a}.xiaoeknow.com", f"{a}.h5.xiaoeknow.com"):
            return True
    return False


def cookie_header_for_app(cookies, app_id):
    """按域过滤后再拼 Cookie 头，避免多子域同名 cookie 撞车。

    只保留两类：xiaoeknow 根域的、以及属于 `app_id` 的子域的。
    同名冲突时**保留域更具体的那个**（子域优先于根域），因为子域上的才是
    当前 app 真正下发的登录态。
    """
    keep = [c for c in cookies if _domain_matches(c.domain, app_id)]
    if not keep:
        return ""
    # 域越长越具体，排前面，这样去重时留下的是更具体的
    keep.sort(key=lambda c: len(c.domain.lstrip(".")), reverse=True)
    seen = set()
    picked = []
    for c in keep:
        if c.name in seen:
            continue
        seen.add(c.name)
        picked.append(c)
    return "; ".join(f"{c.name}={c.value}" for c in picked)


def find_ko_token(cookies, app_id=None, now=None):
    """挑出当前有效的 ko_token，没有就返回 None。

    给定了 app_id 就**只认属于这个 app 的**（含 xiaoeknow 根域这种共享凭证）。
    绝对不要回退到「别的 app 的 ko_token」——那等于把 A 账号的登录态发给 B 账号。
    找不到就是找不到，交给上层去触发登录。

    同名多处存在时优先取域更具体的那个（app 子域 > 根域）。
    """
    now = _time.time() if now is None else now
    cands = [c for c in cookies if c.name == "ko_token"]
    if app_id:
        cands = [c for c in cands if _domain_matches(c.domain, app_id)]
        cands.sort(key=lambda c: len(c.domain.lstrip(".")), reverse=True)
    for c in cands:
        if c.expires <= 0 or c.expires > now:
            return c
    return None


def cookie_status(path, now=None):
    """返回 (条数, 到期说明)。只统计元数据，不读值。

    真正决定鉴权的是 ko_token 这一条（实测单独用它就能调通接口），
    所以优先报它的有效期；报不出来才退回「最早过期」。

    `now` 可注入，方便测试做确定性断言。
    """
    if not path or not os.path.exists(path):
        return None, None
    now = _time.time() if now is None else now
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            cookies = parse_netscape(f.read())
    except Exception:
        return None, None

    n = len(cookies)
    key = None
    earliest = None
    for c in cookies:
        if c.expires <= 0:
            continue
        if c.name == "ko_token" and (key is None or c.expires > key.expires):
            key = c
        if earliest is None or c.expires < earliest:
            earliest = c.expires

    def fmt(e):
        return _time.strftime("%Y-%m-%d %H:%M", _time.localtime(e))

    if key:
        left = key.expires - now
        if left <= 0:
            return n, "⚠ 登录凭证已过期，需要重新导入 cookies"
        days = int(left // 86400)
        when = "今天" if days == 0 else f"{days} 天后"
        return n, f"登录凭证有效至 {fmt(key.expires)}（还剩 {when}）"
    if earliest:
        return n, f"最早 {fmt(earliest)} 过期"
    return n, None
