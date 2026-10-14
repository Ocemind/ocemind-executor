# -*- coding: utf-8 -*-
"""
小鹅通回放下载器（免登录重放 / 只用 cookie 文件）

流程：
  1. 读 cookie -> 调 _alive/v3/get_lookback_list 拿明文 playlist_eof.m3u8 地址
  2. 下载 m3u8（明文），解析分片
  3. 用 ext.host/path/param 拼分片绝对地址，多线程下回来
  4. ffmpeg concat 成 mp4

用法：
  python download.py --url "<课程链接>" [--out "D:\\视频\\xxx.mp4"]
  python download.py --alive-id l_xxx --app-id appxxx --out out.mp4
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse as up

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
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



# 接口错误码 -> 人话
CODE_HINT = {
    11302: "登录态失效。cookie 过期了，需要重新导出一份（见 README 的 Cookie 一节）",
    11303: "没有该课程的观看权限",
    11304: "课程不存在或已下架",
}

_FFMPEG_CACHE = []


def find_ffmpeg():
    """优先 PATH，其次常见安装位置。找不到就退回 'ffmpeg' 让 ffmpeg 自己报错。"""
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE[0]
    import glob
    import shutil
    cand = shutil.which("ffmpeg")
    if not cand:
        pats = [r"D:\Program Files\ffmpeg*\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg*\bin\ffmpeg.exe",
                r"D:\ffmpeg*\bin\ffmpeg.exe",
                r"C:\ffmpeg*\bin\ffmpeg.exe"]
        for p in pats:
            hits = sorted(glob.glob(p))
            if hits:
                cand = hits[-1]
                break
    _FFMPEG_CACHE.append(cand or "ffmpeg")
    return _FFMPEG_CACHE[0]


# ---------------------------------------------------------------- 工具
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


def load_cookies(path):
    """返回 (cookie头字符串, 原始列表)。"""
    pairs = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) < 7:
                continue
            pairs.append(f"{p[5]}={p[6]}")
    return "; ".join(pairs), pairs


_TLS = threading.local()


def _session():
    """每线程一个 requests.Session —— 复用 TCP/TLS 连接，快很多。"""
    s = getattr(_TLS, "sess", None)
    if s is None:
        s = requests.Session()
        ad = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8,
                                           max_retries=0)
        s.mount("https://", ad)
        s.mount("http://", ad)
        _TLS.sess = s
    return s


def http_get(url, cookie, referer, timeout=40, retries=4):
    hdr = {
        "User-Agent": UA,
        "Referer": referer,
        "Origin": "https://xiaoeknow.com",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": cookie,
    }
    last = None
    for i in range(retries):
        try:
            r = _session().get(url, headers=hdr, timeout=timeout)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.content
        except Exception as e:
            last = e
            time.sleep(0.6 * (i + 1))
    raise last


# ---------------------------------------------------------------- 步骤
def parse_course_url(url):
    """从课程链接里抠出 app_id / alive_id。"""
    q = up.parse_qs(up.urlparse(url).query)
    app_id = (q.get("app_id") or [None])[0]
    alive_id = (q.get("alive_id") or q.get("resource_id") or [None])[0]
    if not app_id:
        m = re.match(r"https?://([a-zA-Z0-9]+)\.h5\.xiaoeknow\.com", url)
        if m:
            app_id = m.group(1)
    if not alive_id:
        m = re.search(r"(l_[0-9a-f]{20,})", url)
        if m:
            alive_id = m.group(1)
    return app_id, alive_id


def get_lookback(app_id, alive_id, cookie):
    h5 = f"https://{app_id}.h5.xiaoeknow.com"
    api = (f"{h5}/_alive/v3/get_lookback_list"
           f"?app_id={app_id}&alive_id={alive_id}&protection=0")
    raw = http_get(api, cookie, h5 + "/")
    d = json.loads(raw.decode("utf-8", "replace"))
    code = d.get("code")
    if code != 0:
        raise RuntimeError(f"接口返回 code={code}：{CODE_HINT.get(code, '未知错误')}"
                           f"（msg={d.get('msg')}）")
    return h5, d["data"]


def get_title(app_id, alive_id, cookie, h5, type_=None, resource_type=None):
    """从 base_info 拿标题，用于自动命名。拿不到就返回 None。"""
    for t in ([type_] if type_ else []) + ["2", "1"]:
        q = f"?app_id={app_id}&resource_id={alive_id}&type={t}"
        if resource_type:
            q += f"&resource_type={resource_type}"
        try:
            d = json.loads(http_get(h5 + "/_alive/v3/base_info" + q,
                                    cookie, h5 + "/", retries=2)
                          .decode("utf-8", "replace"))
            if d.get("code") == 0:
                ai = (d.get("data") or {}).get("alive_info") or {}
                ttl = (ai.get("title") or "").strip()
                if ttl:
                    return ttl
        except Exception:
            continue
    return None


def safe_name(s):
    for ch in '<>:"/\\|?*\n\r\t':
        s = s.replace(ch, "_")
    return s.strip(" .")[:120] or "untitled"


def pick_line(data, prefer=None):
    """挑一条线路。prefer 可以是 cloud 名(tx/hw/byte)或线路序号(1起)。"""
    opts = []
    for li, line in enumerate(data):
        for s in line.get("line_sharpness", []):
            ext = s.get("ext") or {}
            if ext.get("host") and s.get("url"):
                opts.append((li + 1, s.get("cloud"), line.get("line_name",
                                                             f"线路{li+1}"), s, ext))
    if not opts:
        raise RuntimeError("没有可用线路")
    if prefer:
        for o in opts:
            if str(prefer) in (str(o[0]), str(o[1])):
                return o
    return opts[0]


def grab_playlist(s, ext, cookie, h5, out_m3u8):
    """下载 m3u8（必要时自动解 [xiaoe] 混淆）。返回 m3u8 文本。"""
    txt = http_get(s["url"], cookie, h5 + "/").decode("utf-8", "replace")
    if not txt.lstrip().startswith("#EXTM3U"):
        # 可能是混淆播放列表，走本地解码器
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import xiaoe_crypto
            key = ext.get("app_id") or ""
            txt = xiaoe_crypto.decode(txt, key) if key else xiaoe_crypto.decode(txt)
            print("    [crypto] 已解混淆播放列表")
        except Exception as e:
            print(f"    [!] 解混淆失败：{e}")
    with open(out_m3u8, "w", encoding="utf-8") as f:
        f.write(txt)
    return txt


def parse_segments(txt):
    segs = []
    for line in txt.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            segs.append(line)
    return segs


def abs_url(seg, ext):
    """把分片相对名拼成绝对地址（带签名）。"""
    if seg.startswith("http"):
        return seg
    host = ext["host"].rstrip("/")
    path = ext["path"].strip("/")
    param = ext.get("param", "")
    u = f"{host}/{path}/{seg}"
    u += ("&" if "?" in u else "?") + param
    return u


def download_all(segs, ext, cookie, h5, tmpdir, workers=12, label=""):
    os.makedirs(tmpdir, exist_ok=True)
    urls = [abs_url(s, ext) for s in segs]
    total = len(urls)
    done = [0]
    fail = []

    def one(i_u):
        i, u = i_u
        fp = os.path.join(tmpdir, f"{i:05d}.ts")
        if os.path.exists(fp) and os.path.getsize(fp) > 1024:
            done[0] += 1
            return None
        try:
            b = http_get(u, cookie, h5 + "/", timeout=60, retries=4)
            if not b:
                raise RuntimeError("空响应")
            with open(fp, "wb") as f:
                f.write(b)
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == total:
                pct = done[0] * 100 // total
                print(f"    {label}{done[0]}/{total} ({pct}%)", flush=True)
            return None
        except Exception as e:
            fail.append((i, u, str(e)))
            return (i, u, str(e))

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, list(enumerate(urls, 1))))

    # 重试失败项
    if fail:
        print(f"    {label}重试 {len(fail)} 个失败分片 ...")
        still = []
        for i, u, _ in fail:
            fp = os.path.join(tmpdir, f"{i:05d}.ts")
            try:
                b = http_get(u, cookie, h5 + "/", timeout=60, retries=6)
                with open(fp, "wb") as f:
                    f.write(b)
            except Exception as e:
                still.append((i, str(e)))
        fail = still
    return fail


def merge(tmpdir, n, out_path):
    lst = os.path.join(tmpdir, "filelist.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for i in range(1, n + 1):
            fp = os.path.join(tmpdir, f"{i:05d}.ts")
            f.write(f"file '{fp}'\n")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    ff = find_ffmpeg()
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "warning",
           "-f", "concat", "-safe", "0", "-i", lst,
           "-c", "copy", "-bsf:a", "aac_adtstoasc", out_path]
    print("    [ffmpeg] 合并中 ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        # 退化方案：重新封装
        cmd2 = [ff, "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "concat", "-safe", "0", "-i", lst, out_path]
        r = subprocess.run(cmd2, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-1500:])
            raise RuntimeError("ffmpeg 合并失败")
    return out_path


# ---------------------------------------------------------------- 主流程
def run_one(url, app_id, alive_id, cookie, args, index=None, total=None):
    tag = f"[{index}/{total}] " if index else ""
    if not (app_id and alive_id):
        print(f"{tag}[!] 链接里没解析出 app_id/alive_id，跳过：{url}")
        return False

    print(f"\n{tag}app_id={app_id}  alive_id={alive_id}")
    try:
        h5, data = get_lookback(app_id, alive_id, cookie)
    except Exception as e:
        print(f"{tag}[!] 接口失败：{e}")
        return False

    li, cloud, lname, s, ext = pick_line(data, args.line)
    print(f"{tag}线路{li}({lname}) cloud={cloud}  host={ext['host']}")

    # 输出路径：--out 优先，否则自动命名
    out = args.out
    if index and total and total > 1:
        out = None            # 批量模式一律自动命名
    if not out:
        ttl = get_title(app_id, alive_id, cookie, h5)
        if not ttl:
            ttl = f"{app_id}_{alive_id}"
            print(f"{tag}[!] 拿不到标题，用 {ttl}")
        out = os.path.join(args.dir, safe_name(ttl) + ".mp4")
        # 仅批量模式下重名才加序号，避免覆盖别的课程
        if total and total > 1 and os.path.exists(out):
            out = out[:-4] + f"_{index:02d}.mp4"
    print(f"{tag}输出 -> {out}")

    if os.path.exists(out) and not args.force:
        print(f"{tag}[=] 已存在，跳过（--force 可覆盖）")
        return True

    tmpdir = os.path.join(os.environ.get("TEMP", "."),
                          "xiaoe_" + alive_id[-8:])
    os.makedirs(tmpdir, exist_ok=True)
    m3u8_path = os.path.join(tmpdir, "playlist.m3u8")

    txt = grab_playlist(s, ext, cookie, h5, m3u8_path)
    segs = parse_segments(txt)
    print(f"{tag}分片 {len(segs)} 个")
    if not segs:
        print(txt[:800])
        return False

    n = args.test or len(segs)
    t0 = time.time()
    fail = download_all(segs[:n], ext, cookie, h5, tmpdir, args.workers, tag)
    print(f"{tag}下载完成 {time.time()-t0:.0f}s，失败 {len(fail)}")

    if args.test:
        print(f"{tag}--test 模式，跳过合并。临时目录：{tmpdir}")
        return True
    if fail:
        print(f"{tag}[!] 缺失 {len(fail)} 个分片，重跑本命令可续传")
        return False

    merge(tmpdir, n, out)
    print(f"{tag}✅ {out}  ({os.path.getsize(out)/1024/1024:.1f} MB)")

    if not args.keep_ts:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return True


def main():
    ap = argparse.ArgumentParser(
        description="小鹅通回放下载器 —— 只要给课程链接，自动出 MP4")
    ap.add_argument("--url", action="append", default=[],
                    help="课程/回放链接，可重复传多次")
    ap.add_argument("--list", help="txt 文件，一行一个课程链接")
    ap.add_argument("--app-id")
    ap.add_argument("--alive-id")
    ap.add_argument("--cookies", default=None, help="默认自动找 D:\\视频\\cookies.txt")
    ap.add_argument("--out", help="输出 mp4 路径（单个课程时用）")
    ap.add_argument("--dir", default=r"D:\视频", help="批量默认输出目录")
    ap.add_argument("--line", default=None, help="线路序号(1/2/3)或 cloud(tx/hw/byte)")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--keep-ts", action="store_true")
    ap.add_argument("--force", action="store_true", help="覆盖已存在文件")
    ap.add_argument("--test", type=int, default=0, help="只下前 N 个分片做验证")
    args = ap.parse_args()

    urls = list(args.url)
    if args.list:
        with open(args.list, encoding="utf-8", errors="ignore") as f:
            urls += [l.strip() for l in f
                     if l.strip() and not l.startswith("#")]
    if not urls and not (args.app_id and args.alive_id):
        ap.error("请用 --url / --list 提供课程链接")

    args.cookies = resolve_cookies(args.cookies)
    cookie, _ = load_cookies(args.cookies)
    print(f"[*] cookie 文件：{args.cookies}（{len(cookie.split('; '))} 条）")

    if not urls:
        ok = run_one(None, args.app_id, args.alive_id, cookie, args)
        sys.exit(0 if ok else 1)

    total = len(urls)
    if total > 1 and args.out:
        sys.exit("[!] 批量模式不要用 --out，用 --dir 指定目录")
    print(f"[*] 共 {total} 个课程")

    ok_n = 0
    for i, u in enumerate(urls, 1):
        if args.app_id and args.alive_id:
            a, b = args.app_id, args.alive_id
        else:
            a, b = parse_course_url(u)
        if run_one(u, a, b, cookie, args, i, total):
            ok_n += 1
    print(f"\n[*] 全部结束：成功 {ok_n}/{total}")
    sys.exit(0 if ok_n == total else 1)


if __name__ == "__main__":
    main()
