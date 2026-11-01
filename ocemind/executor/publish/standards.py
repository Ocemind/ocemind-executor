"""发布标准：写死在文件里，由代码强制执行。

放在这里而不是塞进提示词，是因为提示词只是"建议"——模型可以不理。
这份配置过不了就直接 422，Agent 绕不过去，这才叫"按我制定的标准"。

格式用 JSON 而不是 YAML：不想为了读七个字段引入 PyYAML。
文件在 `ocemind/executor/standards.json`，也可以放到数据目录里覆盖默认值。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT = {
    "title_max": 30,
    "title_min": 4,
    "desc_max": 2000,
    "hashtag_max": 8,
    "hashtag_required": False,
    "cover_ratio": [3, 4],          # 竖版 3:4
    "cover_ratio_tolerance": 0.02,
    "banned_words": ["最好的", "第一", "国家级", "史上最", "百分百", "绝对有效"],
    "require_cover": False,
    "min_video_seconds": 8,
}

_HERE = Path(__file__).resolve().parent.parent / "standards.json"


def load(config=None) -> dict:
    data = dict(DEFAULT)
    for path in (_HERE,):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            data.update({k: v for k, v in loaded.items() if k in DEFAULT})
    if config is not None:
        override = getattr(config, "home", None)
        if override:
            try:
                loaded = json.loads((Path(override) / "standards.json")
                                    .read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update({k: v for k, v in loaded.items() if k in DEFAULT})
            except (OSError, ValueError):
                pass
    return data


def check(meta: dict, rules: dict | None = None) -> list[str]:
    """返回问题列表。空列表 = 可以发。"""
    rules = rules or load()
    problems: list[str] = []

    title = str(meta.get("title") or "").strip()
    if len(title) < int(rules["title_min"]):
        problems.append(f"标题太短（{len(title)} 字，至少 {rules['title_min']}）")
    if len(title) > int(rules["title_max"]):
        problems.append(f"标题超长（{len(title)} 字，上限 {rules['title_max']}）")

    desc = str(meta.get("desc") or "")
    if len(desc) > int(rules["desc_max"]):
        problems.append(f"简介超长（{len(desc)} 字，上限 {rules['desc_max']}）")

    tags = meta.get("hashtags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,，\s#]+", tags) if t]
    if len(tags) > int(rules["hashtag_max"]):
        problems.append(f"话题过多（{len(tags)} 个，上限 {rules['hashtag_max']}）")
    if rules.get("hashtag_required") and not tags:
        problems.append("必须有话题标签")

    blob = title + "\n" + desc
    for word in rules.get("banned_words") or []:
        if word and word in blob:
            problems.append(f"含违禁词：{word}")

    cover = meta.get("cover")
    if cover and rules.get("cover_ratio"):
        ratio = _image_ratio(Path(str(cover)))
        want = rules["cover_ratio"]
        if ratio and abs(ratio - want[0] / want[1]) > float(rules["cover_ratio_tolerance"]):
            problems.append(
                f"封面比例 {ratio:.2f}，要求 {want[0]}:{want[1]}"
                f"（{want[0] / want[1]:.2f}）")
    if rules.get("require_cover") and not cover:
        problems.append("必须上传封面")

    return problems


def _image_ratio(path: Path) -> float | None:
    """只读文件头拿宽高，不加载像素。读不出就返回 None，不误判。"""
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.width / img.height if img.height else None
    except Exception:  # noqa: BLE001
        return None
