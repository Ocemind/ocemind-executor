"""各平台发布页的定位规则。

说明白一句：**平台改版会打破这些选择器**。所以每条规则都不是"一次写死用三年"，
而是集中放在这张表里，改版时只改一处。找不到选择器时引擎会明确报
"页面结构和预期不一致"，而不是静默点错地方。

发布一律走"辅助模式"：程序把文件传上去、把标题话题填好，停在预览页，
人看一眼再点发布。全自动无人确认发布是明确不做的。
"""
from __future__ import annotations

# title_input / desc_input: CSS 选择器
# submit: 提交按钮；留空表示由人点
# verify: 回到内容管理页后，判断"这条真的存在"的 JS 表达式
#         约定用 window.__ocm_title 注入标题

ADAPTERS: dict[str, dict] = {
    "bilibili": {
        "name": "B站",
        "publish_url": "https://member.bilibili.com/platform/upload/video/frame",
        "manage_url": "https://member.bilibili.com/platform/upload-manager/article",
        "file_input": "input[type=file]",
        "title_input": "input[placeholder*='标题']",
        "desc_input": "div[contenteditable='true']",
        "submit": "",
        "verify": "document.body.innerText.includes(window.__ocm_title)",
        "notes": "先上传稿件，标题在稿件信息区；分区需要人工选择",
    },
    "douyin": {
        "name": "抖音",
        "publish_url": "https://creator.douyin.com/creator-micro/content/upload",
        "manage_url": "https://creator.douyin.com/creator-micro/content/manage",
        "file_input": "input[type=file]",
        "title_input": "input[placeholder*='填写作品标题']",
        "desc_input": "div[data-placeholder*='添加作品简介'], div[contenteditable='true']",
        "submit": "",
        "verify": "document.body.innerText.includes(window.__ocm_title)",
        "notes": "上传后用自然流量页的标题框；话题建议用正文里的 # 标签",
    },
    "xiaohongshu": {
        "name": "小红书",
        "publish_url": "https://creator.xiaohongshu.com/publish/publish",
        "manage_url": "https://creator.xiaohongshu.com/new/note-manager",
        "file_input": "input[type=file]",
        "title_input": "input[placeholder*='填写标题']",
        "desc_input": "div[contenteditable='true']",
        "submit": "",
        "verify": "document.body.innerText.includes(window.__ocm_title)",
        "notes": "先选'上传视频'页签再传文件；封面上传是独立一步",
    },
    "weibo": {
        "name": "微博",
        "publish_url": "https://weibo.com",
        "manage_url": "https://weibo.com/at/weibo",
        "file_input": "input[type=file]",
        "title_input": "",
        "desc_input": "textarea[placeholder*='分享'], div[contenteditable='true']",
        "submit": "",
        "verify": "document.body.innerText.includes(window.__ocm_title)",
        "notes": "微博没有独立标题框，标题作为正文首句；认证账号才有视频入口",
    },
    "shipinhao": {
        "name": "视频号",
        "publish_url": "https://channels.weixin.qq.com/platform/post/create",
        "manage_url": "https://channels.weixin.qq.com/platform/post/list",
        "file_input": "input[type=file]",
        "title_input": "input[placeholder*='标题']",
        "desc_input": "div[contenteditable='true']",
        "submit": "",
        "verify": "document.body.innerText.includes(window.__ocm_title)",
        "notes": "需要在微信里扫码登录后再打开创作页；短标题与描述是两个框",
    },
    "kuaishou": {
        "name": "快手",
        "publish_url": "https://cp.kuaishou.com/article/publish/video",
        "manage_url": "https://cp.kuaishou.com/article/manage/video",
        "file_input": "input[type=file]",
        "title_input": "input[placeholder*='标题']",
        "desc_input": "div[contenteditable='true']",
        "submit": "",
        "verify": "document.body.innerText.includes(window.__ocm_title)",
        "notes": "上传与填写在同一页；发布按钮在右下角",
    },
}


def get(platform: str) -> dict | None:
    return ADAPTERS.get(platform)


def platforms() -> list[dict]:
    return [{"key": k, **{f: v.get(f) for f in ("name", "notes")}}
            for k, v in ADAPTERS.items()]
