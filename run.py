# -*- coding: utf-8 -*-
"""唯一入口。PyInstaller 也是从这里打包。

    python run.py                        # 网页壳（原生窗口，失败退浏览器）
    python run.py --web                  # 直接用浏览器打开
    python run.py --tk                   # 旧的 Tk 界面（备用）
    python run.py --url "<课程链接>"      # 命令行

fastapi / uvicorn / pywebview 都只在真正需要时才导入：Tk 与命令行用户
不该为它们多等启动时间，也不该因为没装它们就跑不起来。

包内一律用绝对导入（`from xiaoe_dl.x import y`），这样无论从哪个目录调用
都不会因为模块搜索路径而失败。
"""
import contextlib
import os
import sys
import traceback


class _Tee:
    """同时往多个流写。控制台和日志文件各来一份。"""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def _run_cli():
    """带参数启动 -> 命令行模式。

    打包成 --windowed 后没有控制台，所以输出要落一份日志文件，
    否则出错时什么都看不到。源码运行时同时打屏，方便直接看进度。
    """
    from xiaoe_dl.cli import main as cli_main

    log_path = os.path.join(os.environ.get("TEMP", "."), "xiaoe-cli.log")
    real_out, real_err = sys.stdout, sys.stderr
    for st in (real_out, real_err):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass

    with open(log_path, "w", encoding="utf-8") as lf:
        out = lf if real_out is None else _Tee(real_out, lf)
        err = lf if real_err is None else _Tee(real_err, lf)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli_main()
            except SystemExit:
                pass
            except Exception:
                traceback.print_exc()

    if real_out is not None:
        print(f"日志：{log_path}")


def main():
    argv = sys.argv[1:]
    if "--tk" in argv:
        from xiaoe_dl.gui import main_gui

        main_gui()
    elif "--web" in argv:
        from xiaoe_dl.desktop import main as desktop_main

        desktop_main(force_browser=True)
    elif argv:
        _run_cli()
    else:
        from xiaoe_dl.desktop import main as desktop_main

        desktop_main()


if __name__ == "__main__":
    main()
