# 外部工具下载源：放在 Gitee 上

客户端要用 ffmpeg 和 yt-dlp，但这两个都是独立项目、更新频繁，打进安装包
就得跟着重发。所以做成**首次运行时按需下载**：先看本机装没装，没有再下。

GitHub 在国内不稳，于是下载源分两级：

```
客户端 ──①── Gitee 上的那份拷贝（index.json + 文件）
        └──②── GitHub Release（Gitee 不可用时兜底）
```

① 就是**一个公开的 Gitee 仓库**，不需要服务器、不需要域名、不需要定时任务。

---

## 1. 生成要传的文件

```bash
python tools/sync_tools_mirror.py --out ./mirror
```

产出：

```
mirror/
├── index.json
├── ffmpeg/windows-x86_64/<ffmpeg 压缩包>      # 共享版，约 78 MB
└── ytdlp/any/yt-dlp.zip                        # 约 3 MB
```

为什么用共享版而不是静态版：Gitee 单文件上限 100 MB，静态版 167 MB 传不上去；
共享版 78 MB，解压后 ffmpeg.exe 旁边的 dll 一起用，功能一样。

> 默认只同步 Windows。Linux 静态版 130 MB、超过 Gitee 单文件上限，所以
> 不上镜像——Linux 用户走发行版自带的 ffmpeg（apt/dnf 装了就有），
> 真的没有时才回落 GitHub。

---

## 2. 传到 Gitee

1. 在 Gitee 新建一个**公开**仓库，比如 `ocemind-tools`，不要勾选初始化
   README（省得合并麻烦）；
2. 把 `mirror/` 里的内容整个传上去。两种传法任选：
   - 网页：仓库首页「上传文件」，把 `index.json`、两个目录里的文件按原路径传；
   - 命令行（推荐，路径不会传错）：
     ```bash
     cd mirror
     git init && git add .
     git commit -m "tools 2026-11-01"
     git remote add origin git@gitee.com:<你>/ocemind-tools.git
     git push -u origin master
     ```

传完以后，你需要的那串地址是：

```
https://gitee.com/<你>/ocemind-tools/raw/master/<仓库里的目录>/
```

如果 `index.json` 在仓库根目录，就是
`https://gitee.com/<你>/ocemind-tools/raw/master`。
不确定的话跑 `python tools/sync_tools_mirror.py --out ./mirror --gitee <你>/ocemind-tools`
让它直接打印该填的地址。

---

## 3. 让客户端用上

设置 → 外部工具 → 「工具镜像」填上面那串地址即可。也可以在
`ocemind/toolchain.py` 的 `DEFAULT_MIRROR` 里写死，重新打包后所有安装包
默认走它（用户仍可覆盖）。

客户端会先取 `<镜像>/index.json`，按里面的地址下载并校验 SHA-256；
Gitee 打不开或者清单里没有这个平台，就回落到 GitHub，不会卡住。

---

## 4. 以后怎么更新

上游发新版本后，重跑一次第 1 步（已是最新的不会重复下载），再把变动的
文件覆盖上传、`git push` 一次就行。`--verify` 可以自检清单和文件对不对：

```bash
python tools/sync_tools_mirror.py --out ./mirror --verify
```

想省事也可以把这套跑在一个能连 GitHub 的机器上（比如你现有的服务器），
产出的 `mirror/` 目录再传 Gitee——服务器只当「下载器」，不对外提供访问。

---

## 5. 以后再加一个工具

三步：

1. `ocemind/toolchain.py` 的 `FALLBACK_URLS` 加一条上游地址；
2. 同文件里加安装函数（`_install_xxx`），在 `ensure()` 里接上；
3. `tools/sync_tools_mirror.py` 的 `REPOS` / `ASSET_SUFFIX` 各加一条。

下载、校验、重试、进度回显都不用重写。
