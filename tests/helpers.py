# -*- coding: utf-8 -*-
"""测试共用常量与构造器。

安全红线：这里所有 cookie / 签名都是**合成的假数据**。
绝不把真实 cookie 或带签名的 URL 写进测试——仓是要进 git 的，
凭证一旦进了历史就洗不掉了。
"""

APP_ID = "appfake1234567890"
OTHER_APP = "appother999"
ALIVE_ID = "l_0000000000000000000000000000001"

# 固定的“现在”，让所有时间断言都是确定性的
NOW = 1_800_000_000.0
DAY = 86400


def cookie_line(domain, name, value, expires, path="/", secure=True):
    """拼一行 Netscape 格式的 cookie。"""
    return "\t".join([
        domain,
        "TRUE" if domain.startswith(".") else "FALSE",
        path,
        "TRUE" if secure else "FALSE",
        str(expires),
        name,
        value,
    ])


def default_cookie_lines():
    """默认 cookie 文件内容。刻意包含几个关键形态：

      - 本 app 子域的 ko_token（有效）
      - 另一个 app 子域的 ko_token（有效）—— 验证按域隔离
      - 根域的 ko_token（有效）—— 共享凭证，应当被本 app 采纳
      - value 里带 `=` 的 cookie —— 切分时不能截断
      - 会话级 cookie（expires=0）
      - 已过期的 cookie
    """
    sub = f"{APP_ID}.h5.xiaoeknow.com"
    return [
        "# Netscape HTTP Cookie File",
        cookie_line(sub, "ko_token", "FAKE_TOKEN_THIS_APP", int(NOW) + 2 * DAY),
        cookie_line(f"{OTHER_APP}.h5.xiaoeknow.com", "ko_token",
                    "FAKE_TOKEN_OTHER_APP", int(NOW) + 2 * DAY),
        cookie_line(".xiaoeknow.com", "ko_token",
                    "FAKE_TOKEN_ROOT_DOMAIN", int(NOW) + 2 * DAY),
        cookie_line(sub, "sid", "abc=def=ghi", int(NOW) + 2 * DAY),
        cookie_line(sub, "session_only", "no-expiry", 0),
        cookie_line(sub, "expired", "stale", int(NOW) - DAY),
    ]


def lookback_payload(code=0, msg=None, n_lines=2):
    """一份 get_lookback_list 的合成响应。

    形状照着真接口抄：data 是线路数组，每条有 line_name，
    line_sharpness 里每项有 cloud / url / ext{host,path,param}。
    """
    lines = [{
        "line_name": f"线路{i}",
        "line_sharpness": [{
            "cloud": "tx",
            "url": f"https://cdn.example.com/line{i}/playlist_eof.m3u8",
            "ext": {
                "host": f"https://cdn{i}.example.com",
                "path": f"/vod/line{i}",
                "param": "sign=FAKE&t=0000&us=FAKE",
            },
        }],
    } for i in range(1, n_lines + 1)]
    return {"code": code, "msg": msg, "data": lines}
