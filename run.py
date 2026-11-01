# -*- coding: utf-8 -*-
"""唯一入口。PyInstaller 也是从这里打包。

    python run.py                        # 网页壳（原生窗口，失败退浏览器）
    python run.py --web                  # 直接用浏览器打开
    python run.py --serve --port 8765    # 只起本地服务，给 Tauri 壳当 sidecar
    python run.py --tk                   # 旧的 Tk 界面（备用）
    python run.py --exec capabilities    # 执行器的 CLI（给 Agent 用）
    python run.py --url "<课程链接>"      # 命令行

fastapi / uvicorn / pywebview 都只在真正需要时才导入：Tk 与命令行用户
不该为它们多等启动时间，也不该因为没装它们就跑不起来。

包内一律用绝对导入（`from ocemind.x import y`），这样无论从哪个目录调用
都不会因为模块搜索路径而失败。
"""
import contextlib
import os
import sys
import threading
import time
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
    from ocemind.cli import main as cli_main

    log_path = os.path.join(os.environ.get("TEMP", "."), "ocemind-cli.log")
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


def _serve(argv):
    """只起本地 HTTP 服务，不开窗口。Tauri 壳用这个当 sidecar。

    端口默认取 `OCEMIND_PORT`，也可以 `--port` 指定；传 0 让系统分配。
    服务起来后往 stdout 打一行 `OCEMIND_READY port=...`，壳层用它确认
    端口（即使传了 0 也能拿到真实值）。
    """
    import uvicorn

    from ocemind.downloader.api import create_app
    from ocemind.downloader.config import Config
    from ocemind.downloader.manager import TaskManager

    port = int(os.environ.get("OCEMIND_PORT") or "8765")
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            print("--port 需要一个数字", file=sys.stderr)
            raise SystemExit(2) from None

    cfg = Config()
    # 打包成窗口程序（console=False）时 stdout/stderr 可能是 None；
    # 有 Rust 壳接管管道时它们是真流，这里不会动。
    from ocemind.desktop import _redirect_stdout

    _redirect_stdout(cfg.log_path)

    manager = TaskManager(cfg)
    app = create_app(cfg, manager)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", access_log=False,
    ))
    # /api/shutdown 靠它把 uvicorn 的退出标志立起来
    app.state.server = server

    def announce():
        for _ in range(1200):  # 最多等 60 秒
            if server.started:
                actual = port
                try:
                    actual = server.servers[0].sockets[0].getsockname()[1]
                except (AttributeError, IndexError, OSError):
                    pass
                print(f"OCEMIND_READY port={actual}", flush=True)
                return
            if server.should_exit:
                return
            time.sleep(0.05)

    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run()
    finally:
        executor = getattr(app.state, "executor", None)
        if executor is not None:
            try:
                executor.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            manager.shutdown()
        except Exception:  # noqa: BLE001
            pass


def main():
    argv = sys.argv[1:]
    if "--run-ytdlp" in argv:
        # 打包后没有独立的 yt-dlp 可执行文件，下载引擎靠这个入口让 sidecar
        # 用自己的解释器跑包里的 yt_dlp
        from ocemind.toolchain import run_ytdlp

        raise SystemExit(run_ytdlp(argv[argv.index("--run-ytdlp") + 1:]))
    if "--serve" in argv:
        _serve(argv)
    elif "--tk" in argv:
        from ocemind.gui import main_gui

        main_gui()
    elif "--web" in argv:
        from ocemind.desktop import main as desktop_main

        desktop_main(force_browser=True)
    elif "--exec" in argv:
        # 执行器的命令行：和界面共用同一个后端，只是换个入口
        from ocemind.executor.cli import main as exec_main

        raise SystemExit(exec_main(argv[argv.index("--exec") + 1:]))
    elif argv:
        _run_cli()
    else:
        from ocemind.desktop import main as desktop_main

        desktop_main()


if __name__ == "__main__":
    main()
