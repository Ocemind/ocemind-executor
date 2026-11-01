# -*- coding: utf-8 -*-
"""路径与外部依赖定位。

单独成模块是为了让 cookies / auth / core 都能用这些函数而不产生循环导入。
"""
import glob
import os
import shutil
import sys


def is_frozen():
    """是不是跑在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """配置 / cookie 的规范存放位置（跟随用户漫游，换机器能带走配置）。

    `OCEMIND_HOME` 可以把整个数据目录挪走，测试和便携场景靠它。
    """
    override = os.environ.get("OCEMIND_HOME")
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ocemind")


def local_app_dir():
    """本地数据目录。放浏览器 profile 这种大体积、不该被漫游同步的东西。

    profile 里有几十 MB 的缓存，放进 APPDATA 会被域漫游反复同步，
    既慢又容易在别的机器上因为路径对不上而失效（ABE 密钥是绑路径的）。

    便携版（exe 旁边有 portable.ini）用 `OCEMIND_LOCAL_HOME` 把
    profile 也留在 U 盘 / 解压目录里，不写用户目录。
    """
    override = os.environ.get("OCEMIND_LOCAL_HOME")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ocemind")


def profile_dir():
    """自动登录用的浏览器 profile 目录（见 auth.py）。"""
    return os.path.join(local_app_dir(), "browser_profile")


def desktop_dir():
    """真正的桌面目录。找不到返回 None（有些环境没有桌面）。

    不能想当然用 `%USERPROFILE%\\Desktop`：这台机器的桌面被重定向到了
    D:\\桌面，展开 user profile 会指向一个并不存在的目录。
    """
    cands = [
        os.path.join(os.path.expanduser("~"), "Desktop"),
        r"D:\桌面",
        r"C:\桌面",
    ]
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def project_root():
    """源码运行时的项目根（包目录的上一层）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """便携资源（cookies.txt 之类）该去哪儿找。

    - 打包后：exe 所在目录（有人喜欢把 cookies.txt 丢在 exe 旁边）
    - 源码运行：项目根目录

    注意不能简单用 `dirname(__file__)`：包化之后那会指向 ocemind/，
    便携 cookie 的语义就从「exe 旁边」漂移成了「包目录里」。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return project_root()


def cookie_candidates():
    """按优先级列出 cookie 文件位置。"""
    here = resource_dir()
    return [
        os.path.join(app_dir(), "cookies.txt"),   # 规范位置
        os.path.join(here, "cookies.txt"),        # 便携：放 exe / 项目根旁边
        os.path.join(app_dir(), "cookies_session.txt"),
        r"D:\视频\cookies.txt",                    # 老位置，兼容
        r"D:\视频\cookies_session.txt",
    ]


_FFMPEG_CACHE = []


def reset_ffmpeg_cache():
    """设置里改了 ffmpeg 路径之后必须调一次，否则还是用启动时找到的那个。"""
    _FFMPEG_CACHE.clear()


def find_ffmpeg(explicit=None):
    """优先显式路径，其次自己托管的那个，再 PATH，最后常见安装位置。

    托管目录（`tools/ffmpeg/`）排在 PATH 前面：那是应用按需下下来的最新版，
    比机器上三年前装的更可能是能用的那个。都找不到就退回 'ffmpeg' 让
    ffmpeg 自己报错（返回 None 会让调用方更早炸，但错误信息更难懂）。
    """
    if explicit:
        return explicit
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE[0]
    from .toolchain import managed_path

    managed = managed_path("ffmpeg")
    cand = str(managed) if managed else shutil.which("ffmpeg")
    if not cand:
        pats = [r"D:\Program Files\ffmpeg*\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg*\bin\ffmpeg.exe",
                r"D:\ffmpeg*\bin\ffmpeg.exe",
                r"C:\ffmpeg*\bin\ffmpeg.exe"]
        for p in pats:
            hits = sorted(glob.glob(p))
            if hits:
                cand = hits[-1]
                break
    _FFMPEG_CACHE.append(cand or "ffmpeg")
    return _FFMPEG_CACHE[0]


def ffmpeg_exe():
    """返回可执行文件路径；找不到返回空串。

    和 find_ffmpeg 的区别：那个为了兼容老调用会退回字符串 "ffmpeg"，
    新代码里需要判断"到底有没有"，所以单独给一个诚实的版本。
    """
    cand = find_ffmpeg()
    if cand and cand != "ffmpeg":
        return cand
    return shutil.which("ffmpeg") or ""


_CHROME_CACHE = []


def find_chrome():
    """系统 Chrome 的可执行路径。执行器的账号浏览器要用它。"""
    if _CHROME_CACHE:
        return _CHROME_CACHE[0]
    ok = lambda p: p and os.path.isfile(p)  # noqa: E731
    cands = [
        os.environ.get("OCEMIND_CHROME", ""),
        shutil.which("google-chrome") or "",
        shutil.which("chrome") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/opt/google/chrome/chrome",
    ]
    found = next((c for c in cands if ok(c)), "")
    _CHROME_CACHE.append(found)
    return found


def reset_chrome_cache():
    _CHROME_CACHE.clear()
