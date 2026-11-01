# -*- coding: utf-8 -*-
"""
解开 get_lookback_list 返回里 url 字段的混淆。

已知（由 base64(明文前缀) 与混淆串逐字符比对得到）：
    标准base64 -> 混淆
      '2' -> '#'
      '3' -> '$'
      '4' -> '%'
      '/' -> '_'
      '0' -> '@'   <-- 与 '1' 冲突
      '1' -> '@'   <-- 与 '0' 冲突
      '=' -> 'j'   <-- 与真的 'j' 冲突
因此对 '@' 与尾部 'j' 做枚举，用 "解出来是不是合法 URL" 来判定。
"""

import base64
import itertools
import json
import re
import sys

STATIC_MAP = {"#": "2", "$": "3", "%": "4", "_": "/"}


def candidates(obf: str):
    """生成所有可能的标准 base64 串。"""
    # 先把无歧义的部分还原
    s = obf
    for k, v in STATIC_MAP.items():
        s = s.replace(k, v)

    at_pos = [i for i, c in enumerate(s) if c == "@"]
    # 尾部连续的 j 才可能是 '=' 填充
    j_pos = []
    for i in range(len(s) - 1, -1, -1):
        if s[i] == "j":
            j_pos.append(i)
        else:
            break
    j_pos = sorted(j_pos)

    for at_combo in itertools.product("01", repeat=len(at_pos)):
        t = list(s)
        for pos, ch in zip(at_pos, at_combo):
            t[pos] = ch
        t = "".join(t)
        # 枚举尾部 j 中哪些是 '='（0~2 个）
        for k in range(0, min(2, len(j_pos)) + 1):
            for sel in itertools.combinations(j_pos, k):
                u = list(t)
                for pos in sel:
                    u[pos] = "="
                yield "".join(u)


def try_decode(obf: str):
    out = []
    for cand in candidates(obf):
        try:
            raw = base64.b64decode(cand + "=" * ((-len(cand)) % 4))
            txt = raw.decode("utf-8")
        except Exception:
            continue
        if txt.startswith("http"):
            out.append(txt)
    return out


if __name__ == "__main__":
    raw = open(r"D:\视频\site-js\js_100.js", encoding="utf-8", errors="ignore").read()
    d = json.loads(raw.split("\n", 1)[1])
    for li, line in enumerate(d["data"]):
        for si, s in enumerate(line["line_sharpness"]):
            obf = s["url"]
            res = try_decode(obf)
            print(f"线路{li+1} {s['name']} n_@={obf.count('@')} -> {len(res)} 个候选")
            for r in res[:3]:
                print("   ", r)
            if not res:
                print("    (无解)")
