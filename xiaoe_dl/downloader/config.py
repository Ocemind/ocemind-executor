"""配置与数据目录。

合并之后所有数据都放在 `%APPDATA%\\xiaoe-dl`（`paths.app_dir()`）：
`settings.json` 是这里的设置，`tasks.json` 是任务队列；同目录下的
`config.json` 由旧的 Tk 界面使用，保存时会顺带同步 `out_dir`，两边看到的是
同一个「默认保存目录」。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from xiaoe_dl import paths

#: 合并前 tdm-lite 的数据目录。只读不写，且只在新文件不存在时迁一次。
LEGACY_HOME_NAME = "TDMLite"


def _default_dir() -> str:
    for cand in (Path("D:/视频"), Path("D:/下载"), Path("D:/Downloads"),
                 Path.home() / "Downloads"):
        if cand.exists():
            return str(cand)
    return str(Path.home() / "Downloads")


DEFAULT_SETTINGS = {
    "defaultDir": _default_dir(),
    "defaultConnections": 8,
    "maxConcurrent": 3,
    "maxRateKB": 0,              # 0 = 不限速
    "minChunkMB": 2,             # 小于该体积不走分片
    "askVideoQuality": True,
    "defaultQuality": 1080,
    "cookieBrowser": "",         # chrome / edge / firefox / ""
    "proxyEnabled": False,
    "proxyType": "http",
    "proxyHost": "",
    "proxyPort": "",
    "proxyUser": "",
    "proxyPass": "",
    "ytdlpPath": "",
    "ffmpegPath": "",
    "autoLaunch": False,
    # 课程平台课程
    "courseWorkers": 24,
    "courseLine": "",            # 空=第 1 条线路，也可填 tx/hw/byte
    "courseKeepTs": False,
    "courseCookieFile": "",      # 空=用规范位置 cookies.txt

    # ---------------- 执行器：用哪个后端（本地优先，见 executor/backends.py） ----
    # models 按能力保存选择：{"transcribe.asr": {"backend": "auto",
    #   "provider": "siliconflow", "model": "...", "localModel": "small"}}
    "models": {},
    "localModelDir": "",            # 本地模型缓存目录，留空用 HuggingFace 默认
    # 各服务商密钥（按能力分别选服务商，互不影响）
    "siliconflowKey": "",
    "dashscopeKey": "",
    "deepseekKey": "",
    "groqKey": "",
    "openaiKey": "",
    "customBaseUrl": "",
    "customKey": "",
    # 兼容旧字段：没有 models 选择时作为回退
    "asrProvider": "siliconflow",
    "asrBaseUrl": "",
    "asrModel": "",
    "asrLanguage": "zh",
    "autoTranscribe": True,         # 下载完自动转写
    "monthlyBudget": 100.0,         # 元；超了直接拒绝付费调用

    # ---------------- 执行器：账号浏览器 ----------------
    "chromePath": "",
    "headless": False,
    "screencastQuality": 70,
    "screencastMaxWidth": 1280,
    "keepBrowserAliveSeconds": 900,

    # ---------------- 执行器：Agent 接入 ----------------
    "apiToken": "",

    # ---------------- 执行器：飞书协同（产出索引 + 云文档） ----------------
    # 走本机已登录的 lark-cli，不在这里存 App Secret
    "feishuEnabled": False,
    "feishuCli": "",                 # 空 = 从 PATH 找 lark-cli
    "feishuBaseToken": "",           # 多维表格 app_token，也可直接粘表格链接
    "feishuTableId": "",             # 索引表 table_id
    "feishuFolderToken": "",         # 云文档放哪个文件夹，空 = 我的空间
    "feishuDocMode": "text",         # text = 文字产出转云文档；off = 只登记索引
}


class Config:
    """设置读写。路径在构造时确定，方便测试用临时目录隔离。"""

    def __init__(self, home: str | os.PathLike | None = None,
                 legacy_home: str | os.PathLike | None = None) -> None:
        self._lock = threading.RLock()
        self.home = Path(home) if home else Path(paths.app_dir())
        self.settings_path = self.home / "settings.json"
        self.tasks_path = self.home / "tasks.json"
        self.log_path = self.home / "app.log"
        # 旧 Tk 界面的配置文件，保持 out_dir 同步
        self.tk_config_path = self.home / "config.json"

        if legacy_home is not None:
            self.legacy_home = Path(legacy_home)
        elif home is None:
            # 只有用默认数据目录时才去碰真实机器上的旧目录；
            # 测试传了 home 就绝不读真实环境。
            base = os.environ.get("APPDATA") or str(Path.home())
            self.legacy_home = Path(base) / LEGACY_HOME_NAME
        else:
            self.legacy_home = None

        self.data = dict(DEFAULT_SETTINGS)
        self._migrate_legacy()
        self.load()

    # ------------------------------------------------------------ 迁移
    def _migrate_legacy(self) -> None:
        """把 tdm-lite 的旧数据和 Tk 的 out_dir 接过来。幂等，不删旧文件。"""
        if self.settings_path.exists():
            return
        self.home.mkdir(parents=True, exist_ok=True)
        data = dict(DEFAULT_SETTINGS)
        explicit_dir = None

        legacy_settings = (self.legacy_home / "settings.json"
                           if self.legacy_home else None)
        if legacy_settings and legacy_settings.exists():
            try:
                saved = json.loads(legacy_settings.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            if isinstance(saved, dict):
                for key, value in saved.items():
                    if key in DEFAULT_SETTINGS:
                        data[key] = value
                if "defaultDir" in saved:
                    explicit_dir = saved["defaultDir"]

        if explicit_dir is None:
            try:
                tk = json.loads(self.tk_config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                tk = {}
            if isinstance(tk, dict) and tk.get("out_dir"):
                data["defaultDir"] = tk["out_dir"]

        self._write_settings(data)

        legacy_tasks = (self.legacy_home / "tasks.json"
                        if self.legacy_home else None)
        if legacy_tasks and legacy_tasks.exists() and not self.tasks_path.exists():
            try:
                shutil.copy2(legacy_tasks, self.tasks_path)
            except OSError:
                pass

    # ------------------------------------------------------------ 读写
    def _write_settings(self, data: dict) -> None:
        tmp = self.settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.settings_path)

    def load(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        if self.settings_path.exists():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for key, value in saved.items():
                        if key in DEFAULT_SETTINGS:
                            self.data[key] = value
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            self._write_settings(self.data)
            self._sync_tk_config()

    def _sync_tk_config(self) -> None:
        """Tk 界面只认 config.json 的 out_dir，别让两个入口看到不同的目录。"""
        tk: dict = {}
        try:
            loaded = json.loads(self.tk_config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                tk = loaded
        except (OSError, ValueError):
            pass
        tk["out_dir"] = self.data.get("defaultDir", "")
        try:
            tmp = self.tk_config_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(tk, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.tk_config_path)
        except OSError:
            pass

    # ------------------------------------------------------------ 访问
    def get(self, key: str, default=None):
        with self._lock:
            return self.data.get(key, DEFAULT_SETTINGS.get(key, default))

    def update(self, patch: dict) -> dict:
        with self._lock:
            old_ffmpeg = self.data.get("ffmpegPath")
            for key, value in patch.items():
                if key in DEFAULT_SETTINGS:
                    self.data[key] = value
            self.save()
            if self.data.get("ffmpegPath") != old_ffmpeg:
                paths.reset_ffmpeg_cache()
            return dict(self.data)

    def proxies(self) -> dict | None:
        if not self.get("proxyEnabled") or not self.get("proxyHost"):
            return None
        auth = ""
        if self.get("proxyUser"):
            auth = f"{self.get('proxyUser')}:{self.get('proxyPass')}@"
        scheme = self.get("proxyType") or "http"
        port = self.get("proxyPort")
        host = f"{auth}{self.get('proxyHost')}" + (f":{port}" if port else "")
        url = f"{scheme}://{host}"
        return {"http": url, "https": url}
