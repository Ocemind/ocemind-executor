# -*- coding: utf-8 -*-
"""
小鹅通视频下载器 —— 图形界面

双击 exe 打开窗口 -> 粘贴课程链接 -> 点「开始下载」。
带参数启动时（--url / --list ...）自动切换成命令行模式，方便脚本调用。
"""
import contextlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from types import SimpleNamespace

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import download

APP_TITLE = "小鹅通视频下载器"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          "xiaoe-dl")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_DIR = r"D:\视频\交易训练营"
PCT_RE = re.compile(r"(\d+)/(\d+)\s*\((\d+)%\)")
URL_RE = re.compile(r"https?://[^\s]*xiaoeknow\.com[^\s]*")


# ---------------------------------------------------------------- 配置
def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def cookie_status(path):
    """返回 (条数, 到期说明)。只统计元数据，不读值。

    真正决定鉴权的是 ko_token 这一条（实测单独用它就能调通接口），
    所以优先报它的有效期；报不出来才退回「最早过期」。
    """
    if not path or not os.path.exists(path):
        return None, None
    import time as _t
    n = 0
    key_exp = None
    earliest = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = line.split("\t")
                if len(p) < 7:
                    continue
                n += 1
                try:
                    exp = int(p[4])
                except ValueError:
                    continue
                if exp <= 0:
                    continue
                if p[5] == "ko_token":
                    key_exp = exp
                if earliest is None or exp < earliest:
                    earliest = exp
    except Exception:
        return None, None

    def fmt(e):
        return _t.strftime("%Y-%m-%d %H:%M", _t.localtime(e))

    now = _t.time()
    if key_exp:
        left = key_exp - now
        if left <= 0:
            return n, "⚠ 登录凭证已过期，需要重新导入 cookies"
        days = int(left // 86400)
        when = "今天" if days == 0 else f"{days} 天后"
        return n, f"登录凭证有效至 {fmt(key_exp)}（还剩 {when}）"
    if earliest:
        return n, f"最早 {fmt(earliest)} 过期"
    return n, None


def clipboard_xiaoe_url(root):
    """剪贴板里正好是课程链接就拿来预填。"""
    try:
        s = (root.clipboard_get() or "").strip()
        m = URL_RE.search(s)
        return m.group(0) if m else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- 主窗口
class App:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.worker = None
        self.last_out_dir = self.cfg.get("out_dir") or DEFAULT_DIR
        self.total_urls = 0
        self.done_urls = 0

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("760x600")
        self.root.minsize(660, 520)
        self._build()
        self._prefill()
        self.root.after(100, self._pump)

    # ------------------------------------------------------------ 界面
    def _build(self):
        pad = {"padx": 12, "pady": 6}
        wrap = ttk.Frame(self.root)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="课程链接（可一次粘多条，一行一个）",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", **pad)

        self.txt_url = tk.Text(wrap, height=4, wrap="char",
                               font=("Consolas", 9))
        self.txt_url.pack(fill="x", padx=12)

        row = ttk.Frame(wrap)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="保存到").pack(side="left")
        self.var_dir = tk.StringVar(value=self.last_out_dir)
        ttk.Entry(row, textvariable=self.var_dir).pack(
            side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="浏览…", command=self._pick_dir).pack(side="left")

        crow = ttk.Frame(wrap)
        crow.pack(fill="x", padx=12, pady=(0, 2))
        ttk.Label(crow, text="登录状态").pack(side="left")
        self.var_ck = tk.StringVar(value="")
        ttk.Label(crow, textvariable=self.var_ck, foreground="#555").pack(
            side="left", padx=8)
        ttk.Button(crow, text="导入 cookies…",
                   command=self._import_cookies).pack(side="right")

        bar = ttk.Frame(wrap)
        bar.pack(fill="x", **pad)
        self.btn_go = ttk.Button(bar, text="开始下载", command=self._start)
        self.btn_go.pack(side="left")
        ttk.Button(bar, text="打开文件夹", command=self._open_dir).pack(
            side="left", padx=6)
        self.btn_stop = ttk.Button(bar, text="停止", command=self._stop,
                                   state="disabled")
        self.btn_stop.pack(side="left")

        self.prog = ttk.Progressbar(wrap, mode="determinate", maximum=100)
        self.prog.pack(fill="x", padx=12, pady=(4, 0))
        self.var_status = tk.StringVar(value="待命 —— 粘贴链接后点「开始下载」")
        ttk.Label(wrap, textvariable=self.var_status,
                  foreground="#555").pack(anchor="w", padx=12, pady=(2, 6))

        ttk.Label(wrap, text="运行日志", font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=12)
        box = ttk.Frame(wrap)
        box.pack(fill="both", expand=True, padx=12, pady=(2, 12))
        self.log = tk.Text(box, wrap="word", state="disabled",
                           bg="#1e1e1e", fg="#d4d4d4",
                           font=("Consolas", 9), insertbackground="#d4d4d4")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def _refresh_cookie_label(self):
        try:
            path = download.resolve_cookies(None)
        except SystemExit:
            path = None
        n, exp = cookie_status(path)
        if n is None:
            self.var_ck.set("⚠ 没有 cookies.txt —— 点右边按钮导入一份")
        else:
            s = f"已加载 {n} 条" + (f"，{exp}" if exp else "")
            self.var_ck.set(s)

    def _import_cookies(self):
        f = filedialog.askopenfilename(
            title="选择导出的 cookies.txt（或 cookies.json）",
            filetypes=[("cookies 文件", "*.txt *.json"), ("所有文件", "*.*")])
        if not f:
            return
        dest = os.path.join(download.app_dir(), "cookies.txt")
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if f.lower().endswith(".json"):
                data = json.load(open(f, encoding="utf-8"))
                lines = ["# Netscape HTTP Cookie File"]
                for c in data:
                    dom = c.get("domain", "")
                    lines.append("\t".join([
                        dom,
                        "TRUE" if dom.startswith(".") else "FALSE",
                        c.get("path", "/"),
                        "TRUE" if c.get("secure") else "FALSE",
                        str(int(c.get("expirationDate") or c.get("expires") or 0)),
                        c.get("name", ""), c.get("value", ""),
                    ]))
                with open(dest, "w", encoding="utf-8", newline="\n") as o:
                    o.write("\n".join(lines) + "\n")
            else:
                import shutil
                shutil.copyfile(f, dest)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"导入失败：\n{e}")
            return
        self._refresh_cookie_label()
        messagebox.showinfo(APP_TITLE, "cookies 已导入，可以开始下载了。")

    def _prefill(self):
        self._refresh_cookie_label()
        u = clipboard_xiaoe_url(self.root)
        if u:
            self.txt_url.insert("1.0", u)
            self._set_status("已从剪贴板填入链接 —— 点「开始下载」即可")

    # ------------------------------------------------------------ 小工具
    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_dir.get() or "D:\\")
        if d:
            self.var_dir.set(os.path.normpath(d))

    def _open_dir(self):
        d = self.var_dir.get()
        if not os.path.isdir(d):
            messagebox.showwarning(APP_TITLE, f"目录不存在：\n{d}")
            return
        try:
            os.startfile(d)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def _set_status(self, s):
        self.var_status.set(s)

    def _append(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ 运行
    def _start(self):
        urls = [u for u in (self.txt_url.get("1.0", "end") or "").split("\n")
                if u.strip()]
        urls = [URL_RE.search(u).group(0) if URL_RE.search(u) else u.strip()
                for u in urls]
        urls = [u for u in urls if u]
        if not urls:
            messagebox.showwarning(APP_TITLE, "请先粘贴课程链接")
            return

        out_dir = self.var_dir.get().strip() or DEFAULT_DIR
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception:
                messagebox.showerror(APP_TITLE, f"无法创建目录：\n{out_dir}")
                return

        self.cfg["out_dir"] = out_dir
        save_config(self.cfg)

        self.total_urls = len(urls)
        self.done_urls = 0
        self.prog.configure(value=0)
        self.btn_go.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._set_status(f"开始处理 {len(urls)} 个课程 …")

        self.worker = threading.Thread(target=self._run, args=(urls, out_dir),
                                       daemon=True)
        self.worker.start()

    def _stop(self):
        self._append("")
        self._append("⚠ 已请求停止：当前课程的分片下完后会中断（已下载的会保留，可续传）")
        self._set_status("正在停止 …")
        self.btn_stop.configure(state="disabled")

    def _run(self, urls, out_dir):
        """工作线程：把 download 的 print 接到队列里。"""
        try:
            try:
                cookie_path = download.resolve_cookies(None)
            except SystemExit as e:
                self.q.put(("fatal", str(e)))
                return
            cookie, _ = download.load_cookies(cookie_path)
            args = SimpleNamespace(
                out=None, dir=out_dir, line=None, workers=24,
                keep_ts=False, force=False, test=0, cookies=cookie_path,
            )
            with contextlib.redirect_stdout(self._Writer()):
                print(f"cookie 文件：{cookie_path}")
                ff = download.find_ffmpeg()
                print(f"ffmpeg：{ff}")
                ok = 0
                for i, u in enumerate(urls, 1):
                    a, b = download.parse_course_url(u)
                    try:
                        if download.run_one(u, a, b, cookie, args, i, len(urls)):
                            ok += 1
                    except Exception:
                        print(f"[{i}/{len(urls)}] [!] 异常：")
                        print(traceback.format_exc())
                    self.q.put(("done_one", (i, len(urls), ok)))
                self.q.put(("finish", (ok, len(urls), out_dir)))
        except Exception:
            self.q.put(("fatal", traceback.format_exc()))

    class _Writer:
        """行缓冲的 stdout -> 队列。"""
        def __init__(self):
            self.buf = []

        def write(self, s):
            if not s:
                return
            self.buf.append(s)
            while "\n" in "".join(self.buf):
                all_ = "".join(self.buf)
                line, rest = all_.split("\n", 1)
                app_q.put(("log", line))
                self.buf = [rest]

        def flush(self):
            if self.buf:
                app_q.put(("log", "".join(self.buf)))
                self.buf = []

    # ------------------------------------------------------------ 队列泵
    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                    m = PCT_RE.search(payload)
                    if m:
                        self.prog.configure(value=int(m.group(3)))
                        self._set_status(
                            f"下载分片 {m.group(1)}/{m.group(2)}"
                            f"（{m.group(3)}%）"
                            + (f"　·　第 {self.done_urls+1}/{self.total_urls} 个课程"
                               if self.total_urls > 1 else ""))
                    elif "✅" in payload:
                        self.prog.configure(value=100)
                        self._set_status(payload.strip().lstrip("✅").strip())
                    elif "分片" in payload and "个" in payload:
                        self._set_status(payload.strip())
                elif kind == "done_one":
                    i, n, ok = payload
                    self.done_urls = i
                elif kind == "finish":
                    ok, n, out_dir = payload
                    self.btn_go.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self.prog.configure(value=100 if ok == n else self.prog["value"])
                    self._set_status(f"全部结束：成功 {ok}/{n}")
                    self._refresh_cookie_label()
                    if ok == n:
                        messagebox.showinfo(
                            APP_TITLE, f"成功 {ok}/{n} 个课程。\n\n文件在：{out_dir}")
                    else:
                        msg = (f"成功 {ok}/{n} 个课程。\n\n文件在：{out_dir}\n\n"
                               f"失败的请看下面的日志。\n"
                               f"如果日志里出现 11302，说明登录态过期了，"
                               f"需要重新导出一份 cookies.txt。")
                        messagebox.showwarning(APP_TITLE, msg)
                elif kind == "fatal":
                    self.btn_go.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self._set_status("出错了，详见日志")
                    self._append(payload)
                    messagebox.showerror(APP_TITLE, "运行出错，详见日志窗口。")
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def run(self):
        self.root.mainloop()


# _Writer 是内部类，需要拿到 App 实例的队列；用全局引用最简单
app_q = None


def main_gui():
    global app_q
    app = App()
    app_q = app.q
    app.run()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 带参数启动 -> 命令行模式。打包成 --windowed 后没有控制台，
        # 所以结果同时写一份日志文件，方便排查。
        log_path = os.path.join(os.environ.get("TEMP", "."), "xiaoe-cli.log")
        with open(log_path, "w", encoding="utf-8") as lf:
            with contextlib.redirect_stdout(lf), contextlib.redirect_stderr(lf):
                try:
                    download.main()
                except SystemExit:
                    pass
                except Exception:
                    traceback.print_exc()
        print(f"日志：{log_path}")
    else:
        main_gui()
