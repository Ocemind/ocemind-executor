# -*- coding: utf-8 -*-
"""
核心管线：链接 -> 接口 -> m3u8 -> 分片 -> MP4。

  get_lookback_list  -> 明文 playlist_eof.m3u8 地址 + 分片 CDN 基址与签名
  grab_playlist      -> 下 m3u8，解析分片
  abs_url            -> 拼带签名的分片绝对地址
  download_all       -> 多线程下分片
  merge              -> ffmpeg concat 成 mp4

命令行入口在 cli.py，图形界面在 gui.py。
"""
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

from xiaoe_dl import crypto
from xiaoe_dl.cookies import cookie_header, load_cookies, resolve_cookies
from xiaoe_dl.paths import app_dir, cookie_candidates, find_ffmpeg

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")


# 接口错误码 -> 人话
CODE_HINT = {
    11302: "登录态失效。cookie 过期了，需要重新导出一份（见 README 的 Cookie 一节）",
    11303: "没有该课程的观看权限",
    11304: "课程不存在或已下架",
}


# ---------------------------------------------------------------- 工具
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


def http_get(url, cookie, referer, timeout=40, retries=4, byte_range=None):
    """`byte_range=(start, end)` 是**闭区间**（含两端），需要服务端支持 Range 头。

    有的课程分片按**字节区间**寻址：整场直播切成几百片，但分片名完全相同，
    只靠 URL 上的 `start`/`end` 区分。实测这个 CDN **完全不看那两个查询参数**
    —— 每次请求都返回整个文件（4.4MB / 60 秒），只有 HTTP `Range` 头才认。
    不翻译成 Range 的话，906 片会各下一遍完整文件，拼出 15 小时的视频。
    """
    hdr = {
        "User-Agent": UA,
        "Referer": referer,
        "Origin": "https://xiaoeknow.com",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": cookie,
    }
    want = None
    if byte_range:
        start, end = byte_range
        hdr["Range"] = f"bytes={start}-{end}"
        want = end - start + 1
    last = None
    for i in range(retries):
        try:
            r = _session().get(url, headers=hdr, timeout=timeout)
            if r.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {r.status_code}")
            # 要了区间却收到整份文件 = CDN 忽略了 Range，正是上面那个 bug 的成因。
            # 这种数据看着「有内容」，拼起来却是错的 —— 宁可在这里炸掉。
            if want is not None and r.status_code == 200 and len(r.content) > want:
                raise RuntimeError(
                    f"CDN 忽略了 Range 头（要 {want} 字节，收到 {len(r.content)}）")
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


def is_course_url(url):
    """是不是小鹅通课程链接（按域名判断，不看路径）。

    只看域名是刻意的：课程页、h5 页、分享短链都落在 xiaoeknow.com 及其子域，
    而分片 CDN 在 xet.tech / 腾讯云那些地方。反过来若只看 URL 里有没有
    `xiaoeknow.com` 字样，带该参数的 CDN 直链就会被误判成课程。
    """
    try:
        host = (up.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return host == "xiaoeknow.com" or host.endswith(".xiaoeknow.com")


def h5_base(app_id):
    return f"https://{app_id}.h5.xiaoeknow.com"


def lookback_api(app_id, alive_id):
    return (f"{h5_base(app_id)}/_alive/v3/get_lookback_list"
            f"?app_id={app_id}&alive_id={alive_id}&protection=0")


def parse_lookback(raw):
    """纯函数：解析 get_lookback_list 的响应体，返回 data 段。

    code != 0 时抛出带人话提示的 RuntimeError（见 CODE_HINT）。
    抽成纯函数是为了能单测——错误码翻译是改版时最先要看的东西。
    """
    d = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    code = d.get("code")
    if code != 0:
        raise RuntimeError(f"接口返回 code={code}：{CODE_HINT.get(code, '未知错误')}"
                           f"（msg={d.get('msg')}）")
    data = d.get("data")
    if data is None:
        raise RuntimeError("接口 code=0 但 data 为空，可能改版了")
    return data


def get_lookback(app_id, alive_id, cookie):
    h5 = h5_base(app_id)
    raw = http_get(lookback_api(app_id, alive_id), cookie, h5 + "/")
    return h5, parse_lookback(raw)


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


def grab_playlist(s, ext, cookie, h5, out_m3u8, app_id=None):
    """下载 m3u8（必要时自动解 [xiaoe] 混淆）。返回 m3u8 文本。

    主路径上 `playlist_eof.m3u8` 是**明文**的，解混淆分支基本不会走到；
    留着是兜底。真走到了会打印 [crypto]，那时候值得留意一下。
    """
    raw = http_get(s["url"], cookie, h5 + "/").decode("utf-8", "replace")
    txt = raw
    if not txt.lstrip().startswith("#EXTM3U"):
        # 可能是混淆播放列表，走本地解码器。密钥就是 app_id。
        if not app_id:
            m = re.match(r"https?://([a-zA-Z0-9]+)\.h5\.xiaoeknow\.com", h5 or "")
            app_id = m.group(1) if m else None
        try:
            txt = crypto.decode(crypto.strip_prefix(txt), app_id)
            print("    [crypto] 已解混淆播放列表")
            # 机会主义留样本：下次真遇到 [xiaoe] 就落一份原始文本，
            # 给 tests/data/raw_xiaoe.bin 当真实测试向量用。
            dump = os.environ.get("XIAOE_DUMP_RAW")
            if dump:
                with open(dump, "w", encoding="utf-8") as f:
                    f.write(raw)
                print(f"    [crypto] 原始响应已存到 {dump}")
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


def seg_range(seg):
    """分片名里若带 `start`/`end` 字节区间，返回闭区间 `(start, end)`，否则 None。

    这不是标准的 `#EXT-X-BYTERANGE`，是小鹅通自己加在查询串上的。注意
    **不能把它们从 URL 里删掉** —— URL 带签名，查询串在签名覆盖范围内。

    区间是**两端都含**的：`start=0&end=300235` 对应 300236 字节。
    """
    q = up.parse_qs(up.urlparse(seg).query)
    a = (q.get("start") or [None])[0]
    b = (q.get("end") or [None])[0]
    if a is None or b is None:
        return None
    try:
        start, end = int(a), int(b)
    except ValueError:
        return None
    if start < 0 or end < start:
        return None
    return start, end


def _emit(cb, stage, **fields):
    """进度回调。UI 出错绝不能把下载带崩，一律吞掉。"""
    if not cb:
        return
    try:
        cb({"stage": stage, **fields})
    except Exception:  # noqa: BLE001
        pass


def download_all(segs, ext, cookie, h5, tmpdir, workers=12, label="",
                 on_progress=None, stop_event=None):
    """下全部分片。`on_progress` 收 `{"stage": "downloading", "done", "total"}`。

    `stop_event` 只在**分片边界**检查：已经在路上的请求会跑完（单请求超时
    60 秒封顶），所以按暂停后线程池还要再忙一小会儿才退。这不影响正确性——
    分片是按序号落盘的，下次续传照样接得上。
    """
    os.makedirs(tmpdir, exist_ok=True)
    items = [(abs_url(s, ext), seg_range(s)) for s in segs]
    total = len(items)
    done = [0]
    fail = []
    lock = threading.Lock()
    last_emit = [0.0]

    def progress(force=False):
        if not on_progress:
            return
        now = time.monotonic()
        with lock:
            if not force and now - last_emit[0] < 0.25:
                return
            last_emit[0] = now
            n = done[0]
        _emit(on_progress, "downloading", done=n, total=total)

    def cached(fp, rng):
        """临时目录里的旧分片能不能直接复用。

        区间寻址时**不能只看「文件够大」**：曾经每片都下回整个文件（4.4MB，
        远大于区间），旧判据会认为它下好了而跳过 —— 于是重跑永远修不好，
        只会一遍遍拼出同一个 15 小时视频。区间响应只可能**小于等于**区间
        长度，比区间还大就一定是错的，必须重下。
        """
        if not os.path.exists(fp):
            return False
        size = os.path.getsize(fp)
        if size <= 1024:
            return False
        return not (rng is not None and size > rng[1] - rng[0] + 1)

    def finished():
        with lock:
            done[0] += 1
            n = done[0]
        if n % 25 == 0 or n == total:
            print(f"    {label}{n}/{total} ({n * 100 // total}%)", flush=True)
        progress(force=(n == total))

    def one(i_item):
        i, (u, rng) = i_item
        fp = os.path.join(tmpdir, f"{i:05d}.ts")
        if cached(fp, rng):
            finished()
            return None
        if stop_event is not None and stop_event.is_set():
            return None
        try:
            b = http_get(u, cookie, h5 + "/", timeout=60, retries=4,
                          byte_range=rng)
            if not b:
                raise RuntimeError("空响应")
            with open(fp, "wb") as f:
                f.write(b)
            finished()
            return None
        except Exception as e:
            fail.append((i, u, str(e)))
            return (i, u, str(e))

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, list(enumerate(items, 1))))

    # 重试失败项
    if fail and not (stop_event is not None and stop_event.is_set()):
        print(f"    {label}重试 {len(fail)} 个失败分片 ...")
        still = []
        for i, u, _ in fail:
            if stop_event is not None and stop_event.is_set():
                still.append((i, "已停止"))
                continue
            fp = os.path.join(tmpdir, f"{i:05d}.ts")
            try:
                b = http_get(u, cookie, h5 + "/", timeout=60, retries=6,
                             byte_range=seg_range(segs[i - 1]))
                with open(fp, "wb") as f:
                    f.write(b)
            except Exception as e:
                still.append((i, str(e)))
        fail = still
    return fail


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def merge(tmpdir, n, out_path, ffmpeg=None):
    """concat 成 mp4。先写 `<名字>.tmp.mp4` 再原子改名。

    直接往目标路径写的话，中途崩掉（或用户强杀）会留下一个「文件存在但
    没写完」的 mp4，下次跑就被当成「已存在，跳过」——比没有还糟。
    """
    lst = os.path.join(tmpdir, "filelist.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for i in range(1, n + 1):
            fp = os.path.join(tmpdir, f"{i:05d}.ts")
            f.write(f"file '{fp}'\n")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    ff = find_ffmpeg(ffmpeg)
    tmp_out = out_path + ".tmp.mp4"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "warning",
           "-f", "concat", "-safe", "0", "-i", lst,
           "-c", "copy", "-bsf:a", "aac_adtstoasc", tmp_out]
    print("    [ffmpeg] 合并中 ...")
    r = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        # 退化方案：重新封装
        cmd2 = [ff, "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "concat", "-safe", "0", "-i", lst, tmp_out]
        r = subprocess.run(cmd2, capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            print(r.stderr[-1500:])
            try:
                os.remove(tmp_out)
            except OSError:
                pass
            raise RuntimeError("ffmpeg 合并失败")
    os.replace(tmp_out, out_path)
    return out_path


# ---------------------------------------------------------------- 主流程
def run_one(url, app_id, alive_id, cookies, args, index=None, total=None,
            on_progress=None, stop_event=None, tmpdir=None, ffmpeg=None):
    """下一个课程。

    `cookies` 是 list[Cookie]（不是拼好的头）：每个课程要按它自己的 app_id
    挑出对应那份登录态。批量下载多个小鹅通账号的课时，这一步不能省。

    新增参数全部可选，命令行与 `tools/diagnose.py` 的调用方式不受影响：
    `on_progress` 收事件字典（`resolving/title/output/segments/downloading/
    merging/done/error/cancelled`），`stop_event` 在阶段边界检查，
    `tmpdir` 允许每个任务用自己的临时目录，`ffmpeg` 覆盖合并用的可执行文件。

    返回值仍是 bool：True=成功（含「已存在跳过」），False=失败或被打断。
    被打断时调用方要自己看 `stop_event` 区分取消与真失败。
    """
    tag = f"[{index}/{total}] " if index else ""

    def stopped():
        return stop_event is not None and stop_event.is_set()

    _emit(on_progress, "resolving", url=url)
    if not (app_id and alive_id):
        print(f"{tag}[!] 链接里没解析出 app_id/alive_id，跳过：{url}")
        _emit(on_progress, "error", error="链接里没解析出 app_id/alive_id")
        return False
    if stopped():
        _emit(on_progress, "cancelled")
        return False

    cookie = cookie_header(cookies, app_id)

    print(f"\n{tag}app_id={app_id}  alive_id={alive_id}")
    try:
        h5, data = get_lookback(app_id, alive_id, cookie)
    except Exception as e:
        print(f"{tag}[!] 接口失败：{e}")
        _emit(on_progress, "error", error=str(e))
        return False
    if stopped():
        _emit(on_progress, "cancelled")
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
        else:
            _emit(on_progress, "title", title=ttl)
        out = os.path.join(args.dir, safe_name(ttl) + ".mp4")
        # 仅批量模式下重名才加序号，避免覆盖别的课程
        if total and total > 1 and os.path.exists(out):
            out = out[:-4] + f"_{index:02d}.mp4"
    print(f"{tag}输出 -> {out}")
    _emit(on_progress, "output", path=out)

    if os.path.exists(out) and not args.force:
        print(f"{tag}[=] 已存在，跳过（--force 可覆盖）")
        _emit(on_progress, "done", path=out)
        return True

    tmpdir = tmpdir or os.path.join(os.environ.get("TEMP", "."),
                                    "xiaoe_" + alive_id[-8:])
    os.makedirs(tmpdir, exist_ok=True)
    m3u8_path = os.path.join(tmpdir, "playlist.m3u8")

    try:
        txt = grab_playlist(s, ext, cookie, h5, m3u8_path, app_id)
    except Exception as e:
        print(f"{tag}[!] 播放列表获取失败：{e}")
        _emit(on_progress, "error", error=str(e))
        return False
    segs = parse_segments(txt)
    print(f"{tag}分片 {len(segs)} 个")
    if not segs:
        print(txt[:800])
        _emit(on_progress, "error", error="播放列表里没有分片")
        return False
    _emit(on_progress, "segments", total=len(segs))
    if stopped():
        _emit(on_progress, "cancelled")
        return False

    n = args.test or len(segs)
    t0 = time.time()
    fail = download_all(segs[:n], ext, cookie, h5, tmpdir, args.workers, tag,
                        on_progress=on_progress, stop_event=stop_event)
    print(f"{tag}下载完成 {time.time()-t0:.0f}s，失败 {len(fail)}")

    if stopped():
        # 被暂停/取消：下载到哪算哪，分片留在临时目录里下次续传
        _emit(on_progress, "cancelled")
        return False
    if args.test:
        print(f"{tag}--test 模式，跳过合并。临时目录：{tmpdir}")
        _emit(on_progress, "done", path="")
        return True
    if fail:
        print(f"{tag}[!] 缺失 {len(fail)} 个分片，重跑本命令可续传")
        _emit(on_progress, "error", error=f"缺失 {len(fail)} 个分片")
        return False

    # 合并不响应停止：ffmpeg 起跑后只能等它结束。开始前再看一眼。
    if stopped():
        _emit(on_progress, "cancelled")
        return False
    _emit(on_progress, "merging")
    merge(tmpdir, n, out, ffmpeg=ffmpeg)
    print(f"{tag}✅ {out}  ({os.path.getsize(out)/1024/1024:.1f} MB)")
    _emit(on_progress, "done", path=out)

    if not args.keep_ts:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    return True
