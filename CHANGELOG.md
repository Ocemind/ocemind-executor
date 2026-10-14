# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
版本号见 `xiaoe_dl/__init__.py`。

## [1.0.0] - 2026-10-14

第一次工程化整理。此前是「一个能跑的脚本文件夹」，这版之后是「能长期维护的项目」。

### 新增

- **`tools/diagnose.py`** —— 分阶段链路诊断。把整条链路拆成 7 段依次探测，
  直接报出**首个失败阶段**。网站改版时先跑它找断点，不用再从零逆向
- **`xiaoe_dl/auth.py`** —— 登录态双模式。手动导入 cookies.txt 之外，
  还能开真浏览器让你登一次（扫码 / 手机验证码 / 密码都行，程序不假定登录方式）
- **`tools/make_launcher.py`** —— 在桌面建/修/删源码启动器
- **`tools/build_exe.py`** —— 一条命令打包并部署 exe
- **`tests/`** —— 137 条单测，`python -m pytest` 跑，全部离线
- **`docs/api_chain.md`** —— 三跳接口链路 + **改版对照表**（断点 → 该改哪个函数）
- **`run.py`** —— 唯一入口：无参数起 GUI，带参数走命令行
- CLI 新增 `--login` / `--no-browser`
- GUI 新增「登录一次」按钮与登录状态区

### 变更

- **包化为 `xiaoe_dl/`**。`download.py` / `gui.py` / `xiaoe_crypto.py` /
  `xiaoe_cookies.py` 分别改名为 `core.py` / `gui.py` / `crypto.py` / `cookies.py`。
  **不保留旧名兼容**（只有一个使用者，README 同步改了）
- cookie 一律按域过滤后再拼 `Cookie` 头。多个店铺子域会产生多条同名
  `ko_token`，全拼进一个头服务端行为未定义
- 桌面交付从 exe 改为**源码启动器**（原因见下）

### 修复

- **`find_ko_token` 的跨账号凭证泄漏**：域过滤后没找到时，原实现会回退到
  「所有候选」，等于把 A 店铺的登录态发给 B 店铺。已删掉这个回退，并加了回归测试
- **`grab_playlist` 里的静默 import**：`sys.path.insert` + `try/except` 包着
  `import xiaoe_crypto`，模块名变了也不报错，只会静默少一个功能。改成正常导入
- **`cookie_candidates()` 的路径漂移**：包化后 `dirname(__file__)` 从项目根变成
  了包目录，「cookies.txt 放在 exe 旁边」的语义会悄悄失效
- **`crypto.decode()` 的默认密钥**：原来不传 key 时会静默用 JS 里的兜底值
  `appbgzjnopv1917`，解出乱码但不报错。现在 key 是必填参数
- 源码运行时 `python run.py --url ...` 的输出以前全进了
  `%TEMP%\xiaoe-cli.log`，控制台什么都看不到。改为同时写控制台和日志

### 已知问题

- **本机上重建的 exe 无法运行。** 这台机器开着智能应用控制（Smart App Control，
  强制模式），它按微软 ISG 信誉放行可执行文件——每重新打包一次就是一份新哈希，
  要重新攒一次信誉。实测重建的 exe 被拒（退出码 126，CodeIntegrity 事件 3077）。
  桌面因此改用指向 `pythonw.exe` 的快捷方式，不受该策略约束，且永远是最新代码。
  旧的 exe 保留为「小鹅通下载器（旧版备用）.exe」作兜底
- `crypto.decode()` 的 round-trip 测试只能证明**解码器相对自身自洽**，
  不能证明对真实混淆样本解对——手上已无真样本（探索期的 `_probe.bin` 已清理）。
  真遇到 `[xiaoe]` 响应时用 `XIAOE_DUMP_RAW=<路径>` 落一份样本

### 未做（明确排除）

- 批量课程管理（清单 / 断点续传 / 去重）
- 版本发布流程工程、CI
- 把 Playwright 打进 exe
- `core.py` 内部逻辑重构（能跑就不动，这次只做移动和改导入）
