# -*- coding: utf-8 -*-
"""在桌面放一个「双击即用」的启动器 —— 走源码，不走 exe。

为什么不是 exe
--------------
这台机器开着**智能应用控制（Smart App Control，强制模式）**。它按微软
ISG 信誉放行可执行文件：老 exe 攒够了信誉所以能跑，但**每重新打包一次就是
一份新哈希，就得重新攒一次信誉**，等于每次重建都可能被拦死。

实测：Phase 4 重建出来的 exe 退出码 126，CodeIntegrity 事件 3077 写明
「did not meet the Enterprise signing level requirements」，策略 ID
{0283ac0f-fff1-49ae-ada1-8a933130cad6}。而同一个策略下旧 exe 照跑不误。

启动器指向 pythonw.exe 去跑 run.py —— 那是 Python 官方的签名二进制，本身
就在信誉库里，不受这条策略影响。

顺带还比 exe 多一样东西：**自动登录**。exe 为了控制体积把 playwright 排除
掉了，只有源码方式才有（见 README「登录态」一节）。

用法
----
    python tools/make_launcher.py                  # 建「Ocemind 内容执行器」并清理旧快捷方式
    python tools/make_launcher.py --tk             # 另建一个 Tk 界面入口
    python tools/make_launcher.py --keep-old       # 不清理小鹅通/TDM Lite 的旧快捷方式
    python tools/make_launcher.py --name "下载器"   # 自定义快捷方式名
    python tools/make_launcher.py --remove         # 卸载启动器
"""
import argparse
import base64
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 开发工具在包外，直接 `python tools/make_launcher.py` 时 ROOT 不在 sys.path 上
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xiaoe_dl.paths import desktop_dir  # noqa: E402

LAUNCHER_NAME = "Ocemind 内容执行器"
TK_LAUNCHER_NAME = "Ocemind 内容执行器 (Tk)"
ICON = os.path.join(ROOT, "timi.ico")
#: 合并前的两个入口，建新快捷方式时顺手清掉，免得桌面上摆三个同功能的图标
#: 旧产品落在桌面上的真实文件名。只为迁移清理保留，不再用于界面、文档或新产物。
_LEGACY_NAME = "小鹅通下载器"
OLD_LAUNCHERS = (f"{_LEGACY_NAME}.lnk", "TDM Lite.lnk",
                 "Timi 下载器.lnk", "Timi 下载器 (Tk).lnk")
FROZEN_NAME = f"{_LEGACY_NAME}.exe"
FALLBACK_NAME = f"{_LEGACY_NAME}（旧版备用）.exe"


def find_pythonw():
    """找 pythonw.exe。

    用它的原因是它是 GUI 子系统的解释器——启动时**不闪控制台黑框**。
    不假定它在 PATH 里，优先取当前解释器的同名邻居。
    """
    exe = os.path.abspath(sys.executable)
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(cand):
        return cand
    return shutil.which("pythonw") or shutil.which("python")


def run_ps(script):
    """执行一段 PowerShell。

    用 -EncodedCommand（UTF-16LE + base64）而不是 -Command：路径里带中文和
    空格时，-Command 的引号转义很容易出错，编码方式则完全没有转义问题。
    """
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "PowerShell 失败").strip())


def make_shortcut(lnk, target, args, workdir, icon=None, desc=""):
    def q(s):
        return str(s).replace("'", "''")

    ps = [
        "$ErrorActionPreference = 'Stop'",
        "$sh = New-Object -ComObject WScript.Shell",
        f"$s = $sh.CreateShortcut('{q(lnk)}')",
        f"$s.TargetPath = '{q(target)}'",
        f"$s.Arguments = '{q(args)}'",
        f"$s.WorkingDirectory = '{q(workdir)}'",
        "$s.WindowStyle = 1",
        f"$s.Description = '{q(desc)}'",
    ]
    if icon:
        ps.append(f"$s.IconLocation = '{q(icon)}'")
    ps.append("$s.Save()")
    run_ps("\n".join(ps))


def rename_frozen(desktop, out=print):
    """把桌面那个打不开的 exe 改个名，免得和启动器重名、点错。

    只在「存在旧名、且新名还没有」时动手 —— 这样重复运行是幂等的，
    不会把用户改好的名字又覆盖掉。
    """
    src = os.path.join(desktop, FROZEN_NAME)
    dst = os.path.join(desktop, FALLBACK_NAME)
    if os.path.exists(src) and not os.path.exists(dst):
        os.rename(src, dst)
        out(f"[*] 旧 exe 改名避让：{FROZEN_NAME} → {FALLBACK_NAME}")
        return True
    return False


def remove_old_launchers(desktop, out=print):
    """删掉合并前的桌面入口。删不掉只提示，不影响新快捷方式。"""
    for name in OLD_LAUNCHERS:
        path = os.path.join(desktop, name)
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            out(f"[*] 已清理旧快捷方式：{name}")
        except OSError as exc:
            out(f"[!] 删不掉 {name}：{exc}")


def main():
    ap = argparse.ArgumentParser(
        description="在桌面建一个源码方式的 Ocemind 内容执行器启动器")
    ap.add_argument("--name", default="", help="快捷方式名（不含 .lnk）")
    ap.add_argument("--tk", action="store_true", help="建 Tk 界面入口（run.py --tk）")
    ap.add_argument("--keep-old", action="store_true",
                    help="不清理旧版（小鹅通 / TDM Lite / Timi 下载器）的快捷方式")
    ap.add_argument("--no-rename", action="store_true",
                    help="不重命名桌面上现有的旧版 exe")
    ap.add_argument("--remove", action="store_true", help="删除启动器")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    desktop = desktop_dir()
    if not desktop:
        print("[!] 没找到桌面目录，无法部署")
        return 1

    name = args.name or (TK_LAUNCHER_NAME if args.tk else LAUNCHER_NAME)
    lnk = os.path.join(desktop, name + ".lnk")

    if args.remove:
        if os.path.exists(lnk):
            os.remove(lnk)
            print(f"[*] 已删除 {lnk}")
        else:
            print("[*] 启动器本来就不在")
        return 0

    pythonw = find_pythonw()
    if not pythonw or not os.path.isfile(pythonw):
        print(f"[!] 找不到 pythonw.exe（当前解释器：{sys.executable}）")
        return 1

    entry = os.path.join(ROOT, "run.py")
    if not os.path.isfile(entry):
        print(f"[!] 找不到入口 {entry}")
        return 1

    icon = ICON + ",0" if os.path.isfile(ICON) else None
    if not icon:
        # 没有 timi.ico 时退回旧 exe 的图标，再不行就用 pythonw 的默认图标
        fallback_exe = os.path.join(desktop, FALLBACK_NAME)
        if not args.no_rename:
            rename_frozen(desktop)
        if os.path.exists(fallback_exe):
            icon = fallback_exe + ",0"

    run_args = "run.py --tk" if args.tk else "run.py"
    desc = ("Ocemind 内容执行器（Tk 备用界面）" if args.tk
            else "Ocemind 内容执行器：下载 · 转写 · 处理 · 发布")
    try:
        make_shortcut(lnk, pythonw, run_args, ROOT, icon=icon, desc=desc)
    except RuntimeError as e:
        print(f"[!] 建快捷方式失败：{e}")
        return 1

    if not args.keep_old:
        remove_old_launchers(desktop)

    print(f"[*] 启动器已建：{lnk}")
    print(f"    目标：{pythonw} {run_args}")
    print(f"    工作目录：{ROOT}")
    if icon:
        print(f"    图标：{icon}")
    print()
    print("    pythonw 是 GUI 子系统解释器，双击不会闪控制台窗口。")
    print("    默认打开网页壳；要看报错就用 run.bat，或加 --tk 用旧界面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
