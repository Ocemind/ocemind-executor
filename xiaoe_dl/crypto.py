# -*- coding: utf-8 -*-
"""
小鹅通 confusion_index 混淆播放列表解码器（从播放器 JS 逆向）。

播放器原代码（js_063.js:4131012 等，多处相同实现）：
    wi = i("cc0f")            // = MD5
    decode: function(e) {
      var t = arguments[1] || "appbgzjnopv1917";
      t = wi(t);                                  // md5(key)
      e = decodeURIComponent(e);
      var i = 3, n = +e.substr(-i)[1];
      var r = "ABC...xyz0123456789+/=";           // 65 字符表
      var o = {char: index};
      var a = e[0], s = r.indexOf(a);
      var c = wi(t + a).substr(s % 8, s % 8 + 7).split("").map(ch => ch.charCodeAt());
      e = e.substr(1, e.length - i - 1);
      for (var l="", u=0, d=0, p=0; p<e.length; p++) {
        d = d == c.length ? 0 : d;
        u = o[e[p]] - d - c[d++];
        while (u < 0) u += 65;
        l += r[u];
        n && (l += e.slice(p+1, p+1+n), p += n);
      }
      return base64decode(l);
    }
"""

import base64
import hashlib
import urllib.parse

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
IDX = {c: i for i, c in enumerate(ALPHABET)}

# 播放器 JS 里 `arguments[1] || "appbgzjnopv1917"` 的那个兜底值。
# 注意这**不是笔误**，是上游 JS 自带的默认值——但它是某个特定 app 的密钥，
# 对我们自己的 app 来说几乎肯定是错的。
# 真正该传的密钥是 **app_id**（见 download.grab_playlist 的调用点）。
JS_DEFAULT_KEY = "appbgzjnopv1917"

# 兼容旧名字。不要拿它当默认值用。
DEFAULT_KEY = JS_DEFAULT_KEY

TAIL_LEN = 3


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def decode(encoded: str, key: str) -> str:
    """encoded: 去掉 '[xiaoe]' 前缀后的原始(仍带 %XX 转义)字符串。

    key 必须显式传（通常是 app_id）。这里刻意不给默认值：
    用错密钥不会抛异常，只会解出一堆乱码，静默给错答案比报错难查得多。
    """
    if not key:
        raise ValueError("必须传 key（通常是 app_id）；没有默认值可用")
    t = md5_hex(key)
    e = urllib.parse.unquote(encoded)

    if len(e) < TAIL_LEN + 2:
        raise ValueError("数据过短")

    tail = e[-TAIL_LEN:]
    try:
        insert = int(tail[1])
    except ValueError:
        insert = 0

    first = e[0]
    if first not in IDX:
        raise ValueError(f"首字符 {first!r} 不在字母表内")
    s = IDX[first]

    # 密钥流：md5(key + 首字符).substr(s%8, s%8+7)
    # 注意 JS 的 substr(start, LENGTH)，长度是 s%8+7（7~14），不是固定 7
    start = s % 8
    ks = [ord(ch) for ch in md5_hex(t + first)[start: start + (start + 7)]]

    e = e[1: len(e) - TAIL_LEN]          # 去掉首字符与尾部 3 字符

    out = []
    d = 0
    p = 0
    while p < len(e):
        if d == len(ks):
            d = 0
        v = IDX.get(e[p])
        if v is None:
            # 非字母表字符：原样跳过（保持与 JS 行为近似）
            p += 1
            continue
        v = v - d - ks[d]
        d += 1
        while v < 0:
            v += 65
        out.append(ALPHABET[v])
        if insert:
            out.append(e[p + 1: p + 1 + insert])
            p += insert
        p += 1

    # CryptoJS 的 Base64 字母表不含 '='，解析时会忽略该字符，
    # 而本算法里 '=' 是字母表 index 64 的正常输出字符，因此这里一律剔除。
    b64 = "".join(out).replace("=", "")
    pad = (-len(b64)) % 4
    return base64.b64decode(b64 + "=" * pad).decode("utf-8", errors="replace")


def strip_prefix(body: str) -> str:
    if body.startswith("[xiaoe]"):
        return body[len("[xiaoe]"):]
    return body


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\视频\_probe.bin"
    key = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_KEY
    raw = open(path, "rb").read().decode("utf-8", errors="replace")
    plain = decode(strip_prefix(raw), key)
    print("解密结果长度:", len(plain))
    print("是否含 #EXTM3U:", "#EXTM3U" in plain)
    print("--- 前 600 字符 ---")
    print(plain[:600])
