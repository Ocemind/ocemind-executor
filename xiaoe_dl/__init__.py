# -*- coding: utf-8 -*-
"""Timi 下载器：小鹅通课程 + 平台视频 + 任意直链。

刻意不在这里 import 子模块：那会让 `import xiaoe_dl.crypto` 顺带拖进
requests / tkinter / fastapi，也会给 PyInstaller 的静态分析添乱。
"""

__version__ = "1.1.0"
