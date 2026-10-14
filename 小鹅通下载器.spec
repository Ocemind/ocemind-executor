# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（必须从项目根目录执行）：
    python -m PyInstaller --noconfirm --clean "小鹅通下载器.spec"

三个容易踩的坑，改这个文件时留意：
  1. 入口必须是 run.py（项目根），pathex 要给 "."，否则包 xiaoe_dl 找不到
  2. hiddenimports 用**包内的完整模块名**。写成旧名 'xiaoe_crypto' / 'download'
     不会报错，只会静默漏掉模块，等运行到那一步才炸
  3. playwright 必须排除：它的浏览器二进制有 150MB，塞进 exe 会撑爆体积，
     而且 exe 本来就不支持自动登录（见 README）
"""

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    # core.py 里是模块级 `from xiaoe_dl import crypto`，静态分析本来就能找到；
    # 这里再写一遍是保险——它曾经被包在 try/except 里静默 import。
    hiddenimports=['xiaoe_dl.crypto'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 体积 / 无关依赖
        'matplotlib', 'numpy', 'pandas', 'PIL', 'scipy',
        'PyQt5', 'PySide2', 'IPython', 'pytest',
        # 自动登录相关：exe 不带，走手动导入 cookies
        'playwright', 'greenlet', 'pyee',
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
    name='小鹅通下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
