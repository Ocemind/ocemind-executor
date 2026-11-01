"""重复任务判定的测试（纯前端逻辑，切出源码交给 node 跑）。

比的是「同一条视频」而不是字符串：分享文案里的 spm / share_* 跟踪参数、
参数顺序都不能影响判定，否则这个提示永远不出现。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "ocemind" / "web" / "static" / "app.js"

SLICES = [
    ("const URL_IN_TEXT", "/* ───────────────────────── 初始化"),
    ("const TRACK_PARAMS", "\nfunction renderDup"),
]


def _source() -> str:
    text = APP_JS.read_text(encoding="utf-8")
    parts = []
    for start, end in SLICES:
        i = text.index(start)
        parts.append(text[i:text.index(end, i)])
    return "\n".join(parts)


def test_duplicate_detection(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("没有 node，跳过前端规则表测试")

    now = 1_700_000_000          # created_at 用的是 Unix 秒
    tasks = [
        # 已完成的一条 B 站视频（带分享链接留下的跟踪参数）
        {"id": "done1", "status": "done", "created_at": now - 180,
         "url": "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.999"},
        # 失败的不算重复
        {"id": "err1", "status": "error", "created_at": now - 60,
         "url": "https://www.bilibili.com/video/BVERR"},
        # 能力任务（转写等）不参与重复判定
        {"id": "cap1", "status": "done", "created_at": now - 60,
         "capability": "transcribe.asr", "url": "D:/v/a.mp4"},
        # 从短链加进来的任务：原始短链要能对上
        {"id": "short1", "status": "done", "created_at": now - 30,
         "url": "https://www.youtube.com/watch?v=abc123",
         "url_original": "https://b23.tv/xyz"},
        # YouTube 同一条，参数顺序不同
        {"id": "yt1", "status": "downloading", "created_at": now - 5,
         "url": "https://youtube.com/watch?v=abc123&t=30"},
    ]

    payload = json.dumps(tasks, ensure_ascii=False)
    body = """
const state = { tasks: __TASKS__, dup: null };
__SRC__

const cases = [
  // [输入, 期望命中的任务 id（""=不算重复）]
  ["https://www.bilibili.com/video/BV1xx411c7mD?vd_source=abc&spm_id_from=1", "done1"],
  ["看看这个 https://www.bilibili.com/video/BV1xx411c7mD 好玩", "done1"],
  ["https://www.bilibili.com/video/BV1OTHER", ""],
  ["https://www.bilibili.com/video/BVERR", ""],
  ["D:/v/a.mp4", ""],
  ["https://b23.tv/xyz", "short1"],
  ["https://www.youtube.com/watch?v=abc123&t=30", "yt1"],
  ["https://m.youtube.com/watch?v=abc123", ""],
];

const out = cases.map(([input, want]) => {
  const hit = findDuplicate(input);
  return { input, want, got: hit ? hit.id : "", ok: (hit ? hit.id : "") === want };
});

const ago = [
  [0, "刚刚"],
  [30 * 1000, "刚刚"],
  [180 * 1000, "3 分钟前"],
  [3 * 3600 * 1000, "3 小时前"],
  [30 * 3600 * 1000, "昨天"],
  [5 * 86400 * 1000, "5 天前"],
].map(([ms, want]) => {
  const got = agoText(Date.now() / 1000 - ms / 1000);   // 后端给的是秒
  return { ms, want, got, ok: got === want };
});

console.log(JSON.stringify({ dup: out, ago }));
""".replace("__TASKS__", payload).replace("__SRC__", _source())

    js_file = tmp_path / "dup.mjs"
    js_file.write_text(body, encoding="utf-8")
    proc = subprocess.run(["node", str(js_file)], capture_output=True,
                          text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])

    bad = [c for c in data["dup"] if not c["ok"]]
    assert not bad, "重复判定不对：" + json.dumps(bad, ensure_ascii=False, indent=2)
    bad_ago = [c for c in data["ago"] if not c["ok"]]
    assert not bad_ago, "时间说法不对：" + json.dumps(bad_ago, ensure_ascii=False, indent=2)
