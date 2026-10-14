# 小鹅通回放下载器

**只要给课程链接，自动出 1080p MP4。** 不需要 F12、不需要抓包、不需要往浏览器装插件、不需要重新登录。

## 用法（两种）

### A. 桌面（推荐，给不碰命令行的人用）

桌面上双击 **`小鹅通下载器`** → 粘贴课程链接 → 点「开始下载」。

- 启动时会**自动读剪贴板**，如果剪贴板里正好是课程链接就直接填好，你只要点一下按钮
- 一次可粘多条链接（一行一个），批量下载
- 有进度条、实时日志、「打开文件夹」按钮
- 顶部显示**登录凭证有效期**，快过期会提醒
- 「导入 cookies…」按钮，换 cookie 不用手动翻 AppData
- 保存目录会记住上次用的，存在 `%APPDATA%\xiaoe-dl\config.json`
- 需要 `ffmpeg`（已在系统 PATH 上）；找不到会去常见安装位置兜底找

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

桌面上另有 **`小鹅通下载器（旧版备用）.exe`** —— 重构前打的包，跑得起来，万一
Python 环境坏了可以拿它应急。它和启动器只差一条：**不支持自动登录**（exe 为了
控制体积没打包 playwright）。

> 若你确实想要一个能双击的独立 exe：得先关掉智能应用控制。那是**不可逆**的
> （必须重装 Windows 才能再打开），会降低整机防护。为一个下载工具付这个代价不值。
> 真要走这条路，`python tools/build_exe.py` 一条命令就能打包并部署到桌面。

### B. 命令行

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

界面上的 **「导入 cookies…」** 按钮可以直接选一份导出的 cookies.txt（或 cookies.json）
覆盖到规范位置，不用自己去翻 AppData。

### 哪条 cookie 才是关键的

实测逐条单测的结论：**只有 `ko_token` 这一条是必需的**，它单独就能调通
`get_lookback_list`。其余 16 条都是可有可无的埋点/统计类 cookie。

所以 cookie 失效时，只要保证 `ko_token` 是新鲜的就行——去小鹅通网页上逛一圈它就会自动续期。

界面的「登录状态」会直接显示 `ko_token` 的有效期，例如：
`已加载 17 条，登录凭证有效至 2026-10-16 02:00（还剩 1 天后）`

### 什么时候需要重新导

`ko_token` 是个短期凭证。当前这份 2026-10-16 到期，到期后接口会返回 `code=11302`，
程序会把这条翻译成「登录态失效，cookie 过期了，需要重新导出一份」。

> cookie 只用于调 `get_lookback_list` 拿签名。**分片下载本身不靠 cookie**，
> 靠 URL 上的 `sign/t/us` 鉴权，所以下载阶段带不带 cookie 都行。

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
| `run.py` | **唯一入口**。无参数起 GUI，带参数走命令行；也是 PyInstaller 的打包入口 |
| `xiaoe_dl/core.py` | 核心管线（取流、拼分片、并发下载、ffmpeg 合并） |
| `xiaoe_dl/gui.py` | 图形界面 |
| `xiaoe_dl/cli.py` | 命令行参数解析 |
| `xiaoe_dl/cookies.py` | cookie 文件读取、按域过滤、登录态有效期 |
| `xiaoe_dl/paths.py` | 路径与外部依赖（ffmpeg）定位 |
| `xiaoe_dl/crypto.py` | `[xiaoe]` 混淆播放列表解码器（备用，主流程用不到） |
| `tools/diagnose.py` | **分阶段链路诊断** —— 网站改版时先跑这个找断点 |
| `tools/make_launcher.py` | 在桌面建/修启动器 |
| `tools/build_exe.py` | 一条命令打包并部署 exe（智能应用控制下会被拦，见上文） |
| `tests/` | 单测，`python -m pytest` |
| `_explore/` | 探索期脚本，归档 |
| `dist/小鹅通下载器.exe` | 打包产物（未部署，本机受策略拦截） |
