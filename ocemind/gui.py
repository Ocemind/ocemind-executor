# -*- coding: utf-8 -*-
"""
课程视频下载器 —— 图形界面

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

from ocemind import auth, core
from ocemind.auth import AuthError
from ocemind.cookies import cookie_status, resolve_cookies
from ocemind.paths import app_dir, find_ffmpeg, profile_dir

APP_TITLE = "Ocemind 下载器"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          "ocemind")
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


def clipboard_ocemind_url(root):
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
        # exe 里没有 playwright，这个按钮会走降级分支给出说明，而不是消失
        self.btn_login = ttk.Button(crow, text="登录一次",
                                    command=self._login_once)
        self.btn_login.pack(side="right", padx=6)

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
        """报告当前走的是哪条路 —— 用户要知道的是这个，不是孤零零一个条数。"""
        try:
            path = resolve_cookies(None)
        except SystemExit:
            path = None
        n, exp = cookie_status(path)

        bits = []
        if n is None:
            bits.append("⚠ 没有 cookies.txt")
        else:
            bits.append(f"已加载 {n} 条" + (f"，{exp}" if exp else ""))
        if auth.playwright_available():
            # profile 在不在，决定自动登录是「立刻可用」还是「得先登一次」，
            # 这是用户看一眼就该知道的事
            bits.append("可以自动登录（已登录过）" if os.path.isdir(profile_dir())
                        else "可以自动登录（还没登录过）")
            self.btn_login.configure(state="normal")
        else:
            # exe 场景。按钮留着但变灰，比直接消失更好懂 —— 用户能看出是
            # 「这个版本没有」而不是「这个功能不存在」。
            bits.append("本版本不含自动登录（exe 为体积排除了 playwright）")
            self.btn_login.configure(state="disabled")
        self.var_ck.set("　·　".join(bits))

    def _import_cookies(self):
        f = filedialog.askopenfilename(
            title="选择导出的 cookies.txt（或 cookies.json）",
            filetypes=[("cookies 文件", "*.txt *.json"), ("所有文件", "*.*")])
        if not f:
            return
        dest = os.path.join(app_dir(), "cookies.txt")
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

    # ------------------------------------------------------------ 登录
    def _target_from_input(self):
        """从链接框里抠出 `(app_id, alive_id)`，取不到就是 `(None, None)`。

        有 alive_id 就能真调接口验证，这一点很重要：登录**之前**站点也会下发
        一个匿名 ko_token，光看它出现会误判成「已登录」。
        """
        txt = self.txt_url.get("1.0", "end").strip()
        if not txt:
            return None, None
        cands = URL_RE.findall(txt) or [txt.splitlines()[0]]
        for u in cands:
            a, b = core.parse_course_url(u)
            if a:
                return a, b
        return None, None

    def _login_once(self):
        """弹浏览器让用户登一次。

        **不假定登录方式**：扫码、手机验证码、密码都行。窗口停在登录页，
        用户用自己习惯的方式登进去，程序只负责把会话收下来。
        """
        if not auth.playwright_available():
            messagebox.showinfo(
                APP_TITLE,
                "这个版本不含自动登录。\n\n"
                "exe 为了控制体积把 playwright 排除掉了。两个办法：\n"
                "  1. 用「导入 cookies…」导入一份导出的 cookies.txt\n"
                "  2. 用源码方式跑（桌面的「Ocemind 内容执行器」启动器），那边支持")
            return

        app_id, alive_id = self._target_from_input()
        if not app_id:
            messagebox.showwarning(
                APP_TITLE, "先把课程链接粘进上面的输入框——\n"
                           "我需要从链接里取出 app_id 才知道是哪个店铺。")
            return

        self.btn_login.configure(state="disabled")
        self._set_status("正在打开浏览器，请在里面完成登录 …")
        threading.Thread(target=self._login_worker, args=(app_id, alive_id),
                         daemon=True).start()

    def _login_worker(self, app_id, alive_id):
        try:
            verifier = auth.api_verifier(app_id, alive_id) if alive_id else None
            cks = auth.login_interactive(
                app_id, verifier=verifier,
                on_status=lambda m: app_q.put(("log", f"[登录] {m}")))
            if not cks:
                app_q.put(("log", "[登录] 没拿到登录态，已放弃。"))
                app_q.put(("login_done", False))
                return
            dest = auth.save_cookies(cks)
            app_q.put(("log", f"[登录] 登录态已保存到 {dest}"))
            app_q.put(("login_done", True))
        except AuthError as e:
            app_q.put(("log", f"[登录] {e}"))
            app_q.put(("login_done", False))
        except Exception:
            app_q.put(("log", "[登录] 出异常了：\n" + traceback.format_exc()))
            app_q.put(("login_done", False))

    def _prefill(self):
        self._refresh_cookie_label()
        u = clipboard_ocemind_url(self.root)
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
        """工作线程：把 core 的 print 接到队列里。"""
        try:
            # 先定下 app_id：登录态要按 app 挑，开浏览器登录也得知道是哪个店铺
            app_id = alive_id = None
            for u in urls:
                a, b = core.parse_course_url(u)
                if a:
                    app_id, alive_id = a, b
                    break

            try:
                cookie_path, cookies = auth.get_cookie(
                    app_id=app_id,
                    interactive_ok=True,        # GUI 可以弹浏览器让用户登
                    verifier=(auth.api_verifier(app_id, alive_id)
                              if alive_id else None),
                    on_status=lambda m: app_q.put(("log", m)))
            except AuthError as e:
                self.q.put(("fatal", str(e)))
                return
            args = SimpleNamespace(
                out=None, dir=out_dir, line=None, workers=24,
                keep_ts=False, force=False, test=0, cookies=cookie_path,
            )
            with contextlib.redirect_stdout(self._Writer()):
                print(f"cookie 文件：{cookie_path}")
                ff = find_ffmpeg()
                print(f"ffmpeg：{ff}")
                ok = 0
                for i, u in enumerate(urls, 1):
                    a, b = core.parse_course_url(u)
                    try:
                        if core.run_one(u, a, b, cookies, args, i, len(urls)):
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
                elif kind == "login_done":
                    ok = payload
                    self.btn_login.configure(
                        state="normal" if auth.playwright_available() else "disabled")
                    self._refresh_cookie_label()
                    self._set_status("登录完成" if ok else "登录没成功，详见日志")
                    if ok:
                        messagebox.showinfo(APP_TITLE, "登录成功，可以开始下载了。")
                    else:
                        messagebox.showwarning(
                            APP_TITLE,
                            "没拿到登录态。请看看日志里的说明。\n\n"
                            "常见原因：窗口关得太早，或者登录其实没完成。")
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
