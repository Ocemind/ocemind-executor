"""发布引擎：用真实 Chrome 打开创作后台，把文件交给页面。

两步走，和规格里一致：

1. 准备（auto）：打开后台 → 传文件等处理完 → 填标题/正文/话题 → 校验标准
2. 确认（assisted）：停在预览页，人点"发布"；或者由调用方显式要求提交

发布完必须回读：回到内容管理页确认真实存在，才写 verified。平台会转码失败、
会进审核、会弹二次确认——"点了按钮"不等于"发出去了"。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..engines.base import Engine, fail, ok
from . import adapters, standards, states


def snapshot_hash(files: list[str], meta: dict) -> str:
    """发布批准绑定的是这个哈希：文件变了或标题改了，旧批准自动失效。"""
    h = hashlib.sha256()
    for path in files:
        p = Path(path)
        try:
            stat = p.stat()
            h.update(f"{p.name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        except OSError:
            h.update(f"{path}:missing".encode())
    h.update(json.dumps(meta, ensure_ascii=False, sort_keys=True).encode())
    return h.hexdigest()[:16]


def _wait_selector(sess, selector: str, timeout: float = 30) -> bool:
    end = time.time() + timeout
    js = f"!!document.querySelector({json.dumps(selector)})"
    while time.time() < end:
        try:
            if sess.evaluate(js):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def _fill(sess, selector: str, value: str) -> bool:
    """同时兼容原生 input 与 contenteditable。React 受控组件要靠原生 setter 才认。"""
    js = """
    (function(sel, val){
      const el = document.querySelector(sel);
      if (!el) return false;
      el.focus();
      if (el.isContentEditable) {
        el.textContent = val;
        el.dispatchEvent(new InputEvent('input', {bubbles: true}));
        return true;
      }
      const desc = Object.getOwnPropertyDescriptor(el.__proto__, 'value');
      if (desc && desc.set) desc.set.call(el, val); else el.value = val;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    })(%s, %s)
    """ % (json.dumps(selector), json.dumps(value))
    return bool(sess.evaluate(js))


class PublishBrowser(Engine):
    id = "publish.browser"
    kinds = ("publish",)

    def __init__(self, pool=None) -> None:
        self.pool = pool
        self._session = None

    def stop(self) -> None:
        # 浏览器进程不因为一次任务取消就关掉——里面还有人的登录态
        return

    # ------------------------------------------------------------------

    def run(self, ctx) -> dict:
        if self.pool is None:
            return fail("账号浏览器没有启动")
        account = ctx.job.get("_account") or {}
        if not account:
            return fail("没有指定账号")

        adapter = adapters.get(account.get("platform", ""))
        if adapter is None:
            return fail(f"这个平台还不支持发布：{account.get('platform')}")

        files = [str(f) for f in (ctx.inputs.get("files") or [])]
        if not files:
            return fail("没有要发布的文件")
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            return fail(f"文件不存在：{missing[0]}")

        meta = dict(ctx.params.get("meta") or {})
        title = str(meta.get("title") or Path(files[0]).stem)
        meta["title"] = title

        def mark(state: str) -> None:
            """把结果状态留在任务上：任务会落盘，重启后还认得出来。"""
            ctx.job.setdefault("meta", {})["publish_state"] = state

        # 复查：只回读内容管理页，不重传、不重发。审核中的记录靠这个收尾
        if ctx.params.get("verify_only"):
            ctx.progress(None, "回读确认")
            sess = self.pool.get(account)
            try:
                sess.ensure_started(adapter.get("manage_url") or adapter["publish_url"])
            except Exception as exc:  # noqa: BLE001
                return fail(f"浏览器启动失败：{exc}")
            if self._verify(sess, adapter, title):
                mark(states.VERIFIED)
                ctx.progress(100.0, "完成")
                return ok(*files)
            mark(states.REVIEWING)
            return fail("内容管理页里还是没看到这条，可能仍在审核")

        problems = standards.check(meta, standards.load(ctx.config))
        if problems:
            return fail("不符合发布标准：" + "；".join(problems))

        self._session = self.pool.get(account)
        try:
            self._session.ensure_started(adapter["publish_url"])
        except Exception as exc:  # noqa: BLE001
            return fail(f"浏览器启动失败：{exc}")

        ctx.progress(None, "打开创作后台")
        sess = self._session
        sess.goto(adapter["publish_url"])
        time.sleep(1.5)

        if not _wait_selector(sess, adapter["file_input"], timeout=25):
            return fail("页面结构和预期不一致：找不到上传控件。"
                        "可能是平台改版，或这个账号还没开通创作权限")

        ctx.progress(None, "上传文件")
        for path in files:
            try:
                sess.set_file_input(path, adapter["file_input"])
            except Exception as exc:  # noqa: BLE001
                return fail(f"上传控件操作失败：{exc}")
            time.sleep(2.0)

        ctx.progress(None, "等待平台处理")
        time.sleep(max(3.0, float(ctx.params.get("upload_wait") or 6)))

        if adapter.get("title_input"):
            _fill(sess, adapter["title_input"], title)
        desc = _compose_desc(meta)
        if adapter.get("desc_input") and desc:
            _fill(sess, adapter["desc_input"], desc)

        submitted = False
        if ctx.params.get("submit"):
            if not adapter.get("submit"):
                mark(states.MANUAL)
                return fail("这个平台的发布按钮没有配置选择器，内容已填好，需要人工点发布")
            ctx.progress(None, "提交中")
            sess.evaluate(
                "(function(sel){const b=document.querySelector(sel);"
                "if(b){b.click();return true}return false})(%s)"
                % json.dumps(adapter["submit"]))
            submitted = True

        digest = snapshot_hash(files, meta)
        ctx.job.setdefault("meta", {})["snapshot_hash"] = digest

        if not submitted:
            # 停在预览页等人点 —— 内容都填好了，这不是失败
            mark(states.MANUAL)
            ctx.progress(100.0, "已停在预览页")
            return ok(*files)

        if ctx.params.get("verify", True):
            ctx.progress(None, "回读确认")
            if not self._verify(sess, adapter, title):
                # 点了提交但回读没看到：转码/审核都可能，别报失败催人重发
                mark(states.REVIEWING)
                return fail("提交了，但内容管理页里还没出现这条作品。"
                            "平台多半在转码或审核，过一会儿点「复查」确认")

        mark(states.VERIFIED)
        ctx.progress(100.0, "完成")
        return ok(*files)

    def _verify(self, sess, adapter: dict, title: str) -> bool:
        """回到内容管理页，确认标题真的出现了。"""
        manage = adapter.get("manage_url") or ""
        if not manage:
            return False
        try:
            sess.goto(manage)
            time.sleep(2.0)
            sess.evaluate(
                "window.__ocm_title = %s;" % json.dumps(title))
            end = time.time() + 20
            while time.time() < end:
                if sess.evaluate(adapter["verify"]):
                    return True
                time.sleep(1.5)
        except Exception:  # noqa: BLE001
            return False
        return False


def _compose_desc(meta: dict) -> str:
    desc = str(meta.get("desc") or "").strip()
    tags = meta.get("hashtags") or []
    if isinstance(tags, str):
        import re

        tags = [t for t in re.split(r"[,，\s#]+", tags) if t]
    if tags:
        desc = (desc + "\n\n" + " ".join("#" + t for t in tags)).strip()
    return desc
