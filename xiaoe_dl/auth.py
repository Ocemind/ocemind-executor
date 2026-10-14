# -*- coding: utf-8 -*-
"""登录态：手动导入 cookie 文件 + 自动（真浏览器）两条路。

## 为什么自动模式不假定「扫码登录」

用户可能用扫码、手机验证码、密码——任何一种。所以自动模式的做法是：
**弹出真实浏览器窗口停在登录页，用户用自己习惯的方式登进去，程序只负责收下会话。**
程序不关心你点了什么、等的是哪个页面跳转，因此短信登录那种「没有成功跳转可等」
的情况也能work。

## 为什么不能只看「ko_token 出现了」就判定登录成功

登录之前站点就可能下发一个**匿名 ko_token**：它存在、也没过期，但拿它调接口
照样回 code=11302。所以「ko_token 存在」只是个必要条件，不是充分条件。

判定「真的登进去了」有两个信号，优先用第一个：
  1. **真调一次接口**（`api_verifier`）——把 get_lookback 跑一遍看 code==0。
     这是唯一能区分「真登录态」和「匿名凭证」的办法，需要 alive_id。
  2. **人工信号**：用户**关掉浏览器窗口**。这是个明确动作，任何登录方式都适用。
     没传 verifier 时就走这条。

## 硬规则：绝不用用户真实 Chrome 的 User Data

Chrome 127+ 的 App-Bound Encryption，密钥是绑在「创建它的那个浏览器二进制 +
那个 profile 路径」上的。换个进程去读那片目录，拿到的是一堆解不开的密文——
这就是 `--cookies-from-browser` 被挡住的真正原因。

所以这里统一用 Playwright 自带的 Chromium，profile 放独立目录。能不能读到，
只取决于「profile 是不是被同一个浏览器二进制创建并读取的」。
"""
import argparse
import os
import sys
import time

from xiaoe_dl.cookies import (Cookie, find_ko_token, load_cookies,
                              parse_netscape)
from xiaoe_dl.paths import app_dir, cookie_candidates, profile_dir


class AuthError(RuntimeError):
    """登录态拿不到、且自动解决不了。消息是写给人看的人话。"""


# ---------------------------------------------------------------- 环境探测
def playwright_available():
    """惰性探测，没装就返回 False 而不是抛 ImportError。

    exe 里 playwright 被显式排除了（见 .spec 的 excludes），所以这个函数
    在 exe 里恒为 False —— 调用方必须优雅降级，不能假设它一定可用。
    """
    try:
        import importlib.util
        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def _require_playwright():
    """真要用的时候才 import。"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as e:
        raise AuthError(
            "自动登录需要 playwright，当前环境没有。\n"
            "    源码运行：pip install playwright && playwright install chromium\n"
            "    exe 里不带 playwright（为了控制体积），请手动导入 cookies.txt"
        ) from e


# ------------------------------------------------------- Cookie 双向转换
def from_browser(raw_cookies):
    """playwright 的 cookie dict 列表 -> list[Cookie]。

    转成自家类型之后，按域过滤 / 找 ko_token / 判断有效期**全部复用
    cookies.py 里那一套**，不必在 auth 里再写一遍——写两遍迟早会不一致。

    `expires` 为负表示会话级 cookie，归一成 0（和 Netscape 格式一致）。
    """
    out = []
    for c in raw_cookies or []:
        try:
            exp = int(c.get("expires"))
        except (TypeError, ValueError):
            exp = 0
        out.append(Cookie(
            domain=c.get("domain") or "",
            path=c.get("path") or "/",
            secure=bool(c.get("secure")),
            expires=0 if exp < 0 else exp,
            name=c.get("name") or "",
            value=c.get("value") or "",
        ))
    return out


def to_netscape(cookies):
    """list[Cookie] -> Netscape 格式文本。纯函数。

    两处要小心：
      - 会话级 cookie（expires<=0）统一写 0，这是格式规定的表示法
      - 名字或值里混进制表符/换行会直接把文件写坏，这种脏值宁可不写也不要写出去
    """
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies or []:
        dom = (c.domain or "").strip()
        name = (c.name or "").strip()
        val = c.value or ""
        if not dom or not name:
            continue
        if any(ch in name for ch in "\t\r\n") or any(ch in val for ch in "\t\r\n"):
            continue
        lines.append("\t".join([
            dom,
            "TRUE" if dom.startswith(".") else "FALSE",
            (c.path or "/").strip() or "/",
            "TRUE" if c.secure else "FALSE",
            str(c.expires if c.expires > 0 else 0),
            name,
            val,
        ]))
    return "\n".join(lines) + "\n"


def save_cookies(cookies, dest=None):
    """把 cookie 写到文件，返回路径。

    **原子写**（临时文件 + replace）：这个文件是用户唯一的登录态。直接覆盖写
    到一半崩掉，会同时丢掉「旧的可凭证」和「刚登录的新结果」，两样都没了。
    """
    dest = dest or os.path.join(app_dir(), "cookies.txt")
    d = os.path.dirname(dest)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(to_netscape(cookies))
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------- 浏览器
def _open_context(pw, headless):
    """启动持久化浏览器上下文（复用同一个 profile 目录）。

    登录**必须 headed**：headless 容易被反爬拦，用户也没法完成扫码/收验证码。
    """
    prof = profile_dir()
    os.makedirs(prof, exist_ok=True)
    try:
        return pw.chromium.launch_persistent_context(
            prof, headless=headless,
            args=["--no-first-run", "--no-default-browser-check"],
        )
    except Exception as e:
        msg = str(e).lower()
        if "singleton" in msg or "lock" in msg or "user data directory" in msg:
            raise AuthError(
                f"浏览器 profile 被占用了：{prof}\n"
                "    多半是上一次的下载器浏览器窗口还没关干净。"
                "把它关掉（或重启一次）再试。") from e
        raise


def _goto_login(page, app_id):
    """导航到该 app 的域。

    **必须导航**：不访问这个域，浏览器根本不知道它，`context.cookies()`
    返回的里面就不会有属于它的 ko_token。
    """
    page.goto(f"https://{app_id}.h5.xiaoeknow.com/",
              wait_until="domcontentloaded", timeout=60000)


def login_interactive(app_id, *, timeout_s=300, verifier=None,
                      on_status=None, poll_s=2.0):
    """弹出浏览器让用户自己登录，登完把会话收下来。

    返回 list[Cookie]；失败返回 None（比如超时且没拿到 ko_token）。

    `verifier(cookies) -> bool` 可选。传了就用它判定「真的登进去了」，
    这时**不需要**用户关窗口；没传就等用户关窗口作为人工完成信号。
    """
    say = on_status or (lambda _m: None)
    sync_playwright = _require_playwright()

    with sync_playwright() as pw:
        ctx = _open_context(pw, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto_login(page, app_id)
            say("浏览器已打开。请用你习惯的方式登录（扫码 / 手机验证码 / 密码都行）。")
            if verifier is None:
                say("登录完成后，**直接关掉浏览器窗口**，程序会自动收下会话。")
            else:
                say("登录完成即可，程序会自动验证并接着下载。")

            deadline = time.time() + timeout_s
            last = []
            last_val = None
            while time.time() < deadline:
                try:
                    last = from_browser(ctx.cookies())
                except Exception:
                    break            # 窗口被关了 = 人工完成信号
                tok = find_ko_token(last, app_id)
                if tok is not None and tok.value != last_val:
                    last_val = tok.value
                    if verifier is not None and verifier(last):
                        say("接口验证通过，登录态已就绪。")
                        return last
                time.sleep(poll_s)

            tok = find_ko_token(last, app_id)
            if tok is None:
                say("没拿到 ko_token（窗口已关或超时）。")
                return None
            if verifier is not None:
                say("超时了：最后拿到的 ko_token 没能通过接口验证。")
                return None
            # 没验证器时只能靠人工信号。这里老实说明残留的不确定性。
            say("已收到 ko_token。注意：若下载时报 11302，说明它是登录前的匿名"
                "凭证——下次请确认登录真正完成后再关窗口。")
            return last
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def refresh(app_id, *, headless=False, wait_s=2.0):
    """用已有 profile 读一次 cookie（通常用来给快过期的登录态续期）。

    **默认 headed**：无头容易被反爬拦，而且有些续期逻辑只在有头时跑。
    想在后台静默刷新再显式传 `headless=True`。

    返回 list[Cookie]；profile 不存在、没 playwright、或没拿到 ko_token 都返回 None。
    """
    if not playwright_available():
        return None
    if not os.path.isdir(profile_dir()):
        return None
    sync_playwright = _require_playwright()

    with sync_playwright() as pw:
        ctx = _open_context(pw, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto_login(page, app_id)
            if wait_s:
                time.sleep(wait_s)      # 给站点一点时间把 cookie 续上
            cks = from_browser(ctx.cookies())
        except AuthError:
            raise
        except Exception:
            return None
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    return cks if find_ko_token(cks, app_id) else None


# ---------------------------------------------------------------- 验证器
def api_verifier(app_id, alive_id):
    """构造一个「真调接口」的验证器。

    这是唯一能区分**真登录态**和**登录前匿名 ko_token** 的办法：
    匿名的也在、也没过期，但接口会回 code=11302。

    需要 alive_id（从任意一个课程链接里就能抠出来）。
    """
    def verify(cookies):
        from xiaoe_dl.core import get_lookback
        from xiaoe_dl.cookies import cookie_header
        try:
            get_lookback(app_id, alive_id, cookie_header(cookies, app_id))
            return True
        except Exception:
            return False
    return verify


# ---------------------------------------------------------------- 决策树
def _first_existing():
    for p in cookie_candidates():
        if os.path.exists(p):
            return p
    return None


def _no_file_msg():
    return (
        "没有可用的登录态。三个办法，任选一个：\n"
        "    1. 让程序帮你登一次：python -m xiaoe_dl.auth --login --app-id <app_id>\n"
        "    2. 手动导出 cookies.txt 放到：" + os.path.join(app_dir(), "cookies.txt") + "\n"
        "    3. 浏览器扩展导出 Netscape 格式的 cookies.txt，用 --cookies 指定路径\n"
        "    找过这些位置：\n      " + "\n      ".join(cookie_candidates())
    )


def get_cookie(*, app_id=None, mode="auto", manual_path=None,
               interactive_ok=False, verifier=None, on_status=None):
    """按决策树拿到可用的登录态。返回 `(cookie文件路径, list[Cookie])`。

    `mode="manual"` 只走文件；`mode="auto"`（默认）依次尝试：

      1. 文件里就有没过期的 ko_token  -> 直接用（**快路径，绝大多数时候走这条，
         不会弹浏览器**）
      2. playwright 可用且有 profile  -> 让浏览器续一次，命中就落盘
      3. `interactive_ok=True`        -> 让用户现登一次
      4. 都不行 -> 文件存在就拿旧的并警告；否则抛 AuthError

    `interactive_ok` 由调用方决定：GUI 可以弹浏览器所以传 True，
    exe 里 playwright 不可用，会自然跳过第 2、3 步。
    """
    say = on_status or (lambda _m: None)

    def read(path):
        if not path or not os.path.exists(path):
            return None
        try:
            return load_cookies(path)[1]
        except Exception as e:
            say(f"cookie 文件读不出来（{e}），当作没有处理")
            return None

    # ---- 只想用手动文件
    if mode == "manual":
        path = manual_path or _first_existing()
        cks = read(path)
        if cks is None:
            raise AuthError(_no_file_msg())
        if not find_ko_token(cks, app_id):
            say("⚠ 文件里的 ko_token 缺失或已过期，仍然继续（接口报 11302 就是这个原因）")
        return path, cks

    # ---- 快路径
    path = manual_path or _first_existing()
    cks = read(path)
    if cks and find_ko_token(cks, app_id):
        say(f"用现有的登录态：{path}")
        return path, cks

    # ---- 让浏览器续一次
    if playwright_available():
        say("现有登录态不可用，尝试用浏览器 profile 续一次…")
        fresh = refresh(app_id)
        if fresh:
            dest = save_cookies(fresh)
            say(f"已从浏览器收下登录态并写入 {dest}")
            return dest, load_cookies(dest)[1]

    # ---- 让用户现登一次
    if interactive_ok:
        say("需要在浏览器里登录一次。")
        fresh = login_interactive(app_id, verifier=verifier, on_status=say)
        if fresh:
            dest = save_cookies(fresh)
            return dest, load_cookies(dest)[1]

    # ---- 都不行
    if cks:
        say("⚠ 拿不到有效的登录态，先用手上这份继续（接口报 11302 就是这个原因）")
        return path, cks
    raise AuthError(_no_file_msg())


# ---------------------------------------------------------------- 命令行
def _main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m xiaoe_dl.auth",
        description="小鹅通登录态管理（登录一次 / 检查 / 续期）")
    ap.add_argument("--login", action="store_true", help="打开浏览器登录一次并保存")
    ap.add_argument("--refresh", action="store_true", help="用已有 profile 续一次")
    ap.add_argument("--check", action="store_true", help="只汇报当前登录态，不做任何事")
    ap.add_argument("--app-id", required=True, help="课程链接里的 app_id")
    ap.add_argument("--alive-id",
                    help="课程链接里的 alive_id。给了就能真调接口验证，"
                         "能识破「登录前的匿名 ko_token」")
    ap.add_argument("--headless", action="store_true",
                    help="--refresh 时用无头模式（不推荐，容易被拦）")
    ap.add_argument("--timeout", type=int, default=300, help="登录等待秒数")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    def say(m):
        print(f"[*] {m}")

    if args.check:
        path = _first_existing()
        if not path:
            print("[!] 一份 cookie 文件都没有")
            return 1
        from xiaoe_dl.cookies import cookie_status
        n, status = cookie_status(path)
        print(f"[*] {path}  {n} 条  {status or '（无有效期信息）'}")
        cks = load_cookies(path)[1]
        tok = find_ko_token(cks, args.app_id)
        print("[*] ko_token：" + ("有且未过期" if tok else "缺失或已过期"))
        return 0 if tok else 1

    if not (args.login or args.refresh):
        ap.error("请指定 --login / --refresh / --check 之一")

    if not playwright_available():
        print("[!] 当前环境没有 playwright，自动登录用不了。")
        print("    源码运行：pip install playwright && playwright install chromium")
        return 1

    verifier = api_verifier(args.app_id, args.alive_id) if args.alive_id else None
    if args.login and verifier is None:
        print("[!] 没给 --alive-id，无法调接口验证。")
        print("    后果：登录前站点下发的匿名 ko_token 也可能被当成登录成功。")
        print("    建议从任意课程链接里取一个 alive_id 一起传进来。")

    if args.login:
        cks = login_interactive(args.app_id, timeout_s=args.timeout,
                                verifier=verifier, on_status=say)
    else:
        cks = refresh(args.app_id, headless=args.headless)

    if not cks:
        print("[!] 没拿到登录态")
        return 1
    dest = save_cookies(cks)
    print(f"[*] 已保存到 {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
