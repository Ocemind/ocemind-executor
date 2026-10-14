# -*- coding: utf-8 -*-
"""
1) 用已登录 cookie 调 get_lookback_list 拿最新 ext(host/path/param)
2) 用解出来的分片名拼 URL，逐个候选基址试探下载第 1 个分片
"""
import json
import sys
import urllib.parse as up
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COOKIES = r"D:\视频\cookies_session.txt"
APP_ID = "apptvagt9li8549"
ALIVE_ID = "l_6ab33ce1e4b0694c354c1a5b"
H5 = f"https://{APP_ID}.h5.xiaoeknow.com"
API = f"{H5}/_alive/v3/get_lookback_list?app_id={APP_ID}&alive_id={ALIVE_ID}"

SEG = "156174317_1257317718_1.ts?resolution=1920x1080"


def load_cookie_header(path):
    """返回 cookie 头 + 供 urllib 使用的 cookiejar。"""
    import http.cookiejar as cj
    jar = cj.CookieJar()
    pairs = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if len(p) < 7:
                continue
            dom, _flag, _path, sec, exp, name, val = p[:7]
            pairs.append(f"{name}={val}")
            try:
                jar.set_cookie(cj.Cookie(0, name, val, None, False,
                                         dom.lstrip(".").lower() or None,
                                         False, False, _path or "/",
                                         False, None, None, False, None,
                                         None, {}))
            except Exception:
                pass
    return "; ".join(pairs), jar


def main():
    ck, jar = load_cookie_header(COOKIES)
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/155.0.0.0 Safari/537.36"),
        ("Accept", "application/json, text/plain, */*"),
        ("Referer", f"{H5}/"),
        ("Cookie", ck),
    ]
    print(f"[api] {API}")
    with op.open(API, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    print(f"[api] {len(raw)} bytes")
    d = json.loads(raw)
    print(f"[api] code={d.get('code')} msg={d.get('msg')}")
    data = d.get("data") or []
    if not data:
        print("[!] 无数据"); return

    cands = []
    for li, line in enumerate(data):
        for s in line.get("line_sharpness", []):
            ext = s.get("ext") or {}
            host = ext.get("host", "")
            path = ext.get("path", "")
            param = ext.get("param", "")
            print(f"\n线路{li+1} {s.get('name')} cloud={s.get('cloud')} "
                  f"is_support={s.get('is_support')} pri={s.get('is_pri_support')}")
            print(f"  host = {host}")
            print(f"  path = {path}")
            print(f"  param= {param}")
            print(f"  obf  = {s.get('url','')[:90]}...")
            if host and path:
                cands.append((f"{host}/{path}/{SEG}", param))

    # 去重
    seen = set()
    uniq = []
    for base, param in cands:
        if base not in seen:
            seen.add(base)
            uniq.append((base, param))

    print("\n" + "=" * 72)
    print("试探下载分片")
    print("=" * 72)
    for base, param in uniq:
        for suffix in ([f"{base}&{param}", base] if param else [base]):
            url = suffix
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": op.addheaders[0][1],
                    "Referer": f"{H5}/",
                    "Cookie": ck,
                    "Origin": "https://xiaoeknow.com",
                })
                with urllib.request.urlopen(req, timeout=30) as r:
                    b = r.read(2048)
                ok = len(b) > 0 and b[0] == 0x47
                print(f"\n  [{r.status}] ts={ok} len_first={len(b)}")
                print(f"   {url}")
            except Exception as e:
                print(f"\n  [ERR] {type(e).__name__}: {e}")
                print(f"   {url}")


if __name__ == "__main__":
    main()
