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
    python tools/make_launcher.py                  # 建启动器（旧 exe 自动改名避让）
    python tools/make_launcher.py --no-rename      # 不动桌面上那个 exe
    python tools/make_launcher.py --name "小鹅通"   # 自定义快捷方式名
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

LAUNCHER_NAME = "小鹅通下载器"
FROZEN_NAME = "小鹅通下载器.exe"
FALLBACK_NAME = "小鹅通下载器（旧版备用）.exe"


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


def main():
    ap = argparse.ArgumentParser(description="在桌面建一个源码方式的小鹅通启动器")
    ap.add_argument("--name", default=LAUNCHER_NAME, help="快捷方式名（不含 .lnk）")
    ap.add_argument("--no-rename", action="store_true",
                    help="不重命名桌面上现有的 小鹅通下载器.exe")
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

    lnk = os.path.join(desktop, args.name + ".lnk")

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

    # 启动器图标借用旧 exe 的（那是用户认得的那个图标）。
    # 万一 exe 被删了，图标退化成 pythonw 的默认图标，但快捷方式照常能用。
    fallback_exe = os.path.join(desktop, FALLBACK_NAME)
    icon = None
    if not args.no_rename:
        rename_frozen(desktop)
    if os.path.exists(fallback_exe):
        icon = fallback_exe + ",0"

    try:
        make_shortcut(lnk, pythonw, "run.py", ROOT, icon=icon,
                      desc="小鹅通课程下载器（源码方式，支持自动登录）")
    except RuntimeError as e:
        print(f"[!] 建快捷方式失败：{e}")
        return 1

    print(f"[*] 启动器已建：{lnk}")
    print(f"    目标：{pythonw} run.py")
    print(f"    工作目录：{ROOT}")
    if icon:
        print(f"    图标：{fallback_exe}")
    print()
    print("    pythonw 是 GUI 子系统解释器，双击不会闪控制台窗口。")
    print("    它比 exe 多支持自动登录（exe 里没打包 playwright）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
