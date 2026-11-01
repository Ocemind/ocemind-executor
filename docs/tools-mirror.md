# 外部工具镜像：部署与同步

客户端要用 ffmpeg 和 yt-dlp，但这两个都是独立项目、更新频繁，打进安装包
就得跟着重发。所以做成**首次运行时按需下载**：先看本机装没装，没有再下。

GitHub 在国内不稳（TLS 半路断很常见），于是下载源分两级：

```
客户端 ──①── <镜像>/index.json + <镜像>/<工具>/<平台>/<文件>
        └──②── GitHub Release（镜像不可用时兜底）
```

本文说的是第 ① 级的那个镜像怎么搭、怎么保持和 GitHub 同步。

---

## 1. 镜像长什么样

就是一组静态文件，任何 nginx / Caddy / 对象存储都能托管：

```
/srv/www/tools/
├── index.json                                  # 清单：地址、大小、SHA-256、版本
├── ffmpeg/windows-x86_64/ffmpeg-master-latest-win64-lgpl.zip
├── ffmpeg/linux-x86_64/ffmpeg-master-latest-linux64-lgpl.tar.xz
├── ffmpeg/linux-aarch64/ffmpeg-master-latest-linuxarm64-lgpl.tar.xz
└── ytdlp/any/yt-dlp.zip
```

`index.json` 的条目：

```json
{
  "schema": 1,
  "generated": "2026-11-01T09:00:00Z",
  "tools": {
    "ytdlp": {"any": {
      "url": "ytdlp/any/yt-dlp.zip",
      "sha256": "…",
      "size": 3073072,
      "version": "2026.10.26",
      "source": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp"
    }}
  }
}
```

客户端只认 `url`（相对路径按镜像根拼）、`sha256`、`size`、`version`。
**没有 `sha256` 的条目不用**——校验不过就重新下，绝不会装一个不知道来源的
可执行文件。

---

## 2. 同步脚本

`tools/sync_tools_mirror.py`，只用标准库，在哪跑都行。

```bash
# 只在本地产出（最安全）
python tools/sync_tools_mirror.py --out /srv/www/tools

# 只同步某个平台，省流量
python tools/sync_tools_mirror.py --out /srv/www/tools --platform windows-x86_64

# 本地产出后推到服务器；不带 --delete，不会动同目录的其它文件
python tools/sync_tools_mirror.py --out ./mirror --upload user@host:/srv/www/tools

# 发布前自检：按 index.json 重新算一遍 SHA-256
python tools/sync_tools_mirror.py --out /srv/www/tools --verify

# 只看会下什么，不落盘
python tools/sync_tools_mirror.py --out ./mirror --dry-run
```

脚本的取法是「读 GitHub Release → 挑资产 → 下载 → 算 SHA-256 → 写清单」。
已经是最新的不会重复下载（优先比 GitHub 给的资产 digest，没有就比资产的
`updated_at`）。GitHub API 匿名够用，被限流了设 `GITHUB_TOKEN`。

---

## 3. 部署

以 1Panel + 静态网站为例（也适用于任何已有站点）：

```bash
sudo mkdir -p /srv/www/tools
sudo chown $USER /srv/www/tools
```

1Panel → 网站 → 建一个静态站点（比如 `tools.example.com`），根目录指向
`/srv/www/tools`。nginx 默认就会返回 `index.json`，不需要额外配置。

定时同步（systemd timer 比 cron 好排查）：

```ini
# /etc/systemd/system/ocemind-tools-sync.service
[Unit]
Description=Sync ffmpeg/yt-dlp mirror from GitHub

[Service]
Type=oneshot
WorkingDirectory=/opt/ocemind-executor
ExecStart=/usr/bin/python3 tools/sync_tools_mirror.py --out /srv/www/tools
```

```ini
# /etc/systemd/system/ocemind-tools-sync.timer
[Unit]
Description=Daily ocemind tools mirror sync

[Timer]
OnCalendar=*-*-* 04:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ocemind-tools-sync.timer
sudo systemctl list-timers ocemind-tools-sync.timer   # 看下次跑的时间
```

> 服务器上只新增 `/srv/www/tools` 和这两个 unit 文件，不碰现有站点、
> 不改 sshd、不占端口。同步失败不影响任何正在跑的服务。

---

## 4. 让客户端用上镜像

三种方式，优先级从高到低：

| 方式 | 适合 |
|------|------|
| 设置 → 外部工具 → 「工具镜像」 | 单个用户临时改 |
| 环境变量 `OCEMIND_TOOLS_MIRROR` | 批量部署 / 脚本 |
| `ocemind/toolchain.py` 里的 `DEFAULT_MIRROR` 常量 | 想让所有安装包默认走镜像 |

`DEFAULT_MIRROR` 填上之后要重新打包才生效；它只是默认值，用户仍可在设置里
覆盖。没配镜像或者镜像挂了，客户端会回落到 GitHub，不会因此卡住。

---

## 5. 以后再加一个工具

三步：

1. `ocemind/toolchain.py` 的 `FALLBACK_URLS` 加一条上游地址；
2. 同文件里加安装函数（`_install_xxx`），在 `ensure()` 里接上；
3. `tools/sync_tools_mirror.py` 的 `REPOS` / `ASSET_SUFFIX` 各加一条，
   让镜像把新工具也同步过去。

下载、校验、重试、进度回显这些都不用重写。
