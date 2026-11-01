"""生成可以整包拷走的 OcemindExecutor 目录。

为什么不用 PyInstaller 打 exe：这台机器开着智能应用控制，新生成的 exe
哈希没被信任，双击直接拒绝执行（0xC0E90002）。而 Python 官方的嵌入式
运行时自带签名的 python.exe，不受影响。

用法：

    python tools/make_portable.py                 # 用已有 runtime/，没有就提示
    python tools/make_portable.py --fetch         # 自动下载嵌入式 Python
    python tools/make_portable.py --out D:\\OcemindExecutor

产物结构：

    OcemindExecutor/
    ├── runtime/            python.exe + 标准库（免安装）
    ├── vendor/             fastapi / uvicorn / yt-dlp / pillow / pypdf ...
    ├── app/ocemind/       程序本体
    ├── run.py
    └── 启动.bat
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = "3.12.8"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"

COPY_DIRS = ["ocemind"]
COPY_FILES = ["run.py", "requirements.txt", "README.md"]

LAUNCHER = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0app;%~dp0vendor"
set "PYTHONUTF8=1"
start "" "%~dp0runtime\\pythonw.exe" "%~dp0run.py"
"""

CLI_LAUNCHER = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0app;%~dp0vendor"
set "PYTHONUTF8=1"
"%~dp0runtime\\python.exe" -m ocemind.executor.cli %*
"""

PTH = """app
vendor
"""


def say(text: str) -> None:
    print(f"  {text}")


def ensure_runtime(out: Path, fetch: bool) -> bool:
    runtime = out / "runtime"
    if (runtime / "python.exe").exists():
        say(f"已有运行时：{runtime}")
        return True
    if not fetch:
        say("没有 runtime/python.exe。加 --fetch 自动下载嵌入式 Python，"
            "或手动把解压后的运行时放到该目录。")
        return False

    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / "python-embed.zip"
    say(f"下载 {PY_URL}")
    urllib.request.urlretrieve(PY_URL, zip_path)
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(runtime)
    zip_path.unlink(missing_ok=True)

    # 嵌入式 Python 默认不把当前目录加进 sys.path，也不装 site-packages。
    # 写一个 .pth 同时解决这两件事。
    major, minor = PY_VERSION.split(".")[:2]
    pth = runtime / f"python{major}{minor}._pth"
    pth.write_text(f"python{major}{minor}.zip\n.\napp\nvendor\nimport site\n",
                   encoding="utf-8")
    say(f"已解压到 {runtime}")
    return True


def copy_code(out: Path) -> None:
    app = out / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    for name in COPY_DIRS:
        src = ROOT / name
        if not src.exists():
            continue
        shutil.copytree(src, app / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        say(f"复制 {name}")
    for name in COPY_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, out / name)
            say(f"复制 {name}")


def install_vendor(out: Path) -> None:
    vendor = out / "vendor"
    vendor.mkdir(exist_ok=True)
    req = ROOT / "requirements.txt"
    if not req.exists():
        say("没有 requirements.txt，跳过依赖安装")
        return
    say("安装依赖到 vendor/（这一步要联网，几分钟）")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
           "--target", str(vendor), "-r", str(req),
           "--no-warn-script-location"]
    subprocess.run(cmd, check=False)


def copy_tools(out: Path) -> None:
    """把 ffmpeg 放进 tools/（toolchain.bundled_dir 就找这儿）。

    不塞进 vendor/：那个目录在 PYTHONPATH 上，多一层 tools 容易和模块名打架。
    """
    src = ROOT / "vendor" / "tools"
    if not src.is_dir():
        say("没有 vendor/tools（先跑 python tools/fetch_vendor.py），便携版不带 ffmpeg")
        return
    dst = out / "tools"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    say(f"复制 {dst.relative_to(out)}/")


def write_launchers(out: Path) -> None:
    (out / "启动.bat").write_text(LAUNCHER, encoding="utf-8")
    (out / "ocemind.bat").write_text(CLI_LAUNCHER, encoding="utf-8")
    say("已写 启动.bat / ocemind.bat")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成便携版目录")
    parser.add_argument("--out", default=str(ROOT / "OcemindExecutor"))
    parser.add_argument("--fetch", action="store_true",
                        help="没有运行时就从 python.org 下载嵌入式版本")
    parser.add_argument("--skip-vendor", action="store_true",
                        help="跳过 pip 安装（离线环境用）")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    print(f"输出目录：{out}")

    if not ensure_runtime(out, args.fetch):
        return 1
    copy_code(out)
    if not args.skip_vendor:
        install_vendor(out)
    copy_tools(out)
    write_launchers(out)

    print()
    print("完成。整个目录拷到别的电脑，双击「启动.bat」即可。")
    print("数据（设置、任务、账号 profile）在 %APPDATA%\\ocemind，备份这一个目录就够。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
