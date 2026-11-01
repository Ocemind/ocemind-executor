# Ocemind 内容执行器

**下载 → 转写 → 处理 → 发布，一个人、一台机器、一个窗口。**

起点是一个**课程下载器**：给个课程链接、平台视频地址或任意直链，就能出
MP4。上面再加一层「内容执行」——13 项能力共用同一个任务队列、同一套账号
体系、同一个 Agent 接口，下载原有的入口和参数一个都没变。

```
任务      下载视频、转文字、压片、OCR、配音…… 混在同一张队列里
账号      每个平台账号一个独立浏览器，登录态分开放，过期前提醒
连接Agent MCP / HTTP API / CLI 三个入口，能力清单完全一致
```

---

## 1. 能做的事

| 能力 | 名称 | 在哪跑 | 花钱 | 说明 |
|------|------|:------:|:----:|------|
| `download.platform` | 平台视频下载 | 本地 | — | yt-dlp：B站/小红书/微博/YouTube/X；抖音走内置解析（见下） |
| `transcribe.asr` | 音视频转文本 | 本地优先 | 装了免费 | faster-whisper / openai-whisper；没装才走 API |
| `media.transcode` | 转码 / 压缩 / 合并 | 本地 | — | ffmpeg；合并失败自动回退重编码 |
| `media.subtitle` | 字幕烧制 / 翻译 / 对齐 | 本地 | ¥ | 烧制走 ffmpeg；翻译走对话模型（服务商可换） |
| `media.audio` | 音频后期 | 本地 | — | 响度归一 / 降噪 / 变速 / 抽音轨 |
| `media.thumbnail` | 封面 / 缩略图 | 本地 | — | 均匀抽帧，也可只抽指定时间点 |
| `doc.parse` | 文档解析 | 本地 | — | PDF / Word / Excel / CSV / JSON → Markdown |
| `image.ocr` | 图片 OCR | 本地优先 | 装了免费 | RapidOCR / PaddleOCR / Tesseract；没装才用视觉模型 |
| `image.edit` | 图像处理 | 本地 | — | 缩放 / 裁切 / 水印 / 转格式（Pillow） |
| `image.generate` | AI 生图 | 云端 | ¥ | 本地跑不动扩散模型，只能走 API（服务商可换） |
| `audio.tts` | TTS 配音 | 云端 | ¥ | 文字或文件 → 音频（服务商可换） |
| `audio.voiceclone` | 声音克隆 | 云端 | ¥ | 上传 10 秒以上干净人声建音色，再合成；目前只有 SiliconFlow |
| `publish.browser` | 浏览器发布 | 浏览器 | — | 真实 Chrome 操作创作后台，**必须人来批准** |

**抖音下载**：yt-dlp 会被网页接口的签名（`s_v_web_id` / `a_bogus`）挡住，
所以抖音单独走一条路——跟短链拿到作品 ID，用本机 Chrome 打开作品页，
让页面自己完成签名和拉流，从网络请求里取视频流 / 音频流地址，再分段
下载、ffmpeg 合并成 mp4。粘贴整段分享文案（带「复制打开抖音…」那种）
也能自动把链接摘出来。需要本机有 Chrome 和 `playwright`。

**本地优先**：转写和 OCR 先看本机有没有装开源引擎，装了就直接跑，不联网、
不上传素材、不花钱。「模型与接口」里可以一键安装，也能强制指定某个引擎。
只有本地确实跑不动的（生图 / 配音 / 克隆）才只能走 API。

**按能力选服务商**：转写用 Groq、翻译用 DeepSeek、OCR 用百炼，各存各的
Key，互不影响。哪家便宜或哪家能连上就切哪家，改完下一次任务即生效。
花费记在本地 `usage` 表里，可以设月度预算，超了直接拒单，不会悄悄扣钱。
本地引擎跑的任务不受预算限制。

**明确不做**：素材合成 / 自动剪辑、视频镜头生成、无限画布。
做不了的事写在代码里的注册表里，不会在界面上假装支持。

---

## 2. 怎么启动

```bash
python run.py          # 桌面窗口（pywebview；没装就自动退回浏览器）
python run.py --web    # 直接用系统浏览器打开，好调试
python run.py --tk     # 旧的 Tk 界面
python run.py --serve --port 8765   # 只起本地服务（Tauri 壳 / 远程调试用）
```

`--serve` 不打开任何窗口，给 Tauri 壳当 sidecar：绑定 127.0.0.1，
启动完打印 `OCEMIND_READY port=<实际端口>`，退出走带令牌的
`POST /api/shutdown`。

默认 `http://127.0.0.1:8765`。第一次跑先装依赖：

```bash
pip install -r requirements.txt
```

`ffmpeg` 和 `yt-dlp` 随软件打包，装到别人电脑上不用再单独装。但**本机已经
有的优先用本机的**：启动时先看设置里填的路径、再看 PATH 和常见安装目录
（scoop / chocolatey / winget / `C:\ffmpeg\bin`），都没有才用包里那份。
侧边栏会标出当前用的是哪个（`本机` / `自带`）。

打包前要先把 ffmpeg 下到 `vendor/`（只需一次，`.gitignore` 已忽略）：

```bash
python tools/fetch_vendor.py     # 共享版，约 153 MB
pip install yt-dlp               # 会被编进 sidecar
python tools/build_sidecar.py
```

ffmpeg 不编进 sidecar：onefile 每次启动都要把整包解压一遍，实测把它塞进去
启动从 1 秒变成 7 秒。它作为 Tauri 资源（`bundle.resources`）拷到程序目录的
`tools/` 下，sidecar 从自己旁边找。便携版同理，`make_portable.py` 会复制过去。

### Tauri 桌面壳

`desktop/` 是正式的分发壳：Rust + Tauri 2，窗口里加载的还是
`ocemind/web` 这一套界面，背后的 Python 内核以 sidecar 方式跑。
壳层负责挑空闲端口、拉起 `run.py --serve`（发布版是冻结的
`ocemind-sidecar.exe`）、探活后才出窗口，退出时先请求 `/api/shutdown`
再用 Job Object 收掉整棵进程树。

```bash
# 开发（用本机 python 跑 sidecar）
cd desktop && npm install
npm run dev

# Windows：先打 sidecar，再打 per-user MSI
python tools/build_sidecar.py
cd desktop && npm run build:windows        # 本机验证用，加 -- --debug
# 正式包（带签名，供应用内更新）
npm run build:release:windows

# 便携版 zip（exe + sidecar + portable.ini）
python tools/make_portable_desktop.py
```

MSI 是**按用户安装**：装到 `%LOCALAPPDATA%\Programs\Ocemind 内容执行器\`，
不需要管理员权限。卸载走安装目录里的 `Uninstall ...lnk`（或
`msiexec /x {ProductCode}`）——64 位 per-user MSI 不会往
「设置 → 应用」里写条目，别指望在那里找到它。
安装包语言固定为 `zh-CN`（`tauri.windows.conf.json` 的
`bundle.windows.wix.language`）——中文产品名在默认的 en-US / 代码页
1252 下会直接报 `LGHT0311`，别把它删掉。

debug 构建的壳默认跑源码（`python run.py`），所以本机装了 MSI 也
测不到冻结的 sidecar。要按发布路径验证，起进程时带上
`OCEMIND_SIDECAR_FROZEN=1`，壳就会去找 exe 旁边的 `ocemind-sidecar.exe`。
便携包也同理：`python tools/make_portable_desktop.py --profile debug`。

Linux 只出 **AppImage**（不出 deb / rpm / Flatpak），在
`.github/workflows/release.yml` 的 Ubuntu 24.04 job 里构建；想只验证
构建链路就手动跑 `.github/workflows/desktop.yml`（不打标签）。macOS 暂不做。

选 AppImage 而不是 deb，是因为 Tauri 的 updater 在 Linux 只能就地替换
AppImage——deb 装的是只读副本，没有应用内自更新入口。

`tauri.linux.conf.json` 里有几处不能省的覆盖：

- `productName: "ocemind"`——AppImage 文件名和包内标识都用它，中文名
  在命令行里很难用，ASCII 更稳
- `deb.desktopTemplate`——产品名变 ASCII 后，菜单里要显示的中文名
  由 `src-tauri/linux/ocemind.desktop` 提供（AppImage 内置的 .desktop
  也取这份模板）
- `deb.depends` 只写 `ffmpeg`：webkit/gtk 由 Tauri 自动追加，
  自己再写一遍会重复（留着是为兼容 deb 构建，AppImage 不用）

AppImage 是单文件，双击即跑，用户数据放在 `~/.local/share/ocemind`。
Ubuntu 24.04 默认没有 FUSE2，跑之前要 `sudo apt install libfuse2t64`，
或者用 `./ocemind_2.3.1_amd64.AppImage --appimage-extract-and-run`。

### 自动更新

Windows 安装版和 Linux AppImage 的侧边栏都有「检查更新」：查
`plugins.updater` 里的 `latest.json`，有新版本就下载签过名的安装包并重启
（Windows 走 MSI，Linux 就地替换 AppImage）。便携版、源码运行、deb 装的
版本都不显示这个入口。

启动 3 秒后会静默查一次，有新版本时按钮直接显示「发现新版本 vX.Y.Z」；
点一下就是确认并安装。启动时查不到（网络不通、仓库暂时不可达）不弹任何提示。

仓库是**公开**的，release 匿名可读，更新请求不需要任何令牌。
如果哪天把仓库改回私有，构建时把只读令牌塞进 `OCEMIND_UPDATE_TOKEN`：
`lib.rs` 的 `updater_plugin()` 会给「查更新」和「下载安装包」都加上
`Authorization: Bearer …`，本地从 `.updater-token` 读、CI 用 Secrets，
令牌只落本机文件（已在 .gitignore）。公开仓库下这个变量留空即可。

签名密钥在 `~/.tauri/ocemind-updater.key`（公钥已写进
`tauri.conf.json`），私钥**不进仓库、务必备份**；丢了就签不出能更新的包。
打 `v*` 标签由 `.github/workflows/release.yml` 构建并发布，仓库 Secrets 只需
`TAURI_SIGNING_PRIVATE_KEY`（这个密钥没设密码，密码项留空即可）。

更新地址写死在 `plugins.updater.endpoints`（`releases/latest/download/latest.json`），
目前指向 `github.com/Ocemind/ocemind-executor`——仓库改名或换地址要同步改。
标签推送后 CI **直接发布**（`releaseDraft: false`），客户端几秒后就能查到更新。
MSI 的 `upgradeCode` 固定写在 `tauri.conf.json`（`bundle.windows.wix`），
**不要改**：换了就等于换了一个产品，更新会装成第二份而不是覆盖。

`latest.json` 由 `release.yml` 的 `manifest` job 生成，不是 tauri-action 写的。
原因是它拿 `productName` 拼文件名去 Release 里找资产，而 GitHub 会把资产名里
的空格换成 `.`、删掉中文（`Ocemind 内容执行器_2.3.0_x64_zh-CN.msi` 实际存成
`Ocemind._2.3.0_x64_zh-CN.msi`），对不上就把 `windows-x86_64` 条目静默丢掉。
`tools/make_latest_json.py` 改成按接口里的真实资产名生成，`gh release upload`
上传；两个平台都会写进同一份清单。

本地自检（不需要令牌）：`python tools/make_latest_json.py v2.3.0 out.json`。

### 桌面快捷方式（源码运行）

这台机器开着**智能应用控制**，新打包的 exe 会被直接拒绝执行。
所以桌面放的是指向 `pythonw.exe` 的快捷方式（官方签名的解释器，不受策略约束）：

```bash
python tools/make_launcher.py
```

Tauri 打出来的 exe / MSI 同样要过这一关：未签名的包在本机可能被
SAC 拦下。2026-10-31 实测：debug 构建的壳可以启动；release 构建会在
编译阶段被拦——SAC 拒绝执行新生成的 `build-script-build.exe`
（`os error 4551`，CodeIntegrity 事件 3077/3033）。所以**正式 MSI 在
CI（或没开 SAC 的机器）上打**，本机只用来打 debug 包验证安装链路。
要么给构建产物做代码签名，要么这台机器继续用上面的快捷方式；
**不要为了跑安装包去关 SAC**（不可逆，只能重装系统）。

### 拷到别的电脑

```bash
python tools/make_portable.py --fetch          # 下载嵌入式 Python 3.12
python tools/make_portable.py --out D:\Ocemind
```

产出一个**整包拷走**的目录：内置解释器 + 依赖 + 程序 + `启动.bat`，
对方机器不用装 Python。详见 [docs/downloader.md](docs/downloader.md)。

---

## 3. 界面

左侧导航分六块：**任务执行 / 加密下载 / 产出协同 / 模型与接口 / 账号 / 连接 Agent**。
视觉上是一套暖白纸面 + 墨绿强调色，细线卡片、克制的阴影，主区只有一列内容。

### 任务执行
粘贴链接或拖文件进来，程序按扩展名自动选能力，也可以手动在下拉里改。
任务列表是**一套队列**，下载、转写、发布混在一起，各自的进度各显示各的。
任务是一条条独立卡片：时间、能力、状态在上，标题在中，进度在下。

### 加密下载
要登录才能取的课程回放单独一屏，不再埋在设置里。四件事：

1. 粘回放链接（`l_` 开头的 ID），先「检测」看能不能取、要哪个登录态
2. 看登录态：有没有 cookies、什么时候过期、还剩几天
3. 导入 cookies 文件，或开一个浏览器登录一次；也能直接粘 Netscape 文本
4. 设置保存目录、并发分片数、线路；然后一键加入下载队列

### 产出协同
任务完成的文件不会散落在磁盘上没人知道。每条产物登记成一条记录：
项目、序列、能力、类型、大小、本地路径、同步状态。

- **本地**：视频、音频、图片、文档原件，一个字节都不上传
- **飞书索引**：用本机已登录的 `lark-cli` 写进多维表格；粘表格链接就能
  自动拆出 `app_token` 和 `table_id`
- **云文档**：文字类（md / txt / srt / csv / json）另建一份飞书云文档，
  索引里存文档链接；视频、音频、图片只登记索引
- **手动或自动**：任务完成后自动同步可以关；也能逐条点「同步」，
  或者一次「同步未同步」

同步失败只记在产出那一行（「部分同步 / 同步失败」+ 原因），
不会让已经完成的任务变成失败。老任务可以用「补登记历史任务」找回来，
不重跑、不重新生成文件。

#### 第一次配置

1. 装 CLI 并登录：

   ```bash
   npm i -g @larksuite/cli
   lark-cli config init
   lark-cli auth login --recommend
   ```

2. 在飞书里建一张多维表格，按「产出协同」页「索引表需要这些列」建列：
   `标题 项目 序列 能力 类型 本地路径 大小 状态 云文档 源链接 完成时间`
   （`大小` 用数字列，其余用文本列）

3. 把表格链接粘进「多维表格链接或 app_token」，`table_id` 填 `tbl…`，
   点「测试连接与表结构」。

没装 `lark-cli` 时页面会直接说「未安装」，不会假装同步成功；产出仍然
登记在本地，装好之后点「同步未同步」补齐。

### 模型与接口
每项能力一行，三选一：**自动 / 本地 / 云端 API**。

- 自动：本机装了本地引擎就用本地的，没装才走 API
- 本地：下拉选具体引擎（faster-whisper、RapidOCR…），没装就明确报错，
  不会偷偷换成别的引擎，更不会偷偷花钱
- 云端：选服务商、填模型名；每家服务商的 Key 在这一页统一管理

页面下方是三段：API 服务商（含自定义 OpenAI 兼容地址）、月度预算、
本地引擎（显示体积和装没装，可一键 `pip install`，装完自动切过去）。

### 账号
左边按平台分组列账号，右边是一块实时浏览器画面（点哪打哪、可打字、可跳转）。
每个账号一个独立的 Chromium `--user-data-dir`，互不串味。

登录态用**两个信号取严**：

1. 本地 cookie 的过期时间（离线就能算，不用联网）
2. 需要时再开页面真实探一次（登录页出现「登录」按钮就算掉线）

剩不到 3 天标黄，已过期标红，**超过 7 天没登录过也提醒**——即使 cookie 没过期，
很多平台的风控也会把这种账号当异常设备。

### 连接 Agent
显示这台机器上可被别的 Agent 连到的地址（会标出 Tailscale / 内网），
生成访问令牌，并给出 MCP / HTTP / CLI 三段可直接复制的配置。

---

## 4. Agent 接入

三个入口**共用同一个内核**（`ocemind/executor/facade.py`），
所以不存在「界面能做的事 CLI 做不了」。

### MCP

```json
{
  "mcpServers": {
    "ocemind": {
      "command": "python",
      "args": ["-m", "ocemind.executor.mcp"],
      "env": {"OCEMIND_URL": "http://127.0.0.1:8765", "OCEMIND_TOKEN": "ocm_..."}
    }
  }
}
```

7 个工具：`list_capabilities`、`submit_job`、`get_job`、`wait_job`、
`list_accounts`、`publish_prepare`、`publish_approve`。

### HTTP API

`http://127.0.0.1:8765/api/v1`，必须带 `Authorization: Bearer <令牌>`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/capabilities` | 能力清单 |
| POST | `/jobs` | 提交任务 |
| GET | `/jobs` `/jobs/{id}` | 查询 |
| POST | `/jobs/{id}/cancel` | 取消 |
| GET | `/accounts` | 账号与登录状态（不含 cookie） |
| POST | `/publish` `/publish/{id}/approve` | 发布准备与批准 |
| GET | `/files?path=` | 取产物，**只限输出目录** |

`POST /jobs` 可以带 `project` / `sequence`：Agent 代跑的任务也会归到同一个
项目下，产出索引和界面里手动跑的一样。

界面上那套 `/api/exec/*` 是给本机页面用的，不受令牌限制——两套分开是刻意的：
Agent 能做的事，不等于人能做的事。

### CLI

```bash
python run.py --exec capabilities
python run.py --exec submit transcribe.asr --input D:/a.mp4 --params '{"srt":true}'
python run.py --exec download "<链接>"
python run.py --exec accounts
python run.py --exec connect
```

---

## 5. 发布：为什么必须人来点

发布是唯一会**对外产生后果**的能力，所以做了三道闸：

1. **标准校验**：标题长度、话题数、封面比例、违禁词写在
   [`ocemind/executor/standards.json`](ocemind/executor/standards.json)，
   在提交和发布时都会检查。模型改不了这份文件，也绕不过检查。
2. **快照绑定**：批准时记录文件与文案的哈希（`snapshot_hash`）。
   批准之后文件被改过，就拒绝执行（409），必须重新准备。
3. **回读确认**：发布完必须回到作品管理页，确认那条内容真的出现了，
   才算成功。没读到就算失败，不会凭「点了提交」报成功。

Agent 可以直接 `publish_prepare`，但 `approve` 需要人点头——
API 里也禁止了 Agent 直接提交 `publish.browser`（403）。

---

## 6. 平台矩阵

| 平台 | 下载 | 转文本 | 发布 |
|------|:----:|:------:|:----:|
| B站 | ✅ | ✅ | ✅ |
| 抖音 | ✅ | ✅ | ✅ |
| 小红书 | ✅ | ✅ | ✅ |
| 微博 | ✅ | ✅ | ✅ |
| 视频号 | ❌ | ✅（拖文件） | ✅（浏览器） |
| 快手 | ❌ | ✅（拖文件） | ✅（浏览器） |
| YouTube | ✅（需代理） | ✅ | — |
| X | ✅（需代理） | ✅ | — |

视频号和快手下载不到，是因为 yt-dlp 没有它们的提取器（快手的已被移除），
不是本程序的 bug。手动存好文件拖进来，转写和发布照常。

抖音的画质不由你选：解析器只拿播放器实际拉到的最高流（通常是 1080P），
下拉框里只显示「最高」。这是签名接口的限制，不是漏做。

---

## 7. 设置

左下角齿轮只剩「全局」的东西：下载目录、并发、限速、分片、代理、
外部程序路径。和具体能力相关的两项搬到了各自页面：

- 课程回放链接 / 登录态 / 线路 → 左侧「加密下载」
- 模型选择 / 服务商 Key / 预算 / 本地引擎 → 左侧「模型与接口」

| 要填的 | 什么时候需要 |
|--------|--------------|
| `siliconflowKey` 等 | 用云端转写 / OCR / 生图 / TTS / 克隆时（每家一个字段） |
| `customBaseUrl` | 用别的 OpenAI 兼容服务时 |
| `monthlyBudget` | 想给付费能力封顶时（默认 100 元/月，本地任务不计） |
| `proxy` | 下 YouTube / X，或发布测试时 |
| `chromePath` | Chrome 不在默认位置时 |

数据都在一个目录：

- `%APPDATA%\ocemind\` — `settings.json`、`tasks.json`、`executor.db`（账号/发布/用量）、`transcripts/`、`documents/`、`audio/`、`images/`
- `%LOCALAPPDATA%\ocemind\profiles\` — 每个账号的浏览器 profile（几十 MB，不做漫游同步）

想整体搬家，设环境变量 `OCEMIND_HOME` 指向新目录即可。

---

## 8. 已知限制（都是真的）

- **发布是「辅助」不是「全自动」**：平台创作后台的选择器会随改版失效。
  失效时任务会明确报「找不到上传框」，不会假装成功；改
  [`ocemind/executor/publish/adapters.py`](ocemind/executor/publish/adapters.py) 一处即可。
- **本地引擎要自己装**：转写和 OCR 默认先找本地引擎，没装就退回 API。
  需要 Key、需要联网、要花钱的是云端那一路；装好本地引擎后这两个能力离线可用。
- **账号隔离只防串号，不做指纹伪装**：故意不做，那不是给正常运营用的。
- **长音频转写受供应商单文件上限限制**：超长文件会在提交前就被反问，而不是跑到一半失败。
- **各家 ASR 的返回不一样**：硅基流动的 SenseVoiceSmall 和 Qwen3-ASR
  只回纯文本（Qwen3-ASR 的断句和标点更好），勾了 SRT 也只有 `txt`；
  同一家的 `XingChenAGI/XingChenASR-V3.2` 支持 `verbose_json` 时间轴，
  Groq / OpenAI 的 whisper 和本地 faster-whisper 也都有。程序会按模型
  自动降级，不会因为时间轴拿不到就把任务判失败。
- **视频号 / 快手不能下载**，原因见上表。

---

## 9. 开发与测试

```bash
python -m pytest -q          # 离线，秒级；当前 330 passed, 1 skipped
```

| 目录 | 作用 |
|------|------|
| `run.py` | 唯一入口（桌面 / 网页 / Tk / 命令行 / `--exec`） |
| `ocemind/downloader/` | 任务队列、三种下载引擎、FastAPI 服务 |
| `ocemind/executor/` | 执行层：能力注册表、引擎、账号、发布、API、MCP、CLI |
| `ocemind/executor/facade.py` | **四个入口共用的那层**，业务判断都在这 |
| `ocemind/web/` | 界面（原生 HTML/CSS/JS，无构建步骤） |
| `ocemind/desktop.py` | pywebview 桌面壳：单实例、DPI、窗口 |
| `desktop/` | Tauri 2 桌面壳（Rust + 单实例 / 窗口状态 / Job Object） |
| `ocemind/web/static/bridge.js` | Tauri 里接管相对路径 fetch 与文件对话框，浏览器里直接返回 |
| `tools/` | 诊断、启动器、sidecar 与便携打包 |
| `tests/` | 单测（含能力注册表与引擎的一致性检查） |

新增能力只要两步：在 `engines/registry.py` 登记，在 `engines/` 里给一个
`Engine` 子类并加进 `engine_map.py`。测试里有一条会断言「注册了但没实现」为空，
漏写会直接红。

---

## 10. 文档

- [docs/downloader.md](docs/downloader.md) — 下载模块的原理、cookie、排障
- [docs/api_chain.md](docs/api_chain.md) — 课程平台接口链路与改版对照表
- [docs/scope-v2.md](docs/scope-v2.md) — 能力范围收敛时的判定（历史方案稿）
- [docs/ocemind-integration.md](docs/ocemind-integration.md) — 整合方案的技术梳理（历史方案稿）
- [CHANGELOG.md](CHANGELOG.md)

---

## 11. 随包分发的第三方组件

| 组件 | 许可 | 说明 |
|---|---|---|
| [FFmpeg](https://ffmpeg.org/) | LGPL-2.1（shared 构建） | 以动态库形式随包分发，未改动源码；许可证全文见上游 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | 作为 Python 包编进 sidecar |

两者都从各自官方 Release 获取，没有打补丁。
