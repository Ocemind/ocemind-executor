# -*- coding: utf-8 -*-
"""小鹅通回放下载器。

刻意不在这里 import 子模块：那会让 `import xiaoe_dl.crypto` 顺带拖进
requests / tkinter，也会给 PyInstaller 的静态分析添乱。
"""

__version__ = "1.0.0"
