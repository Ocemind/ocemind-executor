# -*- coding: utf-8 -*-
"""打包 Python sidecar，并按 Tauri 的命名放进 src-tauri/binaries/。

    python tools/build_sidecar.py

Tauri 要求 externalBin 的文件名带目标三元组，例如：
    ocemind-sidecar-x86_64-pc-windows-msvc.exe
    ocemind-sidecar-x86_64-unknown-linux-gnu
三元组从 `rustc -vV` 的 `host:` 行取，和实际编译 Tauri 的 target 一致。
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "ocemind-sidecar.spec")
BIN_DIR = os.path.join(ROOT, "desktop", "src-tauri", "binaries")
# 和旧的 exe 打包（build/ dist/）分开，别把已经生成的
# dist/小鹅通下载器.exe 顺手清掉。
WORK_DIR = os.path.join(ROOT, "build-sidecar")
DIST_DIR = os.path.join(ROOT, "dist-sidecar")


def host_triple() -> str:
    rustc = shutil.which("rustc") or os.path.join(
        os.path.expanduser("~"), ".cargo", "bin",
        "rustc.exe" if os.name == "nt" else "rustc")
    try:
        out = subprocess.run([rustc, "-vV"], capture_output=True, text=True,
                             check=True).stdout
        for line in out.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    # rustc 不在 PATH 时按平台猜一个；Windows 上 Tauri 只用 MSVC。
    if os.name == "nt":
        return "x86_64-pc-windows-msvc"
    return "x86_64-unknown-linux-gnu"


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 Ocemind Python sidecar")
    ap.add_argument("--no-clean", action="store_true", help="不清理 build/ dist/")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", DIST_DIR, "--workpath", WORK_DIR,
        SPEC,
    ]
    if not args.no_clean:
        cmd.insert(3, "--clean")
    print(f"[*] 打包 sidecar：{' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"[!] PyInstaller 失败，退出码 {result.returncode}")
        return result.returncode

    name = "ocemind-sidecar.exe" if os.name == "nt" else "ocemind-sidecar"
    built = os.path.join(DIST_DIR, name)
    if not os.path.isfile(built):
        print(f"[!] 没找到产物：{built}")
        return 1

    triple = host_triple()
    ext = ".exe" if os.name == "nt" else ""
    target_name = f"ocemind-sidecar-{triple}{ext}"
    os.makedirs(BIN_DIR, exist_ok=True)
    target = os.path.join(BIN_DIR, target_name)
    shutil.copy2(built, target)
    size = os.path.getsize(target) / 1024 / 1024
    print(f"[*] 已放到：{target}  ({size:.1f} MB)")
    print("[*] 下一步：cd desktop && npm run build:windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
