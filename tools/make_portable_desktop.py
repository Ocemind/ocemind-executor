# -*- coding: utf-8 -*-
"""把 Tauri 的 release 产物打成一个整包拷走的 zip。

    python tools/make_portable_desktop.py

依赖已经跑过：
    python tools/build_sidecar.py
    cd desktop && npm run build:windows

本机被 SAC 拦住 release 构建时，用 `--profile debug` 拿 target/debug 的
产物试打包（配 OCEMIND_SIDECAR_FROZEN=1 才会跑包里的冻结 sidecar）。

产物：
    dist/Ocemind-portable.zip
        ocemind-executor.exe
        ocemind-sidecar.exe
        tools/ffmpeg/…        ← 安装包版由 Tauri 拷，便携版这里自己拷
        portable.ini          ← 壳看到它就把数据写进 data/，不碰用户目录
        启动.bat
        使用说明.txt

WebView2 运行时是 Windows 11 自带的；Win10 需要系统里已装过 Edge。
"""
import argparse
import glob
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIR = os.path.join(ROOT, "desktop", "src-tauri", "binaries")
OUT_DIR = os.path.join(ROOT, "dist")


def find_one(patterns):
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Ocemind 便携版 zip")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "Ocemind-portable.zip"))
    # 正式包用 release；release 在这台机器上会被 SAC 拦，调试时用 debug 产物试打包。
    ap.add_argument("--profile", default="release", choices=["release", "debug"])
    args = ap.parse_args()

    target_dir = os.path.join(
        ROOT, "desktop", "src-tauri", "target", args.profile)

    # 不要用 *.exe 兜底：sidecar 也在同一个目录，挑错了会把内核当主程序
    # 打进 zip，解压后双击没有任何界面。
    exe = find_one([
        os.path.join(target_dir, "ocemind-executor.exe"),
        os.path.join(target_dir, "ocemind-desktop.exe"),
    ])
    sidecar = find_one([
        os.path.join(target_dir, "ocemind-sidecar.exe"),
        os.path.join(BIN_DIR, "ocemind-sidecar-*.exe"),
    ])
    if not exe or not os.path.isfile(exe):
        print(f"[!] 找不到主程序，先跑 desktop/npm run build:windows：{target_dir}")
        return 1
    if not sidecar or not os.path.isfile(sidecar):
        print("[!] 找不到 sidecar，先跑 python tools/build_sidecar.py")
        return 1

    # 安装包里的 ffmpeg 是 Tauri 按 bundle.resources 拷到程序目录的；便携版
    # 没有安装器，得自己带上，否则拷到没装 ffmpeg 的电脑上剪辑功能直接哑火。
    tools_src = os.path.join(target_dir, "tools", "ffmpeg")
    if not os.path.isdir(tools_src):
        tools_src = os.path.join(ROOT, "vendor", "tools", "ffmpeg")
    tools_ok = os.path.isdir(tools_src)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    readme = (
        "Ocemind 内容执行器（便携版）\r\n"
        "==============================\r\n\r\n"
        "双击「启动.bat」或 ocemind-executor.exe。\r\n"
        "数据、设置、浏览器 profile 都放在本目录的 data\\ 下，\r\n"
        "拷到别的电脑时整个文件夹一起拷走即可。\r\n\r\n"
        "ffmpeg 已放在 tools\\ffmpeg 下，不用另外装；\r\n"
        "对方电脑上已经装了的话，会优先用系统里那个。\r\n\r\n"
        "需要系统里有 WebView2 运行时（Windows 11 自带；\r\n"
        "Win10 装过 Edge 的也有）。\r\n"
    )
    launcher = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"ocemind-executor.exe\"\r\n"
    )

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, "ocemind-executor.exe")
        zf.write(sidecar, "ocemind-sidecar.exe")
        zf.writestr("portable.ini", "[portable]\r\n")
        zf.writestr("启动.bat", launcher.encode("gbk"))
        zf.writestr("使用说明.txt", readme.encode("utf-8-sig"))
        if tools_ok:
            for base, _, files in os.walk(tools_src):
                for name in files:
                    full = os.path.join(base, name)
                    rel = os.path.relpath(full, tools_src)
                    zf.write(full, os.path.join("tools", "ffmpeg", rel).replace(os.sep, "/"))
        else:
            print("[!] 没找到 vendor/tools/ffmpeg，便携包里不带 ffmpeg"
                  "（先跑 python tools/fetch_vendor.py）")
    size = os.path.getsize(args.out) / 1024 / 1024
    print(f"[*] 已生成：{args.out}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
