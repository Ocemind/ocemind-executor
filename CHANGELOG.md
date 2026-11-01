# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
版本号见 `ocemind/__init__.py`。

## [2.3.3] - 2026-11-01

### 新增

- **ffmpeg / yt-dlp 随软件分发**：装到别人电脑上不用再要求对方自己装。
  yt-dlp 作为 Python 包编进 sidecar，由 sidecar 自己的解释器跑
  （`--run-ytdlp`）——`pip` 生成的 `yt-dlp.exe` 没有签名，会被智能应用
  控制拦掉；ffmpeg 用共享版，作为 Tauri 资源装到程序目录的 `tools/` 下，
  不塞进 sidecar（塞进去 onefile 每次启动都要解包 150 MB，README 有实测）

### 变更

- **本机已有的优先用本机的**：设置里填的路径 → PATH / scoop / chocolatey /
  winget / `C:\ffmpeg\bin` → 包里自带的。侧边栏会标出当前用的是哪一个
- 新增 `tools/fetch_vendor.py`：打包前把 ffmpeg 下到 `vendor/`（只做一次，
  产物已忽略），spec 找不到就跳过、不中断打包

### 修复

- Linux / AppImage 上 ffmpeg 一律显示「不可用」：可执行文件名原来写死了 `.exe`

## [2.3.2] - 2026-11-01

### 变更

- **包名与数据目录统一到 `ocemind`**：Python 包为 `ocemind/`，数据目录为
  `%APPDATA%\ocemind`（浏览器 profile 在 `%LOCALAPPDATA%\ocemind`）
- **环境变量**：`OCEMIND_HOME` / `OCEMIND_LOCAL_HOME` / `OCEMIND_DUMP_RAW`
- **下载中间文件后缀**：`.ocemind.part` + `.ocemind.json`
- 文档、注释、构建脚本里的旧品牌字样一并清掉。接口域名 `xiaoeknow.com`
  是站方的，保持原样

## [2.3.1] - 2026-11-01

### 新增

- **启动时自动检查更新**：界面渲染完 3 秒后静默查一次，有新版本就把侧边栏
  的「检查更新」换成「发现新版本 vX.Y.Z」，点一下确认即可下载安装。启动时
  网络不通或没有新版本都不打扰用户

### 修复

- **Windows 更新清单缺条目**：`latest.json` 改由 `tools/make_latest_json.py`
  按 Release 里的真实资产名生成。tauri-action 用 `productName` 拼文件名去
  匹配资产，GitHub 会把空格换成 `.`、删掉中文，对不上就静默丢掉
  `windows-x86_64`，导致 Windows 收不到自动更新
- **MSI 打包报 ICE38**：per-user 安装下 sidecar 组件不能拿文件当 KeyPath，
  改用 HKCU 注册表项；同模板里的反斜杠改成双写，避免被 Handlebars 吞掉

## [2.3.0] - 2026-10-31

桌面端从 pywebview 换成 Tauri 2 壳，界面、能力、接口都不变；新增
Windows 安装包 / 便携版和 Linux AppImage 的构建链路，两端都能应用内更新。

### 新增

- **Tauri 2 桌面壳**（`desktop/`）：原生窗口 + 单实例 + 窗口状态记忆 +
  文件对话框 + 系统浏览器打开外链。界面仍由同一套 `ocemind/web` 提供，
  不另做一套前端
- **Python sidecar 模式**：`python run.py --serve --port N` 只起本地
  服务，不开窗口；`/api/health` 给壳层探活，`/api/shutdown` 带一次性
  令牌，退出前会关账号浏览器、停任务队列
- **随机端口**：桌面版由壳层分配空闲端口，「连接 Agent」显示的本机 /
  局域网地址会跟着实际端口走，不再写死 8765
- **便携版数据目录**：exe 旁边放 `portable.ini` 时，设置、任务、浏览器
  profile 都写进同目录的 `data/`，可用 `OCEMIND_LOCAL_HOME` 单独覆盖
  profile 位置
- **打包脚本**：`tools/build_sidecar.py`（PyInstaller sidecar）、
  `tools/make_portable_desktop.py`（便携 zip）；Windows per-user MSI
  使用自定义 WiX 模板，装到 `%LOCALAPPDATA%\Programs`，不需要管理员
- **CI**：`.github/workflows/desktop.yml`，Windows 构建 MSI + 便携 zip，
  Ubuntu 22.04 只构建 AppImage（不出 deb / rpm / Flatpak）；打 `v*`
  标签时走 `release.yml`，用 minisign 私钥签名更新包并发布
- **应用内更新（Windows + Linux）**：侧边栏「检查更新」→ 校验签名后下载
  安装并重启；Windows 走 MSI，Linux 就地替换 AppImage。公钥在
  `tauri.conf.json`，私钥放在 `~/.tauri/`，不随仓库分发。仓库是私有的，
  更新请求带只读令牌（构建时由 `OCEMIND_UPDATE_TOKEN` 注入，不入库）。
  便携版 / 源码运行 / deb 装的版本不提供该入口
- **`OCEMIND_SIDECAR_FROZEN=1`**：debug 构建也按发布路径去找 exe 旁边的
  `ocemind-sidecar.exe`。本机被 SAC 挡住 release 构建时，靠它验证安装包
  里的冻结 sidecar；`make_portable_desktop.py --profile debug` 同理

### 验证

- MSI 全链路本机实测（debug 产物）：构建 → 静默安装到
  `%LOCALAPPDATA%\Programs\Ocemind 内容执行器` → 启动（冻结 sidecar，
  `/api/health` 正常）→ 关窗后壳与 sidecar 进程全部退出 → 卸载清干净
  文件与开始菜单，用户数据保留
- 便携 zip 实测：解压即用，数据写进同目录 `data/`，进程退出无残留
- Linux `.deb` 在 Ubuntu 24.04 容器里实打实装过：`apt install ./ocemind.deb`
  能解析依赖，`/usr/bin/ocemind-sidecar --serve` 探活正常，Xvfb 下界面
  能起、壳能拉起 sidecar、`/api/health` 正常
- 顺手修了两个会让 deb 直接不可用的问题：`Package` 字段不能带中文
  （改为 `productName: ocemind` + 自定义 `.desktop` 模板保留中文菜单名）、
  自己写的 `depends` 会和 Tauri 自动追加的依赖重复（只留 `ffmpeg`）

### 变更

- 静态资源移入 `ocemind/web/static/`：`index.html` 里的 `static/...`
  在 FastAPI（挂载 `/static`）和 Tauri（按 frontendDist 相对路径）下
  指向同一份文件，不会出现「网页版有样式、桌面版是裸 HTML」
- 新增 `ocemind/web/static/bridge.js`：Tauri 里接管相对路径 fetch、把
  pywebview 的文件选择接口换成 Tauri 命令；浏览器里直接返回，不影响
  原有网页模式
- MSI 语言固定 `zh-CN`（代码页 936）：中文产品名在默认 en-US/1252 下
  会报 `LGHT0311`；`upgradeCode` 固定下来，保证后续版本是升级而不是
  并装第二份
- 侧边栏「检查更新」只在 **Windows 安装版**出现：便携版装不了 MSI，
  Linux `.deb` 也不在 updater 覆盖范围，两种情况下入口直接隐藏

### 已知限制

- 这台机器开着智能应用控制（SAC）。debug 构建可以运行，但 release
  构建会在编译阶段被拦（新生成的 `build-script-build.exe` 被
  CodeIntegrity 拒绝，`os error 4551`）。正式 MSI 请在 CI 或没开 SAC
  的机器上打；不要为此关闭 SAC
- Linux 只出 `.deb`，没有应用内自动更新（Tauri updater 只覆盖
  AppImage），升级方式是装新 deb

### 测试

- 新增 2 项路径测试（便携 profile 覆盖、默认 LOCALAPPDATA）
- 新增 2 项界面资源测试，防止 `static/` 路径再次和打包目录脱节
- 新增 2 项版本一致性测试（Python / Tauri / npm / Cargo）
- 当前 **330 passed, 1 skipped**

## [2.2.1] - 2026-10-31

云端转写换成 `Qwen/Qwen3-ASR-1.7B` 时会报
「网络错误: [WinError 10054]」，其实不是网络问题。

### 修复

- **`response_format=verbose_json` 不被支持时自动降级**：硅基流动的
  Qwen3-ASR 只认 `json` / `text`，收到 `verbose_json` 会回一个空响应的
  400；文件较大、上传还没结束时，服务端干脆关连接，客户端只能看到
  WinError 10054。现在遇到空响应 400 或连接被重置，就用 `json` 重发一次
- **降级结果按「服务商 + 模型」记住**：同一个进程里只试错一次，后续任务
  直接发 `json`，不再白传一遍音频
- **`srt` 参数真正生效**：以前不管传什么都没用，永远请求时间轴；现在
  `srt=false` 只取纯文本
- **真正的接口错误不再被吞掉**：模型名不存在这类 400 会原样报出
  `Model does not exist`，不再和网络故障混在一起
- **转写完成后的提示**：勾了 SRT 但模型只回纯文本时，任务阶段会写明
  「模型只返回纯文本，未生成 SRT」，结果 `meta.note` 里给出替代方案

### 已知限制

- 硅基流动的 SenseVoiceSmall、Qwen3-ASR **不返回时间轴**，只能出 `txt`。
  要 `srt` 可以换同一家的 `XingChenAGI/XingChenASR-V3.2`（支持
  `verbose_json`，实测 60 秒音频切成 3 段），或用 Groq / OpenAI 的
  whisper、本地 faster-whisper

### 测试

- 新增 `tests/test_transcribe_cloud.py`（15 项）：空响应 400 降级、
  连接重置降级、格式记忆、错误上报、5xx 重试、`srt` 解析
- 当前 **324 passed, 1 skipped**

## [2.2.0] - 2026-10-31

抖音分享链接真正能下了。yt-dlp 在抖音上会被 `s_v_web_id` / `a_bogus`
签名挡住，这次换成让页面自己签名的做法。

### 新增

- **抖音专用解析器**（`ocemind/douyin.py`）：跟短链拿作品 ID → 本机
  Chrome 打开作品页 → 从播放器的网络请求里取视频流 / 音频流地址 →
  Range 分段下载 → ffmpeg 无损合并。输出 1080x1920 H.264 + AAC 的 mp4
- **粘贴整段分享文案**：`7.61 复制打开抖音… https://v.douyin.com/xxx/ …`
  这种不干净的输入，前端和后端都会先摘出真正的链接；裸域名也能补全
- **两条入口都接上了**：网页任务队列（`/api/tasks`）和执行器能力
  （`download.platform`）走同一套解析与下载
- **探测**：抖音链接只跟短链、不启动浏览器，标题和时长在下载开始时补上；
  画质只提供「最高」，因为签名接口只给播放器实际拉到的流
- **取消**：下载中取消会立刻停掉两条流，不会留下半个成片

### 测试

- 新增 `tests/test_douyin.py`（18 项）：分享文案取链接、域名识别、
  流分类与码率选择、标题清洗、重名避让
- 当前 **309 passed, 1 skipped**

## [2.1.1] - 2026-10-31

一次全面测试里暴露的参数「写了没读」问题，集中修掉。

### 修复

- **封面抽帧 `at` 生效**：以前只认 `count`，`at` 传了也被忽略；现在指定
  秒数只抽那一帧（超过片长会夹到结尾前）
- **音频后期 `format` 生效**：`mp3 / m4a / wav / flac / ogg / aac` 按格式选
  编码器；`name` 不带扩展名时自动补上，不再让 ffmpeg 报退出码 -22
- **图像处理 `name` 不带扩展名**：自动沿用源文件后缀，不再出现
  `unknown file extension:`
- **PaddleOCR**：检测同时要求 `paddlepaddle`（只有 `paddleocr` 壳不算装了），
  并兼容 3.x 的 `predict()` / `rec_texts` 结果结构；2.x 仍可用
- **配音 / 声音克隆加时长校验**：接口返回不足 0.3 秒的音频判为失败，
  不再把 0.2 秒的空壳文件报成成功
- **无扩展名下载的产出归类**：靠文件头识别视频 / 音频 / 图片，抖音直链
  这类不带后缀的文件不再一律记成「其他」

### 测试

- 新增 `tests/test_engine_params.py`（21 项），覆盖上述参数与适配逻辑
- 当前 **291 passed, 1 skipped**

## [2.1.0] - 2026-10-31

把「产物」从磁盘上的一个路径，变成能按项目找、能进飞书协同的记录。

### 新增

- **「产出协同」页面**：每个任务产物单独登记一条（项目 / 序列 / 能力 / 类型 /
  大小 / 本地路径 / 同步状态）。一个任务出多个文件就是多条，不再只有一个
  `path`
- **飞书协同（走本机 lark-cli）**：
  - 多维表格索引：每条产物写一行，粘表格链接自动拆出 `app_token` / `table_id`
  - 文字转云文档：md / txt / srt / csv / json 另建一份飞书文档，索引里存链接；
    视频、音频、图片只登记，不上传
  - 自动或手动：任务完成后的同步可关，也能逐条同步、批量同步
  - 表结构检查：`base describe` 读列名，缺哪列直接说出来
  - 同步失败不拖垮任务：状态记成「部分同步 / 同步失败」，原因留在产出行
- **项目 / 序列**：提交任务时可选填，下载与能力任务共用；界面记住上次输入；
  Agent 的 `POST /jobs` 同样认这两个字段
- **补登记历史任务**：功能上线前完成的产物能找回来，不重跑任务
- 新增 16 项测试（产出登记 7、飞书桥 9），当前 **270 passed, 1 skipped**

### 变更

- 任务完成后先登记本地 SQLite（毫秒级），需要同步飞书时再另开后台线程，
  不占用下载队列
- 课程链接的错误文案统一为「加密下载」，不再出现旧产品名

### 已知限制

- 没装 `lark-cli` 或没登录时无法同步。页面会明确显示「未安装 / 未登录」，
  产出仍会登记在本地，装好后点「同步未同步」补齐
- 索引表需要自己先建好列。程序不会替你建表——表结构属于用户资产，
  自动创建比报错更危险

## [2.0.0] - 2026-10-31

在 课程下载器上加执行层，产品改名 **「Ocemind 内容执行器」**。
下载功能全部保留（命令、参数、cookie 机制、三种引擎、Tk 界面都没动）。

### 新增

- **13 项内容能力**：平台下载、音视频转文本、转码压缩合并、字幕烧制/翻译/对齐、
  音频后期、封面抽帧、文档解析、图片 OCR、图像处理、AI 生图、TTS 配音、
  声音克隆、浏览器发布。能力清单只有 `engines/registry.py` 一份，
  API、界面、Agent 都从它读
- **同一张任务队列**：能力任务用 `kind="capability"` 并入原有队列，
  并发、暂停、取消、重启恢复、进度显示全部复用
- **账号浏览器**：每个账号独立 Chromium profile，界面里直接看画面并操作；
  登录态按 cookie 过期时间 + 页面探活双信号判定，3 天内标黄、7 天没登录提醒
- **浏览器发布**：标准校验 → 快照哈希绑定 → 发布后回读确认；Agent 不能直接批准
- **Agent 三入口**：MCP（`python -m ocemind.executor.mcp`，7 个工具）、
  HTTP `/api/v1`（Bearer 令牌）、CLI（`python run.py --exec ...`），
  共用 `executor/facade.py` 一层
- **用量与预算**：付费能力按次记账，按月封顶，超预算直接拒单；
  本地引擎执行的任务不计费、不查预算
- **幂等**：同一能力 + 同一输入 + 同一参数只建一个任务
- **本地优先后端**（`executor/backends.py`）：转写接 faster-whisper /
  openai-whisper，OCR 接 RapidOCR / PaddleOCR / Tesseract；装了本地引擎
  就离线跑，没装才走 API
- **按能力选服务商**：每项能力单独保存 `{本地/云端, 服务商, 模型}`，
  转写、翻译、OCR、生图、TTS、克隆可以各用各的平台，随时切换
- **一键装本地引擎**：`executor/local_install.py` 用白名单包名跑
  `pip install`，界面里显示日志和结果
- **「加密下载」独立页面**：回放链接检测、登录态、cookies 导入、并发/线路
  设置不再挤在设置弹窗里
- **「模型与接口」页面**：按能力选题、服务商 Key、月度预算、本地引擎集中管理
- **新增 API**：`GET /api/exec/models`、`POST /api/exec/models/{capability}`、
  `GET /api/exec/local`、`POST /api/exec/local/install`
- **便携打包**：`tools/make_portable.py` 生成内置 Python 的整包目录
  （绕开智能应用控制，不用 PyInstaller）

### 变更

- 产品名、桌面窗口标题、桌面快捷方式改为「Ocemind 内容执行器」；
  单实例互斥名同步更换，旧的三个下载器快捷方式自动清理
- 界面重做：五块导航（任务 / 加密下载 / 模型与接口 / 账号 / 连接 Agent），
  视觉按 aihot.news 的 token 体系落地 —— 暖白纸面 `#faf9f6`、墨绿强调色
  `#176b75`、四级墨色、`#dfe4e1` 细线、12px 卡片圆角、胶囊分段控件；
  任务改成独立卡片的信息流，元信息在上、标题在中、进度在下
- 设置弹窗瘦身：课程登录态和模型配置移出，只留全局下载 / 代理 / 外部程序
- 登录态徽标只说结论（「登录态已过期」），详细原因留在下面的行内提示里
- 任务状态文案统一（`处理中` 等），能力任务的进度允许「不确定」，
  不再为了好看编一个百分比

### 修复

- 文档目录 `doc.parse` 在源文件已是 Markdown 且输出目录相同的情况下会覆盖输入

### 兼容

- `tasks.json` 里的旧任务原样可读；重启后「处理中」按原有规则转为「已暂停」
- 旧设置里的 `asrProvider` / `asrModel` / `asrBaseUrl` 仍然认，作为没有
  显式选择时的回退；新写入走 `settings["models"]`
- 原有 245 项测试全部通过，新增 19 项执行层测试；本次再加 9 项后端选择测试
  （当前 254 passed, 1 skipped）

## [1.1.0] - 2026-10-20

并入自建的通用下载器（更早的独立项目），一个应用、一个桌面入口。

### 新增

- **网页界面（默认）**：FastAPI + pywebview 原生窗口，课程、平台视频、任意直链
  在同一张任务列表里。`--tk` 保留旧 Tk 界面，`--web` 用系统浏览器打开。
  没装 WebView2 / pywebview 时自动退回浏览器
- **课程成为第三种任务类型**：标题回填、分片进度（`231/796 片`）、暂停续传、
  合并阶段提示；同一门课 + 同一输出目录**不允许重复排队**
- **两种通用引擎**：yt-dlp 管平台视频，自研 Range 分片管直链（断点续传、限速、
  多线程）。任务队列支持并发数、暂停/继续/取消/重试、重启后恢复
- **课程登录态管理**：设置里的「导入 cookies 文件」/「在浏览器里登录一次」，
  对应 `GET /api/course/auth`、`POST /api/course/cookies/import`、
  `POST /api/course/login`
- 数据目录统一到 `%APPDATA%\ocemind`：`settings.json` / `tasks.json` /
  `config.json`（Tk 仍读它）/ `cookies.txt` / `app.log`
- `run.bat`（带控制台）、`tests/manual/`（联网测试脚本）

### 变更

- 桌面入口改名 **「课程下载器」**，图标换成 `app.ico`
- `run.py` 默认启动网页界面；带参数时仍是原来的课程命令行，行为不变
- yt-dlp 优先用 `python -m yt_dlp`：pip 生成的 `yt-dlp.exe` 没有签名，会被
  本机智能应用控制拦掉
- 合并改为先写 `.tmp.mp4` 再原子改名，崩溃不会留下半个「已完成」的文件

### 修复

- **课程进度会被字节数采样覆盖回 0**：任务调度原来对所有任务按字节采样，
  课程的进度来自分片回调，两者打架。现在对 course 直接跳过采样
- `download_all` 在暂停后仍会继续排队的请求：现在每个分片开始前检查停止信号

### 已知限制

- 合并阶段不可打断：ffmpeg 一旦起跑只能等它结束，暂停在合并完成后才生效
- 旧的 CLI 临时分片目录（`%TEMP%\ocemind_<alive 后8位>`）不会被新任务复用，
  一次性重下

## [1.0.1] - 2026-10-15

### 修复

- **区间寻址的课程会合出超长视频（15.3 小时 vs 真实 66 分钟）**。
  有的课程把整场直播切成几百片、共用一个文件名，只靠 URL 上的 `start`/`end`
  区分。这个 CDN **完全不看那两个查询参数**，每次都返回整个文件（4.4 MB /
  60 秒）；`download_all` 又没带 `Range` 头，于是 906 片各下一遍完整文件，
  906 × 60.08s ≈ 15.3 小时。
  现在 `core.seg_range()` 解析区间、`http_get(byte_range=...)` 翻成
  `Range: bytes=s-e`（**闭区间，两端都含**）。
  `start`/`end` 仍留在 URL 里 —— URL 带签名，查询串在签名覆盖范围内

- **脏缓存会让这个 bug 永远修不好**。`download_all` 原来的判据是「.ts 文件
  大于 1KB 就算下好了」，而坏版本留下的正是「每片都是整个文件」的 4.4MB 文件
  —— 全都被当成有效缓存跳过，重跑多少次都只会拼出同一个 15 小时视频。
  现在区间分片额外要求缓存**不超过区间长度**（区间响应不可能更长）

### 新增

- `http_get(byte_range=...)` 的护栏：要了区间却收到 200 + 超长响应时**直接抛错**。
  这种数据首字节是正常的 `0x47`、状态码也正常，静默接受就会再出一次超长视频
- `diagnose` 的 `5-segment` 阶段现在会：
  - 报出该课程的**寻址方式**（`区间寻址 start=0 end=300235` / `整文件寻址`）
  - 要了 1024 字节却收到整份文件时判 **FAIL** —— 这正是 1.0.0 漏掉这个 bug
    的原因：拿到的是货真价实的 TS 流，光看这一个分片挑不出任何毛病
- `tests/test_download.py` —— 16 条，钉住区间解析、Range 头、两道护栏，
  以及「脏缓存必须被丢弃重下」

### 验证

```
问题课程 l_624645f9e4b09dda1254fcb0  →  906 分片 / 3973.7 秒 (66.2 分钟) / 422 MB / 1080p
回归课程 l_6ab33ce1e4b0694c354c1a5b  →  796 分片 / 整文件寻址，分片数不变
```

## [1.0.0] - 2026-10-14

第一次工程化整理。此前是「一个能跑的脚本文件夹」，这版之后是「能长期维护的项目」。

### 新增

- **`tools/diagnose.py`** —— 分阶段链路诊断。把整条链路拆成 7 段依次探测，
  直接报出**首个失败阶段**。网站改版时先跑它找断点，不用再从零逆向
- **`ocemind/auth.py`** —— 登录态双模式。手动导入 cookies.txt 之外，
  还能开真浏览器让你登一次（扫码 / 手机验证码 / 密码都行，程序不假定登录方式）
- **`tools/make_launcher.py`** —— 在桌面建/修/删源码启动器
- **`tools/build_exe.py`** —— 一条命令打包并部署 exe
- **`tests/`** —— 137 条单测，`python -m pytest` 跑，全部离线
- **`docs/api_chain.md`** —— 三跳接口链路 + **改版对照表**（断点 → 该改哪个函数）
- **`run.py`** —— 唯一入口：无参数起 GUI，带参数走命令行
- CLI 新增 `--login` / `--no-browser`
- GUI 新增「登录一次」按钮与登录状态区

### 变更

- **包化为 `ocemind/`**。`download.py` / `gui.py` / `ocemind_crypto.py` /
  `ocemind_cookies.py` 分别改名为 `core.py` / `gui.py` / `crypto.py` / `cookies.py`。
  **不保留旧名兼容**（只有一个使用者，README 同步改了）
- cookie 一律按域过滤后再拼 `Cookie` 头。多个店铺子域会产生多条同名
  `ko_token`，全拼进一个头服务端行为未定义
- 桌面交付从 exe 改为**源码启动器**（原因见下）

### 修复

- **`find_ko_token` 的跨账号凭证泄漏**：域过滤后没找到时，原实现会回退到
  「所有候选」，等于把 A 店铺的登录态发给 B 店铺。已删掉这个回退，并加了回归测试
- **`grab_playlist` 里的静默 import**：`sys.path.insert` + `try/except` 包着
  `import ocemind_crypto`，模块名变了也不报错，只会静默少一个功能。改成正常导入
- **`cookie_candidates()` 的路径漂移**：包化后 `dirname(__file__)` 从项目根变成
  了包目录，「cookies.txt 放在 exe 旁边」的语义会悄悄失效
- **`crypto.decode()` 的默认密钥**：原来不传 key 时会静默用 JS 里的兜底值
  `appbgzjnopv1917`，解出乱码但不报错。现在 key 是必填参数
- 源码运行时 `python run.py --url ...` 的输出以前全进了
  `%TEMP%\ocemind-cli.log`，控制台什么都看不到。改为同时写控制台和日志

### 已知问题

- **本机上重建的 exe 无法运行。** 这台机器开着智能应用控制（Smart App Control，
  强制模式），它按微软 ISG 信誉放行可执行文件——每重新打包一次就是一份新哈希，
  要重新攒一次信誉。实测重建的 exe 被拒（退出码 126，CodeIntegrity 事件 3077）。
  桌面因此改用指向 `pythonw.exe` 的快捷方式，不受该策略约束，且永远是最新代码。
  旧的 exe 保留为「旧版备用」作兜底
- `crypto.decode()` 的 round-trip 测试只能证明**解码器相对自身自洽**，
  不能证明对真实混淆样本解对——手上已无真样本（探索期的 `_probe.bin` 已清理）。
  真遇到 `[xiaoe]` 响应时用 `OCEMIND_DUMP_RAW=<路径>` 落一份样本

### 未做（明确排除）

- 批量课程管理（清单 / 断点续传 / 去重）
- 版本发布流程工程、CI
- 把 Playwright 打进 exe
- `core.py` 内部逻辑重构（能跑就不动，这次只做移动和改导入）
