# 接口链路 —— 改版时照这张表定位

> 这份文档存在的唯一目的：**课程平台改版时，能在几分钟内知道该动哪一行代码。**
>
> 用法：先跑
> ```bash
> python tools/diagnose.py --url "<课程链接>"
> ```
> 拿到「首个失败阶段」，再对照下面的表找到对应的代码位置。
> 别再从零逆向一遍 —— 2026-10 那次花了几小时，大部分时间花在重新发现这些事上。

---

## 总览：三跳

```
课程链接
  └─ 第 1 跳  get_lookback_list       线路列表 + CDN 签名参数
       └─ 第 2 跳  playlist_eof.m3u8  分片清单
            └─ 第 3 跳  分片 .ts        视频数据（裸 TS）
                 └─ ffmpeg concat 合并 → MP4

（旁路）base_info  只用来取标题做文件名，取不到就退回默认名，不影响下载
```

**只有 `ko_token` 这一条 cookie 是必需的**，挂域 `{app_id}.h5.xiaoeknow.com`
（或 `xiaoeknow.com` 根域）。其余都是埋点/统计类，实测单独用 `ko_token` 就能调通。
详见 [cookie 需求](#cookie-需求)。

---

## 第 1 跳 · `get_lookback_list`

**代码位置**：[`xiaoe_dl/core.py`](../xiaoe_dl/core.py) 的 `lookback_api()`
（拼 URL）、`parse_lookback()`（解响应）、`get_lookback()`（串起来）

**URL**（`protection=0` 是关键，去掉会拿到加密流）：

```
https://{app_id}.h5.xiaoeknow.com/_alive/v3/get_lookback_list?app_id={app_id}&alive_id={alive_id}&protection=0
```

**请求头**：`User-Agent`、`Referer: https://{app_id}.h5.xiaoeknow.com/`、
`Origin: https://xiaoeknow.com`、`Cookie`。见 `http_get()`。

**期望响应形状**：

```jsonc
{
  "code": 0,
  "msg": "ok",
  "data": [                       // 线路数组，通常 3 条
    {
      "line_name": "线路1",
      "line_sharpness": [         // 每条线路下的可选清晰度
        {
          "cloud": "tx",          // tx / hw / byte
          "url": "https://.../playlist_eof.m3u8",
          "ext": {                // ★ 分片地址靠这三个字段拼，最关键的字段
            "host":  "https://ttv-tos.xet.tech",
            "path":  "/vod/xxxx",
            "param": "sign=...&t=...&us=..."   // ★ 会过期
          }
        }
      ]
    }
  ]
}
```

**判定为正常的条件**：`code == 0` 且 `data` 非空数组且每项有 `ext.host` + `url`。

### ★ 这里有两个必须记住的坑

1. **`ext.param` 是签名，会过期。** `sign` / `t` / `us` 三个参数有有效期，
   所以**每次下载都必须重新调这一跳**，不能把分片地址缓存下来复用。
   诊断时报「分片 CDN 连不上」但过一会儿重跑又好了，就是这个原因。

2. **`code != 0` 不代表接口挂了。** 最常见的是 `11302`（登录态失效），
   那是 cookie 的问题，不是改版。**先看错误码，再怀疑接口**。
   错误码翻译表在 `core.py` 的 `CODE_HINT`。

---

## 第 2 跳 · `playlist_eof.m3u8`

**代码位置**：`core.py` 的 `grab_playlist()`（下载 + 必要时解混淆）、
`parse_segments()`（切出分片清单）

**URL**：直接用第 1 跳响应里 `line_sharpness[].url` 那个值，不用自己拼。

**请求头**：同第 1 跳。

**期望内容**：**明文** HLS 播放列表，以 `#EXTM3U` 开头：

```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:5
#EXTINF:5.000,
seg_00001.ts
seg_00002.ts
...
#EXT-X-ENDLIST
```

**判定为正常的条件**：第一行是 `#EXTM3U`，且切出的分片数 > 0
（实测这部课是 **796** 个）。

### 关于 `[xiaoe]` 混淆分支

`grab_playlist()` 里有一段：如果响应不以 `#EXTM3U` 开头，就当它是
`[xiaoe]` 混淆内容，调 `crypto.decode()` 解。

**这段是兜底代码，主流程走不到** —— 线上 `playlist_eof.m3u8` 一直是明文的。
它存在的意义是：万一站方哪天又开始返回混淆内容，不至于当场抓瞎。

- 触发时会打印 `[crypto] 已解混淆播放列表`。**看到这行要留意**，说明站方改了。
- 手上已经没有真实的混淆样本了（探索期的 `_probe.bin` 已清理），所以
  `tests/test_crypto.py` 测的是「解码器相对自身自洽」，**不能证明对真样本解对**。
- 真遇到时用 `XIAOE_DUMP_RAW=<路径>` 跑一次，把原始响应落盘存成
  `tests/data/raw_xiaoe.bin`，那个测试会自动从 skip 变成真跑。

---

## 第 3 跳 · 分片 `.ts`

**代码位置**：`core.py` 的 `abs_url()`（拼地址）、`download_all()`（并发下载）、
`merge()`（ffmpeg 合并）

**地址拼法**（`abs_url()` 的全部逻辑）：

```
{ext.host}/{ext.path}/{分片名}  +  ("&" if 已含 "?" else "?")  +  {ext.param}
```

那个 `&` / `?` 的判断别改：分片名本身**可能已经带了 `?resolution=1920x1080`**
这类查询串，这时要接 `&` 而不是 `?`。

**请求头**：`User-Agent`、`Referer: {h5}/`（CDN 有时校验），`Range` **见下**。

**判定为正常的条件**：HTTP 200/206，且**首字节是 `0x47`**（TS 的同步字节）。
拿到别的东西说明 CDN 返回的是错误页而不是视频流 —— `diagnose` 的
`5-segment` 阶段就是查这个。

### ★ 两种寻址方式：整文件 vs 字节区间

分片清单里有两种分片名，**必须区分**：

| 形态 | 例子 | 怎么取 |
|------|------|--------|
| **整文件** | `156174317_1257317718_1.ts?resolution=1920x1080` | 直接 GET，一个分片就是一个文件 |
| **字节区间** | `3877768290_2020660754_1.ts?end=300235&start=0&resolution=1920x1080&type=mpegts&sign=...&t=...` | 同一个文件名被几百个分片共用，只有 `start`/`end` 不同 |

**区间寻址必须靠 HTTP `Range` 头取，不能指望 URL 上的 `start`/`end`。**

> 2026-10-15 踩的坑：这个 CDN **完全不看 `start`/`end` 查询参数** —— 带不带、
> 写多少，都返回**整个文件**（4488312 字节 / 60.08 秒）。当时 `abs_url` 只负责
> 拼地址、`download_all` 不带 `Range` 头，于是 906 个分片各下回一遍完整文件，
> 合出来的视频是 906 × 60.08s ≈ **15.3 小时**（真实内容只有 66 分钟）。
>
> 注意 `start`/`end` **不能从 URL 里删掉**：URL 是带签名的，查询串在签名覆盖
> 范围内。它们和 `Range` 头是两回事，两个都要有。

**代码位置**：`core.seg_range()` 解析区间（`(start, end)` **闭区间，两端都含**）→
`core.http_get(byte_range=...)` 翻成 `Range: bytes=s-e` → `core.download_all()` 串起来。

闭区间这件事别搞错：`start=300236&end=749555` → `Range: bytes=300236-749555`
→ 449320 字节（不是 449319）。

**两道护栏**（都别删）：

1. `http_get` 里 —— 要了区间却收到 200 + 超过区间长度的响应 → 直接抛错。
   这种数据「看着有内容」，拼起来却是错的，静默接受就会再出一次 15 小时视频
2. `download_all` 的缓存判据 —— 区间分片**不能只看「文件够大就算下好了」**。
   脏缓存正是「每片都是整个文件」，比区间长 15 倍；旧判据会把它当有效缓存跳过，
   于是重跑一万次也修不好。区间响应只可能 ≤ 区间长度

---

## 旁路 · `base_info`（只为了取标题）

**代码位置**：`core.py` 的 `get_title()`

```
https://{app_id}.h5.xiaoeknow.com/_alive/v3/base_info?app_id={app_id}&resource_id={alive_id}&type={t}
```

`type` 依次试 `2`、`1`，哪个能出 `data.alive_info.title` 就用哪个。
**取不到就返回 None，回退到默认文件名，绝不影响下载。**
这一跳故意写得很宽容（`retries=2` + 吞掉所有异常）。

---

## 错误码表

代码位置：`core.py` 的 `CODE_HINT`。**改版时站方可能加新码或改语义，
发现新码就往这张表里补一行。**

| code | 含义 | 该做什么 |
|------|------|---------|
| `0` | 成功 | — |
| `11302` | 登录态失效 | **不是改版**。重新导入 cookies.txt，或用 `--login` 登一次 |
| `11303` | 没有该课程的观看权限 | 换个账号，或确认是不是买过这门课 |
| `11304` | 课程不存在或已下架 | 检查链接 |
| 其他 | 未知 | 去 [`_explore/`](../_explore/) 翻当年的抓包脚本对比响应形状 |

---

## Cookie 需求

| 项 | 值 |
|---|---|
| 必需的 cookie | **只有 `ko_token`**（隔离测试验证过：单留它就能调通全部三跳） |
| 挂载域 | `{app_id}.h5.xiaoeknow.com`，或 `xiaoeknow.com` 根域 |
| 有效期 | 短期凭证，实测约 2 天。过期后接口回 `11302` |
| 格式 | Netscape（7 个 TAB 分隔字段），`#HttpOnly_` 前缀行要当有效数据处理 |

**多店铺注意**：profile 访问过多个 `{app_id}.h5.xiaoeknow.com` 时，会存在
**多条同名 `ko_token`**。全拼进一个 Cookie 头服务端行为未定义。所以一律走
`cookies.cookie_header_for_app()` 按域过滤，**不要**自己拼 Cookie 头。

相关代码：`xiaoe_dl/cookies.py`（解析/过滤/状态）、`xiaoe_dl/auth.py`（拿登录态）。

---

## 改版对照表 ★

**拿 `diagnose` 报出的首个失败阶段，在下表里找那一行，直接去改对应位置。**

| 断点阶段 | 症状 | 最可能变的东西 | 去改/去看 |
|---------|------|--------------|----------|
| `1-cookie` | 没有有效 ko_token | — | 不是改版。重新导入 cookies，或 `python -m xiaoe_dl.auth --login --app-id <id>` |
| `2-url` | 抠不出 app_id / alive_id | 链接的参数名变了 | `core.parse_course_url()` —— 改参数名或正则 |
| `3-api` | HTTP 错 / JSON 解析错 | **接口路径或域名前缀变了** | `core.h5_base()` / `core.lookback_api()` |
| `3-api` | `code` 是个新数字 | 站方加了新错误码 | `core.CODE_HINT` 补一行；顺便看 `msg` 说什么 |
| `3-api` | `code=0` 但 `data` 为空 | 响应结构变了 | `core.parse_lookback()` |
| `3-api` | `data` 拿到了但挑不出线路 | `ext.host` / `url` 字段改名或挪位置了 | `core.pick_line()` —— 它按 `ext.host` + `url` 过滤 |
| `4-playlist` | m3u8 拿不到 | `line_sharpness[].url` 不是 m3u8 了 | 先看 `3-api` 的响应原文 |
| `4-playlist` | 拿到了但解析不出分片 | 清单格式变了（不再是裸 HLS） | `core.parse_segments()` |
| `4-playlist` | 打印了 `[crypto]` | **站方又开始返回混淆内容了** | 见上文「关于 `[xiaoe]` 混淆分支」。用 `XIAOE_DUMP_RAW` 落样本 |
| `5-segment` | 拼出来的地址 404 / 403 | **`ext.host` / `ext.path` / `ext.param` 的拼法变了** | `core.abs_url()` |
| `5-segment` | 首字节不是 `0x47` | CDN 返回错误页，多半是签名参数名变了 | 看 `ext.param` 原文里都有什么 |
| `5-segment` | **报「CDN 忽略了 Range 头」** | CDN 不再支持 `Range`，或分片改回整文件寻址 | 对比第 4 跳报的寻址方式。若真不支持 Range 了，区间分片这条路要走 CDN 参数 |
| `5-segment` | 时好时坏 | 签名过期（正常现象，不是改版） | 重跑即可，见第 1 跳的坑 1 |
| `6-ffmpeg` | 产物**时长明显偏长**（如 15 小时 vs 66 分钟） | **区间分片没带 `Range` 头，每片下回了整个文件** | 见上文「两种寻址方式」。先看第 4 跳报的是哪种寻址 |
| `6-ffmpeg` | 合并失败 | 分片容器从 TS 变成 fMP4 了 | `core.merge()` 的 concat 方式 |

**排查顺序永远是**：`diagnose` 定断点 → 本表查到代码位置 → 用
`http_get` 的原始响应确认形状 → 改那一个函数 → **重跑 `diagnose` 确认全绿** →
跑 `python -m pytest`（纯函数都有测试兜底）。

---

## 已经不work的老路子（别再试）

见 README 的「走过的弯路」一节。这里只强调一条跟本文相关的：

**不要试图用 `--cookies-from-browser` 或任何方式去读系统 Chrome 的 cookie 库。**
Chrome 127+ 的 App-Bound Encryption 让密钥绑定在「创建它的浏览器二进制 +
profile 路径」上，别的进程读到的只是解不开的密文。
`auth.py` 用 Playwright 自带的 Chromium + 独立 profile，**绝不要指向用户真实
Chrome 的 `User Data`** —— 那既踩 ABE，又踩 Chrome 136+ 对默认目录禁用远程调试的策略。
