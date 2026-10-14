# -*- coding: utf-8 -*-
"""在播放器 JS 里搜出 url 字段的反混淆代码。"""
import glob
import os
import re

DIR = r"D:\视频\xiaoe-js"

PATTERNS = [
    r"replace\(/\s*[@#\$%_]\s*/g",
    r"'@'\s*:",
    r'"@"\s*:',
    r"@\s*:\s*['\"]?0",
    r"#\s*:\s*['\"]?2",
    r"deconfus",
    r"confuse",
    r"reverseUrl",
    r"decodeUrl",
    r"atob\(.{0,40}replace",
]

for fn in sorted(glob.glob(os.path.join(DIR, "*.js"))):
    try:
        t = open(fn, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    for p in PATTERNS:
        for m in re.finditer(p, t):
            s = max(0, m.start() - 260)
            e = m.start() + 360
            print(f">>> {os.path.basename(fn)}  pat={p}")
            print(t[s:e].replace("\n", " "))
            print()
            break
