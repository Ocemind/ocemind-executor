# 手动联网测试

这里的脚本**不属于 pytest**（文件名 `manual_*.py`，不会被收集），因为它们都要
真实访问外网、下几十 MB 的文件，跑一遍要几分钟。默认测试集必须离线秒过。

从更早的独立仓库迁来时只改了三处：`backend.*` → `ocemind.downloader.*`、
数据目录环境变量改名成 `OCEMIND_HOME`、包根路径多退了一层。

| 脚本 | 验什么 | 大概耗时 |
|------|--------|----------|
| `manual_local_e2e.py` | **不用外网**：本机起下载源，走完添加→下载→校验→暂停→续传→重启恢复→重复保护 | 40 秒 |
| `manual_downloader.py` | 分片引擎完整性、多线程与单线程结果一致、暂停续传 | 2–4 分钟 |
| `manual_api.py` | 真起服务走一遍添加/暂停/续传/视频 | 2–3 分钟 |
| `manual_errors.py` | 404 必须报 error，不能变 paused、不能把错误页当文件 | 30 秒 |
| `manual_video.py` | yt-dlp 探测与下载 | 1 分钟 |
| `manual_video_pause.py` | 视频下载中途停止 | 1 分钟 |

跑法：

```
python tests/manual/manual_downloader.py
```

它们各自用临时目录当数据目录，不会碰 `%APPDATA%\ocemind`。
