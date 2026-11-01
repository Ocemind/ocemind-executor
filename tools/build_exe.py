# -*- coding: utf-8 -*-
"""一条命令：打包 exe 并部署到桌面。

    python tools/build_exe.py             # 打包 + 复制到桌面
    python tools/build_exe.py --no-deploy # 只打包

桌面是 D:\\桌面（这台机器的桌面被重定向过），不是 C:\\Users\\<用户>\\Desktop。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 开发工具在包外，直接 `python tools/build_exe.py` 时 ROOT 不在 sys.path 上
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ocemind.paths import desktop_dir  # noqa: E402

SPEC = os.path.join(ROOT, "ocemind-executor.spec")
EXE_NAME = "Ocemind 内容执行器.exe"


def human(n):
    return f"{n / 1024 / 1024:.1f} MB"


def main():
    ap = argparse.ArgumentParser(description="打包并部署 Ocemind 内容执行器 exe")
    ap.add_argument("--no-deploy", action="store_true", help="只打包，不复制到桌面")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"[*] 项目根：{ROOT}")
    for leftover in ("build", "dist"):
        p = os.path.join(ROOT, leftover)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            print(f"[*] 已清理 {leftover}/")

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC]
    print(f"[*] 打包中：{' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        return f"[!] PyInstaller 失败，退出码 {r.returncode}"

    hits = glob.glob(os.path.join(ROOT, "dist", EXE_NAME))
    if not hits:
        return f"[!] 没找到产物 {EXE_NAME}，检查 spec 里的 name 字段"
    exe = hits[0]
    size = os.path.getsize(exe)
    print(f"[*] 产物：{exe}  ({human(size)})")

    # playwright 的浏览器二进制有 150MB。体积突然变大基本就是它被误打进去了。
    if size > 40 * 1024 * 1024:
        print(f"[!] 体积异常（>40MB）。八成是 playwright 没排除干净，"
              f"检查 spec 的 excludes")

    if args.no_deploy:
        return None

    desktop = desktop_dir()
    if not desktop:
        print("[!] 没找到桌面目录，跳过部署。产物在 dist/ 下")
        return None
    dest = os.path.join(desktop, EXE_NAME)
    if os.path.exists(dest):
        shutil.copy2(dest, dest + ".bak")
    shutil.copy2(exe, dest)
    print(f"[*] 已部署到：{dest}")
    return None


if __name__ == "__main__":
    msg = main()
    if msg:
        print(msg)
        sys.exit(1)
