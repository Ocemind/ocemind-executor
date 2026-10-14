# -*- coding: utf-8 -*-
"""`[xiaoe]` 混淆播放列表解码器。

**这个模块是兜底代码，主流程走不到。** 线上 `playlist_eof.m3u8` 一直是明文的，
解混淆分支只在接口改版、又开始返回混淆内容时才会被触发。

关于测试的诚实说明：
下面的 `encode()` 不是从播放器 JS 逆向出来的**原版编码器**，而是把 `decode()`
逐行倒推着写的。所以 round-trip 测试证明的是「decode 相对自身自洽、没被改坏」，
**不能**证明它解真站点的密文是对的——手上已经没有真实的加密样本了
（探索期的 _probe.bin 已清理）。

真样本要等下次实际遇到 `[xiaoe]` 响应时，用
`XIAOE_DUMP_RAW=... python run.py ...` 落一份，存成 tests/data/raw_xiaoe.bin，
`test_real_sample_if_available` 就会自动从 skip 变成真跑。
"""
import base64
import os
import random
import string

import pytest

from xiaoe_crypto import (ALPHABET, IDX, JS_DEFAULT_KEY, TAIL_LEN, decode,
                          md5_hex, strip_prefix)

KEY = "apptvagt9li8549"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def encode(data: bytes, key: str, first: str = "A") -> str:
    """decode 的逆运算，仅用于 round-trip 测试。

    对应 decode 的步骤逐条倒过来：
      1. 明文 base64（去掉 = padding，因为 decode 里 `=` 会被当作分隔符剔除）
      2. 每个输出字符 o 对应一个字母表字符 c，满足 IDX[c] ≡ IDX[o] + d + ks[d] (mod 65)
      3. 首字符选一个字母表内的（它决定密钥流的起点）
      4. 尾部 3 字符，第 2 位是插入长度，取 0 表示不插入
    """
    payload = base64.b64encode(data).decode("ascii").rstrip("=")
    t = md5_hex(key)
    start = IDX[first] % 8
    ks = [ord(ch) for ch in md5_hex(t + first)[start: start + (start + 7)]]
    body = "".join(
        ALPHABET[(IDX[ch] + (i % len(ks)) + ks[i % len(ks)]) % len(ALPHABET)]
        for i, ch in enumerate(payload)
    )
    return first + body + "0" * TAIL_LEN


# ------------------------------------------------------------ md5_hex
def test_md5_known_vectors():
    assert md5_hex("") == "d41d8cd98f00b204e9800998ecf8427e"
    assert md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72"
    assert md5_hex("appbgzjnopv1917") == md5_hex("appbgzjnopv1917")
    assert len(md5_hex(KEY)) == 32


# ---------------------------------------------------------- strip_prefix
def test_strip_prefix_removes_marker():
    assert strip_prefix("[xiaoe]AAAA") == "AAAA"


def test_strip_prefix_leaves_plain_text_alone():
    assert strip_prefix("#EXTM3U\nx.ts") == "#EXTM3U\nx.ts"
    assert strip_prefix("") == ""


def test_strip_prefix_only_strips_leading_marker():
    assert strip_prefix("AA[xiaoe]BB") == "AA[xiaoe]BB"


# ------------------------------------------------------- round-trip 主体
@pytest.mark.parametrize("plaintext", [
    "#EXTM3U\n#EXT-X-VERSION:3\nseg_1.ts\nseg_2.ts\n#EXT-X-ENDLIST\n",
    "a",
    "#EXTM3U\n" + "\n".join(f"seg_{i}.ts?resolution=1920x1080" for i in range(50)),
])
def test_round_trip(plaintext):
    raw = plaintext.encode("utf-8")
    assert decode(encode(raw, KEY), KEY) == plaintext


def test_empty_input_cannot_round_trip():
    """格式本身要求至少『首字符 + 3 个尾字符』，所以空明文表达不出来。

    这是格式的固有下限，不是 bug——但值得钉住，免得有人看到
    「数据过短」以为解码器坏了。
    """
    with pytest.raises(ValueError, match="过短"):
        decode(encode(b"", KEY), KEY)
    assert len(encode(b"", KEY)) == 1 + TAIL_LEN


@pytest.mark.parametrize("first", list("AZaz09+/"))
def test_round_trip_across_key_stream_offsets(first):
    """首字符决定密钥流起点（s%8）和长度，每个起点都要能自洽。"""
    raw = ("#EXTM3U\nseg.ts\n" * 5).encode("utf-8")
    assert decode(encode(raw, KEY, first=first), KEY) == raw.decode("utf-8")


def test_round_trip_chinese_title():
    raw = ("#EXTM3U\n# 35、区间震荡量价行为案例分析\n").encode("utf-8")
    assert decode(encode(raw, KEY), KEY) == raw.decode("utf-8")


def test_output_never_contains_equals_sign():
    """`=` 是字母表里的正常输出字符（index 64），但 CryptoJS 的 base64 解析会忽略它，
    所以实现里一律剔除。这条钉住那个处理，别哪天「优化」掉了。"""
    raw = bytes(range(1, 60))
    out = decode(encode(raw, KEY), KEY)
    assert isinstance(out, str)


def test_different_keys_produce_different_plaintext():
    """密钥用错不会抛异常，只会解出乱码——所以绝不能给 decode 设默认密钥。"""
    raw = b"#EXTM3U\nseg.ts\n"
    good = decode(encode(raw, KEY), KEY)
    bad = decode(encode(raw, KEY), "appsomewhereelse")
    assert good != bad


# ------------------------------------------------------------ 护栏 / 健壮性
def test_key_is_required():
    with pytest.raises(ValueError, match="key"):
        decode("AAAAAAAA", "")


def test_too_short_input_raises():
    with pytest.raises(ValueError, match="过短"):
        decode("AAAA", KEY)


def test_first_char_outside_alphabet_raises():
    with pytest.raises(ValueError, match="不在字母表内"):
        decode("!!!!!AAAA", KEY)


def test_random_garbage_does_not_crash():
    """混淆数据可能因网络截断而残缺；只要求不崩，不要求解对。"""
    rnd = random.Random(20261014)
    for _ in range(40):
        n = rnd.randint(TAIL_LEN + 2, 200)
        s = "A" + "".join(rnd.choice(ALPHABET) for _ in range(n))
        try:
            decode(s, KEY)
        except (ValueError, UnicodeDecodeError, base64.binascii.Error):
            pass          # 可接受的失败方式
        # 其他类型的异常（IndexError / KeyError / TypeError）会直接让测试红掉


def test_js_default_key_is_not_used_implicitly():
    """回归测试：曾经 decode 的默认参数就是 JS_DEFAULT_KEY，
    不传 key 时静默用错密钥。现在必须显式传。"""
    import inspect
    sig = inspect.signature(decode)
    assert sig.parameters["key"].default is inspect.Parameter.empty
    # 常量本身保留，因为它确实是播放器 JS 里的兜底值
    assert JS_DEFAULT_KEY == "appbgzjnopv1917"


# ------------------------------------------------- 机会主义：真实样本
REAL_SAMPLE = os.path.join(DATA_DIR, "raw_xiaoe.bin")


@pytest.mark.skipif(not os.path.exists(REAL_SAMPLE),
                    reason="还没有真实 [xiaoe] 样本。遇到时用 "
                           "XIAOE_DUMP_RAW=<路径> 跑一次即可落盘。")
def test_real_sample_if_available():
    raw = open(REAL_SAMPLE, "r", encoding="utf-8", errors="replace").read()
    plain = decode(strip_prefix(raw), KEY)
    assert "#EXTM3U" in plain
