"""平台定义：登录入口、创作后台、登录态判定依据。

`key_cookies` 用来做离线判活：Chromium 的 Cookies 表里 `expires_utc` 是明文，
不解密就能算出还剩几天。这是预警的离线信号，权威性低于真实探测，但零成本。
"""
from __future__ import annotations

PLATFORMS: dict[str, dict] = {
    "douyin": {
        "name": "抖音",
        "home": "https://www.douyin.com",
        "login": "https://www.douyin.com",
        "publish": "https://creator.douyin.com/creator-micro/content/upload",
        # 创作者后台未登录会跳走
        "login_probe": "https://creator.douyin.com/creator-micro/content/upload",
        "logged_out_markers": ("/login", "passport"),
        "key_cookies": ("sessionid", "sessionid_ss", "sid_guard"),
        "can_download": True,
        "can_publish": True,
    },
    "bilibili": {
        "name": "B站",
        "home": "https://www.bilibili.com",
        "login": "https://passport.bilibili.com/login",
        "publish": "https://member.bilibili.com/platform/upload/video/frame",
        "login_probe": "https://member.bilibili.com/platform/home",
        "logged_out_markers": ("passport.bilibili.com", "/login"),
        "key_cookies": ("SESSDATA", "bili_jct", "DedeUserID"),
        "can_download": True,
        "can_publish": True,
    },
    "xiaohongshu": {
        "name": "小红书",
        "home": "https://www.xiaohongshu.com",
        "login": "https://www.xiaohongshu.com",
        "publish": "https://creator.xiaohongshu.com/publish/publish",
        "login_probe": "https://creator.xiaohongshu.com/publish/publish",
        "logged_out_markers": ("/login", "passport"),
        "key_cookies": ("web_session", "a1"),
        "can_download": True,
        "can_publish": True,
    },
    "weibo": {
        "name": "微博",
        "home": "https://weibo.com",
        "login": "https://weibo.com/login.php",
        "publish": "https://weibo.com/upload/channel",
        "login_probe": "https://weibo.com",
        "logged_out_markers": ("login.php", "passport.weibo.com"),
        "key_cookies": ("SUB", "SUBP"),
        "can_download": True,
        "can_publish": True,
    },
    "shipinhao": {
        "name": "视频号",
        "home": "https://channels.weixin.qq.com",
        "login": "https://channels.weixin.qq.com/login.html",
        "publish": "https://channels.weixin.qq.com/platform/post/create",
        "login_probe": "https://channels.weixin.qq.com/platform",
        "logged_out_markers": ("login.html", "/login"),
        "key_cookies": ("sessionid", "wxuin"),
        "can_download": False,   # 无 extractor，手动存文件
        "can_publish": True,
    },
    "kuaishou": {
        "name": "快手",
        "home": "https://www.kuaishou.com",
        "login": "https://passport.kuaishou.com/pc/account/login",
        "publish": "https://cp.kuaishou.com/article/publish/video",
        "login_probe": "https://cp.kuaishou.com/",
        "logged_out_markers": ("passport", "/login"),
        "key_cookies": ("did", "kuaishou.web.cp.api_st"),
        "can_download": False,   # 当前 yt-dlp 无 extractor
        "can_publish": True,
    },
}

# 只有下载能力、没有发布场景的平台
EXTRA_DOWNLOAD_ONLY = {
    "youtube": {"name": "YouTube", "can_download": True, "can_publish": False},
    "x": {"name": "X", "can_download": True, "can_publish": False},
}


def platform_info(key: str) -> dict:
    return PLATFORMS.get(key) or EXTRA_DOWNLOAD_ONLY.get(key) or {"name": key}


def all_platforms() -> list[dict]:
    merged = dict(PLATFORMS)
    merged.update(EXTRA_DOWNLOAD_ONLY)
    return [{"key": k, **v} for k, v in merged.items()]
