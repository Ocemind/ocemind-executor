# 执行器范围收敛 v2 —— 下载 / 转文本 / 账号浏览器 / 发布

> 状态：**历史方案稿**。本文把能力砍到 4 项，是 2026-10-31 讨论过程中的一版；
> 后来用户明确要求保留 13 项（本文"其余 12 个能力全部不做"的结论**不生效**），
> 最终实现见 [README](../README.md)。
> 本文仍然有效的部分：平台矩阵、内嵌浏览器选型、发布分档。

---

## 0. 收敛后只剩四件事

| # | 能力 | 说明 |
|---|------|------|
| 1 | 下载 | 国内平台为主 + YouTube / X |
| 2 | 视频转文本 | 音视频 → 文字 + 字幕 |
| 3 | 账号浏览器 | 内嵌浏览器、多账号隔离、登录态保活与过期预警 |
| 4 | 发布 | 国内平台，浏览器自动化 |

其余 12 个能力全部不做。

---

## 1. 平台矩阵（本机实测，yt-dlp 2026.08.19 / 1752 个 extractor）

| 平台 | 下载 | 转文本 | 发布 |
|------|:----:|:------:|:----:|
| B站 | ✅ | ✅ | ✅ |
| 抖音 | ✅ | ✅ | ✅ |
| 小红书 | ✅ | ✅ | ✅ |
| 微博 | ✅ | ✅ | ✅ |
| 视频号 | ❌ | ✅（拖文件） | ✅（浏览器） |
| 快手 | ❌ **extractor 已从 yt-dlp 移除** | ✅（拖文件） | ✅（浏览器） |
| YouTube | ✅（需代理） | ✅ | — |
| X | ✅（需代理） | ✅ | — |

**两个缺口都有替代入口**：下载不到的平台，用户手动存文件拖进来，转文本和发布照常。
UI 上要明写原因，别让人以为是 bug。

---

## 2. 内嵌浏览器：选型结论

| 方案 | 真内嵌 | 多账号隔离 | 本机可行 |
|------|:------:|:----------:|:--------:|
| Electron `session.fromPartition('persist:x')` | ✅ | ✅ | ❌ 新 exe 被智能应用控制拦 |
| pywebview / WebView2 | ✅ | ❌ 一进程一 profile | 单账号可用 |
| **Edge + `--user-data-dir` + Playwright 驱动** | 窗口级 | ✅ 一号一目录 | ✅ Edge 微软签名，不触发 SAC |

**选第三种。**

- 账号目录：`%APPDATA%\xiaoe-dl\profiles\<platform>-<label>\`
- cookie / 缓存 / localStorage 由 Chromium 自己物理隔离——这就是"独立隔离环境"，
  **不做指纹伪造**（那是矩阵号防关联用的，个人运维不需要，也不该做）
- 登录一次，会话由浏览器自己维持，行为和日常上网完全一致
- 可选美化：窗口起来后用 Win32 `SetParent` 挂进主窗口右侧客户区；
  失败自动退回独立窗口，不影响功能

---

## 3. 账号模型与过期预警

```python
@dataclass
class Account:
    id: str                  # "douyin-main"
    platform: str            # douyin / bilibili / xiaohongshu / weibo / shipinhao / kuaishou
    label: str               # 用户起的名
    profile_dir: str         # profiles/<id>
    last_ok_at: float | None # 最后一次登录验证通过
    last_check_at: float | None
    cookie_expiry: float | None
    status: str              # ok / warn / expired / never
```

**预警用双信号，取严的那个：**

1. **离线**：读 profile 里 `Network/Cookies`（SQLite）。cookie 值被 DPAPI 加密解不开，
   但 `expires_utc` 是明文——**不解密就能算出还剩几天**。开销为零，可以每分钟刷。
2. **联网**：每 12 小时用 Playwright 打开一次平台登录态接口
   （如 B站 `x/web-interface/nav`），返回未登录直接判死。

左侧栏四档：`正常` / `即将过期`（剩余 <25%）/ `已失效` / `从未登录`。
点一下账号 → 打开它自己的窗口重新登。

---

## 4. 发布适配器

每个平台一个适配器，固定三个方法：

```python
class Publisher(Protocol):
    def login_ok(self, page) -> bool: ...
    def upload(self, page, files, meta) -> str:   # 返回平台侧草稿/作品 id
    def verify(self, page, ref) -> dict:          # 回后台作品列表确认
```

**`verify` 是硬要求，不是可选。** 点了"发布"不等于发出去了——平台可能转码失败、
可能进审核、可能弹二次确认。没有回读确认，"发布成功"就是幻觉。

---

## 5. Agent / Skill 集成

### 可以这样理解，但要分清是哪一层

**Agent 决定"发什么、发给谁、说什么"；适配器决定"怎么点"。**
这个分工是整套东西可靠与否的分水岭。

| | 模式 A（采用） | 模式 B（不采用） |
|---|---|---|
| 谁操作浏览器 | 已封装的适配器 | LLM 现场看 DOM 决定点哪 |
| 确定性 | 高，同样输入同样路径 | 低，每次都不一样 |
| 成本 | 接近零 | 每步都要一次模型调用 |
| 出错可复现 | 可以（日志 + DOM 快照） | 基本不能 |
| 风控暴露 | 与真人操作接近 | 节奏异常，容易触发 |

所谓"自然语言直接发布"，成立的方式是：**自然语言 → 翻译成参数 → 适配器执行**，
而不是"自然语言 → 模型去点屏幕"。

### 两层接口

```
L1  原子工具（确定性，给 Agent 调）
    publish.create(platform, account, files, meta) -> job_id
    publish.run(job_id)                            -> 逐步状态
    publish.verify(job_id)                         -> 平台侧回读结果
    accounts.list() / accounts.probe(id)

L2  编排（Agent 自由发挥，但每步都落在 L1 上）
```

Skill 就是 L1 的说明书 + 几个预设配方，不包任何模型调用。

### "我制定的标准"必须是代码，不是提示词

这条最关键。标准如果只写在 Skill 的提示词里，模型一次疏忽就漏了；写成配置，
适配器强制执行，模型绕不过去：

```yaml
# publish/standards.yaml
douyin:
  title_max: 55
  hashtags: [交易, 复盘]
  cover_ratio: "9:16"
  banned_words: [...]        # 命中直接拒绝，不发布
  require_snapshot_hash: true
```

自然语言说"按我的标准发"，实际是 `meta` 提交后先过这张表，不合格返回 422 并说明
违反了哪条。**这才叫"按我制定的标准"。**

### 端到端长什么样

> 用户："把这条视频发抖音和 B站，晚上 8 点发"

```
Agent → 解析出 {files, platforms:[douyin, bilibili], schedule: 20:00}
      → 调 L1 校验：标题长度、话题标签、封面比例、违禁词
      → 生成 2 个 job，绑定产物 sha256
      → 到点由执行器跑适配器（不依赖 Agent 在线）
      → verify 回读 → 汇报"抖音已通过，B站转码中"
      → 任一失败：截图 + DOM 快照落盘，等人修一次
```

### 四条必须写进 Skill 的限制

1. **Agent 必须跑在本机或 Tailscale 网段内**。云上的 Agent 打不到 `127.0.0.1`。
2. **登录过期时 Agent 不能自己解决**。需要扫码，只能挂起等人，不能硬试。
3. **平台改版会让选择器失效**。失败时保存 DOM 快照，人工修一次；不让模型每次现场猜。
4. **批量发布有风控**。频率、内容重复度都算。要限速，且不承诺"多发不封"。

### 审批门（防止 Agent 发错东西）

```http
POST /api/publish/{job_id}/approve
{ "snapshot_hash": "<产物 sha256>" }
```

服务端重算哈希，不一致 409。这样"你批准的那份东西"和"实际发出去的东西"
在物理上是同一串字节。

---

## 6. 目录结构与工作量

```
xiaoe_dl/
├── accounts/
│   ├── store.py        # 账号与状态持久化            ~120 行
│   ├── browser.py      # 拉起/关闭 Edge profile      ~150 行
│   ├── probe.py        # 登录验证 + cookie 过期计算   ~130 行
│   └── session.py      # 保活调度                   ~80 行
├── engines/transcribe.py   # ASR 三档              ~250 行
├── publish/
│   ├── base.py         # Publisher 协议 + 标准校验   ~150 行
│   ├── douyin.py / bilibili.py / xiaohongshu.py
│   ├── weibo.py / shipinhao.py / kuaishou.py       # 合计 ~600 行
│   └── standards.yaml
└── downloader/manager.py   # 只加一个任务类型         ~30 行
```

前端左侧账号栏 + 内嵌浏览器区：~300 行。

**`http_downloader.py` / `ytdlp_engine.py` / `course_engine.py` / 现有 16 个测试
全部不动。**

---

## 7. 明确不做

- 不做视频号 / 快手的链接抓取
- 不做 MITM 抓包
- 不做浏览器指纹伪造
- 不打包 exe（走 pythonw 快捷方式）
- 不让模型直接操控浏览器作为主路径
- 不碰远程机器的 sshd 配置
- 原 16 能力里的：无限画布、AI 生图、TTS/克隆、视频镜头生成、OCR、文档解析

---

## 8. 待确认

| 项 | 默认 |
|----|------|
| 快手要不要单独想办法 | 先按"手动存文件"处理 |
| 内嵌是否要真嵌进主窗口 | 先做独立窗口，`SetParent` 作为可选增强 |
| ASR 默认档 | SenseVoice 免费档，质量档手动切 |
| 发布是否要定时 | 要，执行器自己到点跑，不依赖 Agent 在线 |
