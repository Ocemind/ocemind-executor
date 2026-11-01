"""界面上「错误说人话」的规则表测试。

规则写在 app.js 里（纯前端），所以这里直接把那段源码切出来丢给 node 跑，
测的是真正会下发到浏览器的那份代码，而不是它的 Python 复刻。
没装 node 就跳过——这条测试是给改文案的人兜底的，不该挡住构建。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "ocemind" / "web" / "static" / "app.js"

START = "const ERROR_HINTS = ["
END = "\nlet toastTimer"


def _rules_source() -> str:
    text = APP_JS.read_text(encoding="utf-8")
    i = text.index(START)
    j = text.index(END, i)
    return text[i:j]


def _node_available() -> bool:
    return shutil.which("node") is not None


# (原始错误, 期望的关键词) —— 关键词取人话那半句里最有辨识度的字
CASES = [
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot.", "人机验证"),
    ("ERROR: [youtube] x: This video is available to this channel's members on level: 会员.", "会员"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "私密"),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country", "地区限制"),
    ("ERROR: unable to download webpage: HTTP Error 429: Too Many Requests", "限流"),
    ("ERROR: [youtube] x: Video unavailable", "不存在"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "拒绝"),
    ("ERROR: Sign in required, HTTP Error 401", "登录态过期"),
    ("ERROR: Unsupported URL: https://example.com/watch/1", "下不了"),
    ("ERROR: Requested format is not available", "清晰度"),
    ("OSError: [Errno 28] No space left on device", "磁盘满"),
    ("ERROR: unable to download: timed out", "网络"),
    ("ERROR: ffmpeg exited with code 1", "转码"),
]


def test_error_rules(tmp_path):
    if not _node_available():
        pytest.skip("没有 node，跳过前端规则表测试")

    payload = json.dumps([[raw, want] for raw, want in CASES], ensure_ascii=False)
    body = """
const cases = __CASES__;
const out = cases.map(([raw, want]) => ({
  raw,
  want,
  hint: humanError(raw),
  ok: humanError(raw).includes(want),
}));

// 顺序守卫：同时命中「会员」和「403」时，必须先讲会员
const both = "This video is available to this channel's members, HTTP Error 403 Forbidden";
const orderOk = humanError(both).includes("会员");

// 原始错误：跳过 [debug] 行，取最有信息量的一条
const raw = rawError("[debug] Command-line config: x\\n[debug] yt-dlp 2026.10\\nERROR: boom 429");
const rawOk = raw === "ERROR: boom 429";

// 不认识的短错误原样透出
const passOk = humanError("标题太短") === "标题太短";

console.log(JSON.stringify({results: out, orderOk, raw, rawOk, passOk}));
""".replace("__CASES__", payload)
    script = _rules_source() + body
    js_file = tmp_path / "rules.mjs"
    js_file.write_text(script, encoding="utf-8")

    proc = subprocess.run(
        ["node", str(js_file)], capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])

    bad = [r for r in data["results"] if not r["ok"]]
    assert not bad, "这些错误没翻成人话：" + json.dumps(
        [{"raw": r["raw"], "got": r["hint"], "want": r["want"]} for r in bad],
        ensure_ascii=False,
        indent=2,
    )
    assert data["orderOk"], "会员应该排在 403 前面，否则会被吃掉"
    assert data["rawOk"], f"rawError 取错了：{data['raw']!r}"
    assert data["passOk"], "认不出的错误应该原样透出，别吞"
