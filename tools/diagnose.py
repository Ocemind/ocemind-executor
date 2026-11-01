# -*- coding: utf-8 -*-
"""
分阶段链路诊断 —— 网站改版应急预案的核心手段。

    python tools/diagnose.py --url "<课程链接>"

把整条链路拆成 7 段依次探测，**第一段失败的地方就是断点**。
2026-10 那次打通全靠人工试错、逆向了几小时；下次网站改版，
先跑这个，直接看断在哪一环，再决定要不要动逆向。

用法：
    python tools/diagnose.py --url "<链接>"            # 常规
    python tools/diagnose.py --url "<链接>" --json     # 给脚本消费
    python tools/diagnose.py --url "<链接>" --stop     # 停在首个 FAIL
    python tools/diagnose.py --url "<链接>" --with-ffmpeg   # 连合并也验一遍

退出码 = 失败阶段数（0 表示全通）。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback

# 开发工具，不在 exe 里。允许直接 `python tools/diagnose.py` 跑。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xiaoe_dl import auth  # noqa: E402
from xiaoe_dl import core as download  # noqa: E402
from xiaoe_dl import cookies as cookies_mod  # noqa: E402
from xiaoe_dl import paths  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# 每个阶段声明它依赖哪些阶段；依赖项失败时本阶段记为 SKIP
STAGES = [
    ("0-env",      "环境（python / ffmpeg / playwright / profile）", [],            "stage_env"),
    ("1-cookie",   "cookie 文件与 ko_token 有效期",                  [],            "stage_cookie"),
    ("2-url",      "从课程链接解析 app_id / alive_id",               [],            "stage_url"),
    ("3-api",      "get_lookback_list 接口",                         ["1-cookie", "2-url"], "stage_api"),
    ("4-playlist", "m3u8 下载与分片解析",                            ["3-api"],     "stage_playlist"),
    ("5-segment",  "分片 CDN 可达性",                                ["4-playlist"], "stage_segment"),
    ("6-ffmpeg",   "下载 + ffmpeg 合并（需 --with-ffmpeg）",         ["5-segment"], "stage_ffmpeg"),
]


# ---------------------------------------------------------------- 输出样式
_USE_COLOR = [False]

_COLORS = {PASS: "\033[32m", FAIL: "\033[31m", SKIP: "\033[33m"}
_RESET = "\033[0m"


def _enable_ansi():
    """Windows 10+ 的控制台默认不解释 ANSI，需要显式打开 VT 处理。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        return bool(k.SetConsoleMode(k.GetStdHandle(-11), 7))
    except Exception:
        return False


def paint(text, kind):
    if not _USE_COLOR[0]:
        return text
    return f"{_COLORS.get(kind, '')}{text}{_RESET}"


class Result:
    def __init__(self, key, label, status, detail="", hint=""):
        self.key = key
        self.label = label
        self.status = status
        self.detail = detail
        self.hint = hint

    def as_dict(self):
        return {"stage": self.key, "label": self.label, "status": self.status,
                "detail": self.detail, "hint": self.hint}


class SkipStage(Exception):
    """阶段内部主动放弃（前置条件不足），记为 SKIP 而非 FAIL。"""


# ---------------------------------------------------------------- 各阶段
def stage_env(ctx):
    parts = []
    problems = []

    parts.append(f"python {sys.version.split()[0]}")

    ff = download.find_ffmpeg()
    ff_ok = False
    try:
        import subprocess
        r = subprocess.run([ff, "-version"], capture_output=True, text=True,
                           timeout=15)
        if r.returncode == 0:
            ver = r.stdout.splitlines()[0].split("version")[1].split()[0]
            where = "PATH" if shutil.which("ffmpeg") else "兜底路径"
            parts.append(f"ffmpeg {ver} ({where})")
            ff_ok = True
        else:
            problems.append(f"ffmpeg 退出码 {r.returncode}")
    except Exception as e:
        problems.append(f"ffmpeg 不可用：{e}")
    ctx["ffmpeg_ok"] = ff_ok

    pw = _playwright_status()
    parts.append(f"playwright {'有' if pw else '无'}")

    profile = _profile_dir()
    has_profile = bool(profile and os.path.isdir(profile))
    parts.append(f"浏览器 profile {'有' if has_profile else '无'}")

    detail = " / ".join(parts)
    if problems:
        return Result("0-env", "", FAIL, detail + "  " + "；".join(problems),
                      "ffmpeg 是合并阶段必需的，先把它装上或放进 PATH")
    return Result("0-env", "", PASS, detail,
                  "没有 playwright 就不能自动登录（只影响源码运行；exe 本来就不带）"
                  if not pw else "")


def _playwright_status():
    """复用 auth 的实现，别在这里再写一遍 —— 两处判断迟早会不一致。"""
    return auth.playwright_available()


def _profile_dir():
    return paths.profile_dir()


def stage_cookie(ctx):
    try:
        path = cookies_mod.resolve_cookies(ctx.get("cookie_arg"))
    except SystemExit as e:
        return Result("1-cookie", "", FAIL, "一份 cookie 文件都没找到",
                      str(e).strip().replace("\n", " "))
    ctx["cookie_path"] = path

    n, status = cookies_mod.cookie_status(path)
    if n is None:
        return Result("1-cookie", "", FAIL, f"{path} 读不出来",
                      "文件可能损坏或不是 Netscape 格式")

    size = os.path.getsize(path)
    detail = f"{path}  {n} 条 / {size} 字节  {status or '（无有效期信息）'}"

    _, cks = cookies_mod.load_cookies(path)
    ctx["cookies"] = cks

    # 按域过滤后再拼头：多子域 profile 里会有多条同名 ko_token，
    # 全拼进一个 Cookie 头服务端行为未定义。
    header = cookies_mod.cookie_header_for_app(cks, ctx.get("app_id"))
    if not header:                       # 过滤后为空说明域没对上，退回全量更稳
        header = "; ".join(f"{c.name}={c.value}" for c in cks)
    ctx["cookie_header"] = header

    tok = cookies_mod.find_ko_token(cks, ctx.get("app_id"))
    if tok is None:
        return Result("1-cookie", "", FAIL, detail,
                      "没有有效的 ko_token。它是唯一必需的 cookie，"
                      "去课程平台网页逛一圈会自动续期，或重新导入一份 cookies.txt")
    ctx["ko_token"] = tok
    return Result("1-cookie", "", PASS, detail)


def stage_url(ctx):
    """URL 在 run() 里已经解析过并塞进 ctx 了，这一阶段只负责报告和体检。"""
    if not (ctx.get("url") or ctx.get("app_id")):
        raise SkipStage("未提供 --url")
    detail = f"app_id={ctx.get('app_id')}  alive_id={ctx.get('alive_id')}"
    if not (ctx.get("app_id") and ctx.get("alive_id")):
        return Result("2-url", "", FAIL, detail,
                      "链接里没抠出 app_id / alive_id。确认复制的是课程页地址；"
                      "或者直接用 --app-id / --alive-id 手工指定")
    if not ctx.get("url"):
        detail += "  （来自 --app-id/--alive-id）"
    return Result("2-url", "", PASS, detail)


def stage_api(ctx):
    cookie = ctx["cookie_header"]
    try:
        h5, data = download.get_lookback(ctx["app_id"], ctx["alive_id"], cookie)
    except Exception as e:
        return Result("3-api", "", FAIL, str(e)[:300],
                      "接口层的错。若 code=11302 是登录态过期；"
                      "若 HTTP/解析错，多半是接口改版了 —— 对照 docs/api_chain.md 第 1 跳")
    ctx["h5"] = h5
    ctx["data"] = data
    n_lines = len(data)
    n_opts = sum(len(l.get("line_sharpness") or []) for l in data)
    return Result("3-api", "", PASS, f"code=0  {n_lines} 条线路 / {n_opts} 个可选清晰度")


def stage_playlist(ctx):
    try:
        li, cloud, lname, seg, ext = download.pick_line(ctx["data"])
    except Exception as e:
        return Result("4-playlist", "", FAIL, str(e)[:200],
                      "接口通了但挑不出可用线路，通常是响应结构变了 —— "
                      "检查 ext.host / url 这两个字段还在不在")

    ctx["seg"] = seg
    ctx["ext"] = ext
    tmp = ctx["tmpdir"]
    m3u8 = os.path.join(tmp, "diagnose.m3u8")
    try:
        txt = download.grab_playlist(seg, ext, ctx["cookie_header"], ctx["h5"],
                                     m3u8, ctx["app_id"])
    except Exception as e:
        return Result("4-playlist", "", FAIL, f"线路{li}({lname}) m3u8 下载失败：{e}",
                      "m3u8 地址取不到。若怀疑又出了 [xiaoe] 混淆，"
                      "用 XIAOE_DUMP_RAW=<路径> 跑一次把原始响应存下来")

    segs = download.parse_segments(txt)
    ctx["segs"] = segs
    plain = txt.lstrip().startswith("#EXTM3U")
    detail = (f"线路{li}({lname}) cloud={cloud}  明文={plain}  "
              f"分片 {len(segs)} 个  host={ext.get('host')}")
    if not segs:
        return Result("4-playlist", "", FAIL, detail + "  m3u8 头部：" + repr(txt[:120]),
                      "m3u8 拿到了但解析不出分片。可能清单格式变了")
    if not plain:
        return Result("4-playlist", "", PASS, detail + "  ⚠ 走了 [xiaoe] 解混淆分支",
                      "注意：解混淆分支已被触发，说明站方又开始返回混淆内容了")
    return Result("4-playlist", "", PASS, detail)


def stage_segment(ctx):
    import requests

    RANGE_LEN = 1024
    seg0 = ctx["segs"][0]
    url = download.abs_url(seg0, ctx["ext"])
    rng = download.seg_range(seg0)
    hdr = {
        "User-Agent": download.UA,
        "Referer": ctx["h5"] + "/",
        "Range": f"bytes=0-{RANGE_LEN - 1}",
    }
    try:
        r = requests.get(url, headers=hdr, timeout=30)
    except Exception as e:
        return Result("5-segment", "", FAIL, f"{e}",
                      "分片 CDN 连不上。注意分片鉴权靠 URL 上的 sign/t/us，"
                      "它们会过期 —— 隔一会儿重跑一次，如果又好了就是签名问题")
    if r.status_code not in (200, 206):
        return Result("5-segment", "", FAIL, f"HTTP {r.status_code}",
                      "CDN 拒绝了。先重跑一次排除签名过期；"
                      "持续失败多半是分片地址拼法变了（检查 abs_url 那段逻辑）")

    body = r.content
    head = body[:4].hex()
    addr = (f"区间寻址 start={rng[0]} end={rng[1]}" if rng else "整文件寻址")
    detail = (f"HTTP {r.status_code}  取到 {len(body)} 字节  开头={head}  "
              f"{url.split('/')[2]}  {addr}")

    # 明确要了 1024 字节却回了一整份文件 = CDN 不认 Range 头。
    # 这一条**必须单独拦**：拿到的是货真价实的 TS 流，首字节、状态码全正常，
    # 光看这个分片挑不出任何毛病 —— 但区间寻址的课程会因此每片都下回整个文件，
    # 906 片叠成 15 小时的视频。2026-10-15 就是这么漏过去的，别再删。
    if r.status_code == 200 and len(body) > RANGE_LEN:
        return Result("5-segment", "", FAIL, detail,
                      f"CDN 忽略了 Range 头（要 {RANGE_LEN} 字节，收到 {len(body)}）。"
                      "区间寻址的课程会每片都下回完整文件，合出超长视频。"
                      "检查 core.http_get 是否带上了 Range")
    # TS 流以同步字节 0x47 开头；拿到别的东西说明 CDN 返回了错误页
    if body and body[0] != 0x47:
        return Result("5-segment", "", FAIL, detail,
                      "拿到的不是 TS 流（首字节应为 0x47）。"
                      "可能是 CDN 返回了错误页，或缺了签名参数")
    return Result("5-segment", "", PASS, detail)


def stage_ffmpeg(ctx):
    if not ctx.get("with_ffmpeg"):
        raise SkipStage("未加 --with-ffmpeg")
    if not ctx.get("ffmpeg_ok"):
        return Result("6-ffmpeg", "", FAIL, "ffmpeg 不可用",
                      "先解决 0-env 阶段报的 ffmpeg 问题")
    n = min(2, len(ctx["segs"]))
    tmp = os.path.join(ctx["tmpdir"], "segs")
    os.makedirs(tmp, exist_ok=True)
    try:
        fail = download.download_all(ctx["segs"][:n], ctx["ext"],
                                     ctx["cookie_header"], ctx["h5"], tmp,
                                     workers=2, label="diagnose ")
        if fail:
            return Result("6-ffmpeg", "", FAIL, f"{len(fail)}/{n} 个分片下载失败",
                          "分片级下载不稳。先看 5-segment 是否 PASS")
        out = os.path.join(ctx["tmpdir"], "diagnose.mp4")
        download.merge(tmp, n, out)
        size = os.path.getsize(out)
        if size == 0:
            return Result("6-ffmpeg", "", FAIL, "合并产物 0 字节",
                          "ffmpeg 跑完了但没输出。看上面的 ffmpeg stderr")
        return Result("6-ffmpeg", "", PASS,
                      f"{n} 个分片 -> {size / 1024:.0f} KB")
    except Exception as e:
        return Result("6-ffmpeg", "", FAIL, str(e)[:300],
                      "合并失败。若只是 2 个分片的衔接问题，"
                      "多半不影响完整课程（关键帧边界），但值得看一眼 ffmpeg 输出")


# ---------------------------------------------------------------- 主流程
def run(url=None, cookie_arg=None, app_id=None, alive_id=None,
        with_ffmpeg=False, stop_on_fail=False):
    ctx = {"url": url, "cookie_arg": cookie_arg, "with_ffmpeg": with_ffmpeg}

    # 先解析一次 URL：cookie 阶段要靠 app_id 才能挑出属于这个 app 的
    # ko_token。显式传的 --app-id 优先于链接里抠出来的。
    p_app, p_alive = download.parse_course_url(url) if url else (None, None)
    ctx["app_id"] = app_id or p_app
    ctx["alive_id"] = alive_id or p_alive

    results = []
    failed_keys = set()
    skipped_keys = set()
    tmpdir = tempfile.mkdtemp(prefix="xiaoe_diag_")
    ctx["tmpdir"] = tmpdir

    try:
        for key, label, deps, fn_name in STAGES:
            fn = globals()[fn_name]
            blocked = [d for d in deps if d in failed_keys or d in skipped_keys]
            if blocked:
                r = Result(key, label, SKIP, f"（因 {blocked[0]} 未通过而跳过）")
                results.append(r)
                skipped_keys.add(key)
                if stop_on_fail and failed_keys:
                    break
                continue
            try:
                r = fn(ctx)
            except SkipStage as e:
                r = Result(key, label, SKIP, f"（{e}）")
                skipped_keys.add(key)
            except Exception:
                r = Result(key, label, FAIL, traceback.format_exc()[-400:],
                           "探测脚本自己崩了。这是 diagnose 的 bug，不是下载器的")
            r.label = label
            results.append(r)
            if r.status == FAIL:
                failed_keys.add(key)
                if stop_on_fail:
                    break
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return results


def render(results, out=sys.stdout):
    width = max(len(r.key) for r in results) if results else 8
    for r in results:
        tag = paint(f"[{r.status}]", r.status)
        print(f"{tag} {r.key:<{width}}  {r.detail}", file=out)
        if r.hint and r.status in (FAIL, SKIP):
            print(f"{' ' * 8}↳ {r.hint}", file=out)
    print("─" * 60, file=out)
    fails = [r.key for r in results if r.status == FAIL]
    if not fails:
        print("断点定位：无 —— 全链路通过", file=out)
    else:
        print(f"断点定位：首个失败阶段 = {fails[0]}"
              + (f"（共 {len(fails)} 处失败）" if len(fails) > 1 else ""), file=out)


def main():
    ap = argparse.ArgumentParser(
        description="课程平台链路分阶段诊断 —— 改版时先跑这个找断点")
    ap.add_argument("--url", help="课程链接")
    ap.add_argument("--cookies", help="cookie 文件路径（默认自动找）")
    ap.add_argument("--app-id", help="手工指定 app_id（链接解析不出来时用）")
    ap.add_argument("--alive-id", help="手工指定 alive_id（同上）")
    ap.add_argument("--with-ffmpeg", action="store_true",
                    help="额外验证下载 + 合并（会真的下 2 个分片）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--stop", action="store_true", help="停在首个失败阶段")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _USE_COLOR[0] = (not args.no_color) and _enable_ansi()

    results = run(url=args.url, cookie_arg=args.cookies, app_id=args.app_id,
                  alive_id=args.alive_id, with_ffmpeg=args.with_ffmpeg,
                  stop_on_fail=args.stop)

    if args.json:
        print(json.dumps([r.as_dict() for r in results],
                         ensure_ascii=False, indent=2))
    else:
        render(results)

    return sum(1 for r in results if r.status == FAIL)


if __name__ == "__main__":
    sys.exit(main())
