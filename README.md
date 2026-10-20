# Timi 下载器

**小鹅通课程、平台视频、任意直链，一个窗口下完。**

- **小鹅通回放**：给个课程链接就出 1080p MP4。不需要 F12、不需要抓包、
  不需要往浏览器装插件
- **通用下载**：YouTube / B站 / X / 抖音… 交给 yt-dlp；任意直链走自研多线程
  Range 分片，实测 8 线程比单线程快约 9 倍
- **同一个任务队列**：并发数、限速、暂停续传、失败重试；课程和普通下载混在
  一张列表里

## 用法（三种）

### A. 桌面（推荐，给不碰命令行的人用）

桌面上双击 **`Timi 下载器`** → 粘贴链接 → 点「添加下载」。

- 粘贴课程链接会提示「需要登录」；点一次「登录一次」或导入 cookies 即可
- 课程任务显示分片进度（`231/796 片`），普通下载显示速度和剩余时间
- 平台视频会自动选择最佳音视频并合并
- 保存目录会记住上次用的
- 需要 `ffmpeg`（已在系统 PATH 上）；找不到会去常见安装位置兜底找
- 所有数据在一个目录：`%APPDATA%\xiaoe-dl\`
  （`settings.json` 设置、`tasks.json` 任务列表、`cookies.txt` 登录态、`app.log`）

默认是**网页界面套在原生窗口里**（pywebview）。两个备用入口：

| 想干什么 | 怎么做 |
|----------|--------|
| 用系统浏览器打开（好调试） | `python run.py --web` |
| 用旧的 Tk 界面 | `python run.py --tk`，或 `python tools/make_launcher.py --tk` 另建一个桌面入口 |
| 看控制台的报错 | 双击 `run.bat` |

没装 WebView2 / pywebview 时，原生窗口起不来会**自动退回浏览器**，不会一无所获。

#### 为什么桌面放的是快捷方式，不是 exe

这台机器开着**智能应用控制（Smart App Control，强制模式）**。它按微软 ISG 信誉
放行可执行文件：老 exe 攒够信誉所以曾经能跑，但**每重新打包一次就是一份新哈希、
要重新攒一次信誉**。实测 Phase 4 重建出来的 exe 直接被拒（退出码 126，
CodeIntegrity 事件 3077：未满足企业签名级别要求）。

快捷方式指向 `pythonw.exe` 跑 `run.py`：那是 Python 官方的签名二进制，本身就在
信誉库里，不受这条策略约束，而且**永远是最新代码**，不用再打包。

重建或修复启动器（换机器、项目挪位置之后跑一次）：

```bash
python tools/make_launcher.py
```

重构前打的那个 exe 已经不在桌面上了：它的能力（含自动登录）现在全在源码入口里，
留着只会多一个分叉。`dist/` 下的旧产物仅作归档，别拿它当入口。

> 若你确实想要一个能双击的独立 exe：得先关掉智能应用控制。那是**不可逆**的
> （必须重装 Windows 才能再打开），会降低整机防护。为一个下载工具付这个代价不值。
> 真要走这条路，`python tools/build_exe.py` 一条命令就能打包并部署到桌面。

### B. 命令行（课程）

带任何参数时 `run.py` 走命令行模式，行为与合并前完全一致。

```bash
# 单个课程（自动命名，存到 D:\视频）
python run.py --url "<课程链接>"

# 指定输出目录
python run.py --url "<课程链接>" --dir "D:\视频\交易训练营"

# 批量：urls.txt 一行一个链接
python run.py --list urls.txt --dir "D:\视频\交易训练营"
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--workers N` | 并发线程，默认 24（796 个分片约 20 秒下完） |
| `--line 1/2/3` | 指定线路，也可写 `tx` / `hw` / `byte`（默认第 1 条） |
| `--force` | 已存在也重新下载 |
| `--keep-ts` | 保留临时分片目录 |
| `--test N` | 只下前 N 个分片验证连通性 |
| `--login` | 登录态失效时打开浏览器让你登一次（默认不弹浏览器） |
| `--no-browser` | 绝不打开浏览器，只用手上的 cookie 文件。**脚本/定时任务用这个** |

### C. 通用下载（图形界面 / 网页）

| 链接类型 | 引擎 | 说明 |
|----------|------|------|
| 小鹅通课程 | `core.run_one`（本仓库核心） | 取明文 m3u8 → 并发下分片 → ffmpeg 合并 |
| 平台视频 | yt-dlp | 支持 cookie、代理、清晰度选择、播放列表 |
| 任意直链 | 自研 Range 分片 | 断点续传（`.tdmpart` + `.tdmlite.json`）、限速、多线程 |

yt-dlp 默认用 `python -m yt_dlp` 而不是 `yt-dlp.exe`：pip 生成的 exe 没有签名，
在这台机器上会被**智能应用控制**拦掉；`python.exe` 是签过名的，跑模块没事。
设置里显式填了 yt-dlp 路径的仍以它为准。

### 分发：拷给别人怎么用

1. 拷整个文件夹
2. `pip install -r requirements.txt`
3. 装 ffmpeg（放 PATH 或填进设置）
4. 双击 `run.bat`，或者 `python tools/make_launcher.py` 建桌面快捷方式

对方不需要 Python 之外的任何东西；没装 pywebview 时会退回浏览器，照样能用。
自动登录（Playwright）是可选的：`pip install playwright && playwright install chromium`。

## 实测结果

```
35、区间震荡量价行为案例分析   →  1920x1080 h264 + aac 44.1kHz，65.8 分钟，473 MB
```

## 原理（三行核心）

1. **`get_lookback_list` 接口直接返回明文 `playlist_eof.m3u8` 地址**

   ```
   GET https://{app_id}.h5.xiaoeknow.com/_alive/v3/get_lookback_list
       ?app_id={app_id}&alive_id={alive_id}&protection=0
   ```
   每条线路返回三样东西：
   - `url` —— 真实 m3u8（**明文，无需解混淆**）
   - `ext.host` / `ext.path` —— 分片 CDN 基址
   - `ext.param` —— 分片签名 `sign=...&t=...&us=...`（**会过期，每次重新取**）

2. **拼分片地址**：`{ext.host}/{ext.path}/{分片名}&{ext.param}`
   分片名形如 `156174317_1257317718_1.ts?resolution=1920x1080`，直接取自 m3u8。

   分片有**两种寻址方式**，代码要区别对待：

   | 形态 | 例子 | 怎么取 |
   |------|------|--------|
   | 整文件 | `156174317_1257317718_1.ts?resolution=1920x1080` | 直接 GET |
   | 字节区间 | `3877768290_2020660754_1.ts?end=300235&start=0&...` | 同一个文件名被几百片共用，**必须靠 HTTP `Range` 头**取 |

   > 那个 CDN 不认 URL 上的 `start`/`end` —— 带着它们请求照样返回整个文件。
   > 早期版本因此把 906 片各下了一遍完整文件，合出 15.3 小时的视频（真实 66 分钟）。
   > `core.seg_range()` 负责把区间翻成 `Range: bytes=s-e`（闭区间，两端都含）。

3. **ffmpeg concat** 合并为 MP4。

标题通过 `_alive/v3/base_info?resource_id={alive_id}&type=2` 拿，用于自动命名。

## Cookie

### 存在哪

规范位置：`%APPDATA%\xiaoe-dl\cookies.txt`
（即 `C:\Users\TimiHub\AppData\Roaming\xiaoe-dl\cookies.txt`）

查找顺序：

1. `--cookies` 显式指定
2. `%APPDATA%\xiaoe-dl\cookies.txt` ← 规范位置
3. 脚本/exe 同目录的 `cookies.txt`（便携场景）
4. `D:\视频\cookies.txt`（老位置，仅兼容）

图形界面里 **设置 → 课程登录态 → 导入 cookies 文件** 可以直接选一份导出的
cookies.txt（或 cookies.json）覆盖到规范位置，不用自己去翻 AppData；
也可以把文件内容直接粘进下面的输入框。

### 哪条 cookie 才是关键的

实测逐条单测的结论：**只有 `ko_token` 这一条是必需的**，它单独就能调通
`get_lookback_list`。其余 16 条都是可有可无的埋点/统计类 cookie。

所以 cookie 失效时，只要保证 `ko_token` 是新鲜的就行——去小鹅通网页上逛一圈它就会自动续期。

界面的「登录状态」会直接显示 `ko_token` 的有效期，例如：
`已加载 17 条，登录凭证有效至 2026-10-16 02:00（还剩 1 天后）`

### 自动登录（不用再手动导出）

除了手动导入，还能让程序**自己开一个浏览器让你登一次**，登完自动把会话存到上面
那个规范位置。扫码、手机验证码、密码——**用哪种都行**，程序不关心你怎么登的。

三种入口：

| 入口 | 操作 |
|------|------|
| 图形界面 | 粘贴课程链接后点探测卡片上的「登录一次」；或设置里的「在浏览器里登录一次」 |
| 命令行 | `python run.py --url "<课程链接>" --login` |
| 单独登录 | `python -m xiaoe_dl.auth --login --app-id <app_id> --alive-id <alive_id>` |

**队列里的课程任务绝不会自己弹浏览器**（跑到一半弹窗对批量任务就是灾难）。
需要登录时由你显式点一下，登录窗口在后台会话里打开。

> **为什么 exe 里用不了自动登录？** exe 为了控制体积把 playwright（约 150MB）
> 排除掉了。桌面那个快捷方式走的是源码，**支持**自动登录——这也是它比 exe 强的地方。

关于「怎么算登录成功」有个坑值得知道：**站方在你登录之前就会下发一个匿名的
`ko_token`**，它存在、也没过期，但拿它调接口照样返回 `11302`。所以程序不能只看
「有 ko_token 了」就认为登好了。实际判定靠两条：

1. 从链接里取到了 `alive_id` 时，**真调一次接口**确认 `code=0`（最可靠）
2. 取不到时，用「**你关掉浏览器窗口**」这个动作当人工信号

没传 `--alive-id` 时走的是第 2 条，这种情况下如果下载报 `11302`，就是遇到了匿名凭证
——重跑一次，等登录真正完成后再关窗口。

### 什么时候需要重新导

`ko_token` 是个短期凭证（约 2 天）。到期后接口会返回 `code=11302`，程序会把这条
翻译成「登录态失效，cookie 过期了，需要重新导出一份」。

到那时候你不必真的去手动导出：用上面的「登录一次」就行，或者直接正常下载——
`--login` 之外，程序也会先试着用已有的浏览器 profile 静默续一次。

> cookie 只用于调 `get_lookback_list` 拿签名。**分片下载本身不靠 cookie**，
> 靠 URL 上的 `sign/t/us` 鉴权，所以下载阶段带不带 cookie 都行。

## 出问题了怎么办

**先跑诊断，别猜：**

```bash
python tools/diagnose.py --url "<课程链接>"
```

它把整条链路拆成 7 段依次探测，**第一段失败的地方就是断点**：

```
[PASS] 0-env       python 3.13.15 / ffmpeg 9.0.2 (PATH) / playwright 有 / 浏览器 profile 无
[PASS] 1-cookie    ...cookies.txt  17 条  登录凭证有效至 2026-10-16 06:09（还剩 1 天后）
[PASS] 2-url       app_id=apptvagt9li8549  alive_id=l_6ab33ce1e4b0694c354c1a5b
[PASS] 3-api       code=0  3 条线路 / 3 个可选清晰度
[PASS] 4-playlist  线路1(线路1) cloud=tx  明文=True  分片 796 个
[PASS] 5-segment   HTTP 206  取到 1024 字节  开头=47401110  ttv-tos.xet.tech
[SKIP] 6-ffmpeg    （未加 --with-ffmpeg）
────────────────────────────────────────────────────────────
断点定位：无 —— 全链路通过
```

退出码 = 失败阶段数（0 表示全通）。其他开关：`--json`（给脚本消费）、
`--stop`（停在首个失败）、`--with-ffmpeg`（连合并也验一遍）。

**拿到断点之后**，去 [docs/api_chain.md](docs/api_chain.md) 的「改版对照表」查那一行
——它会直接告诉你该改哪个函数。

常见的其实不是改版：

| 现象 | 多半是 | 怎么办 |
|------|-------|-------|
| `code=11302` | 登录态过期 | 点「登录一次」，或 `--login` |
| 分片时好时坏 | 签名过期（正常） | 重跑即可 |
| 分片连不上 | 签名过期（正常） | 同上。持续失败才是改版 |
| **视频时长明显偏长**（比如 66 分钟变成 15 小时） | 区间分片没按 `Range` 取，每片下回了整个文件 | 1.0.1 已修。先看 `diagnose` 第 5 段报的是哪种寻址 |
| exe 双击没反应 | 智能应用控制拦截 | 改用桌面的「Timi 下载器」快捷方式 |
| 双击图标没窗口，浏览器却开了 | 没装 WebView2 / pywebview | 正常降级，照常使用；想用原生窗口就装 WebView2 |
| 窗口一闪就没了 | 启动报错 | 用 `run.bat` 看控制台，或看 `%APPDATA%\xiaoe-dl\app.log` |

## 走过的弯路（已放弃，别再试）

这些是本项目早期试过并**确认失败**的方案，记录在此避免重复踩坑：

- `yt-dlp --cookies-from-browser`：Chrome 127+ 的 App-Bound Encryption 导致 DPAPI 解密失败
- 复制 Chrome Profile 到新目录：ABE 把密钥绑死在**原始路径**上，复制后 cookie 全部失效
- Chrome 136+ 在默认 User Data 目录下禁用 `--remote-debugging-port`，且 Chrome 单例机制让
  「再起一个带调试端口的 Chrome」根本起不来
- 对课程页 URL 直接跑 yt-dlp：Unsupported URL
- F12 抓包：页面有反调试，直接 `Paused in debugger`
- 硬解 `confusion_index` 混淆播放列表：算法逆向出来了（见 `xiaoe_dl/crypto.py`，确实能用），
  **但没必要** —— `playlist_eof.m3u8` 是明文的
- 硬解 `url` 字段的 base64 字符替换：同样没必要，走 `ext.param` 就行

`_explore/` 目录里是这些探索期的脚本，留作参考，**不是入口**。真正的入口只有 `run.py`。

## 文件说明

| 文件 | 作用 |
|------|------|
| `run.py` | **唯一入口**。无参数起网页壳，`--web` 浏览器，`--tk` 旧 Tk，其余参数走命令行 |
| `run.bat` | 带控制台的启动入口（排查问题用） |
| `xiaoe_dl/core.py` | 课程核心管线（取流、拼分片、并发下载、ffmpeg 合并） |
| `xiaoe_dl/desktop.py` | 桌面壳：DPI、单实例、uvicorn、pywebview，失败退回浏览器 |
| `xiaoe_dl/downloader/` | 任务队列与三种引擎（yt-dlp / Range 分片 / 课程） |
| `xiaoe_dl/web/` | 网页界面（原生 HTML + CSS + JS，无构建步骤） |
| `xiaoe_dl/gui.py` | 旧的 Tk 界面（`--tk`） |
| `xiaoe_dl/cli.py` | 命令行参数解析 |
| `xiaoe_dl/cookies.py` | cookie 文件读取、按域过滤、登录态有效期 |
| `xiaoe_dl/auth.py` | 手动导入 / Playwright 自动登录 |
| `xiaoe_dl/paths.py` | 路径与外部依赖（ffmpeg）定位 |
| `xiaoe_dl/crypto.py` | `[xiaoe]` 混淆播放列表解码器（备用，主流程用不到） |
| `tools/diagnose.py` | **分阶段链路诊断** —— 网站改版时先跑这个找断点 |
| `tools/make_launcher.py` | 在桌面建/修启动器 |
| `tools/build_exe.py` | 一条命令打包并部署 exe（智能应用控制下会被拦，见上文） |
| `tests/` | 单测，`python -m pytest`（离线、秒级） |
| `tests/manual/` | 联网的手动测试脚本，见该目录的 README |
| `_explore/` | 探索期脚本，归档 |
| `dist/小鹅通下载器.exe` | 打包产物（未部署，本机受策略拦截） |
