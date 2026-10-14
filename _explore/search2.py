# -*- coding: utf-8 -*-
"""按接口字段名定位处理 get_lookback_list 响应的代码。"""
import glob
import os
import re

DIR = r"D:\视频\xiaoe-js"
KEYS = ["line_sharpness", "lineSharpness", "material_id", "keyframe_id",
        "is_pri_support", "get_lookback_list", "lookback_list"]

for fn in sorted(glob.glob(os.path.join(DIR, "*.js"))):
    try:
        t = open(fn, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if len(t) > 3_000_000:
        continue
    for k in KEYS:
        idxs = [m.start() for m in re.finditer(re.escape(k), t)][:2]
        for i in idxs:
            s = max(0, i - 300)
            e = i + 300
            print(f">>> {os.path.basename(fn)}  key={k} @{i}")
            print(t[s:e].replace("\n", " "))
            print()
