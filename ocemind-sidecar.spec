# -*- mode: python ; coding: utf-8 -*-
"""Tauri 壳用的 Python sidecar。

用法（从项目根目录执行）：
    python tools/build_sidecar.py

和 ocemind-executor.spec 的区别：
  * 入口一样是 run.py，但只跑 `--serve`，由 Rust 壳负责开窗口；
  * name 用 ASCII（ocemind-sidecar），Tauri 的 externalBin 不认中文文件名；
  * 带上 web 界面和 standards.json —— 这不只是壳的资源，浏览器直连
    这个服务时也要能打开同一个界面；
  * 不排除 Pillow：图像处理（image.edit）是注册过的能力，不能装完才发现缺。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(SPECPATH))
# yt-dlp 运行时下载、不打进包，但它用到的标准库模块得补上，否则冻结进程里
# `import yt_dlp` 会报 No module named 'optparse'（见 tools/ytdlp_stdlib.py）
from tools.ytdlp_stdlib import ytdlp_stdlib_imports  # noqa: E402

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('ocemind/web', 'ocemind/web'),
        ('ocemind/executor/standards.json', 'ocemind/executor'),
    ],
    hiddenimports=['ocemind.crypto'] + ytdlp_stdlib_imports(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PyQt5', 'PySide2', 'IPython', 'pytest',
        # 自动登录 / 发布走系统 Chrome 的 CDP；playwright 的浏览器包
        # 有 150MB，不塞进 sidecar。需要时在源码环境安装。
        'playwright', 'greenlet', 'pyee',
        # 可选本地引擎（OCR / ASR）体积极大，而且 PyInstaller 只能打
        # 进当前解释器的版本；它们由「模型与接口」页按需安装到可写目录，
        # 不该在分析阶段被扫进来。
        'cv2', 'paddle', 'paddleocr', 'paddlex',
        'torch', 'tensorflow', 'onnxruntime', 'rapidocr_onnxruntime',
        'faster_whisper', 'whisper', 'pyclipper', 'shapely', 'lmdb', 'skimage',
        # yt-dlp 由「首次运行按需下载」提供（ocemind/toolchain.py），
        # 绝不能跟着构建机的 pip 环境打进来：那样包里多 6 MB、版本被
        # 冻结在发版那天，下载来的新版本反而永远用不上。
        'yt_dlp',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ocemind-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
