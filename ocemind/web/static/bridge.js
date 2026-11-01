/* Tauri 桌面壳的桥接层。
 *
 * 界面本身还是同一套 HTML/CSS/JS：浏览器里（python run.py --web）这段脚本
 * 直接返回，什么都没改。只有在 Tauri 里（页面由 tauri.localhost 加载、
 * Rust 侧注入了 __OCEMIND_PORT__）才做三件事：
 *
 * 1. 把相对路径的 fetch 指到 127.0.0.1 上的 Python 服务；
 * 2. 用 Tauri 的文件对话框顶替 pywebview 的 JsApi；
 * 3. 外链交给系统浏览器打开，不在应用窗口里跳走。
 *
 * 必须在 app.js 之前加载。
 */
(function () {
  "use strict";

  var port = window.__OCEMIND_PORT__;
  var tauri = window.__TAURI__;
  if (!port || !tauri || !tauri.core) return;

  var base = "http://127.0.0.1:" + port;
  var invoke = tauri.core.invoke;
  window.__OCEMIND__ = { baseUrl: base, desktop: true, version: window.__OCEMIND_VERSION__ || "" };

  /* ── fetch：只改本机相对路径，外链和完整 URL 原样放行 ───────────── */
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    if (typeof input === "string" && input.charAt(0) === "/") {
      input = base + input;
    }
    return nativeFetch(input, init);
  };

  /* ── pywebview JsApi 的替身 ──────────────────────────────────────── */
  function call(name, args) {
    return invoke(name, args || {}).catch(function () { return ""; });
  }

  var api = {
    select_file: function (current) {
      return call("pick_file", { current: current || "" }).then(function (p) { return p || ""; });
    },
    select_folder: function (current) {
      return call("pick_folder", { current: current || "" }).then(function (p) { return p || ""; });
    },
    select_cookie_file: function (current) {
      return call("pick_cookie_file", { current: current || "" }).then(function (p) { return p || ""; });
    },
    open_path: function (path) {
      return call("reveal_path", { path: path || "" }).then(function (ok) { return !!ok; });
    },
  };
  window.pywebview = window.pywebview || {};
  window.pywebview.api = api;

  /* ── 外链：交给系统默认浏览器 ───────────────────────────────────── */
  document.addEventListener("click", function (event) {
    var node = event.target;
    while (node && node !== document && !(node.tagName === "A" && node.getAttribute("href"))) {
      node = node.parentNode;
    }
    if (!node || node === document) return;
    var href = node.getAttribute("href") || "";
    if (!/^https?:\/\//i.test(href)) return;
    event.preventDefault();
    call("open_url", { url: href });
  }, true);

  /* ── 检查更新（只在装了桌面壳时出现） ──────────────────────────── */
  /* 只有能自更新的形态才显示入口：Windows 安装版，和 Linux 的 AppImage。
     便携版没有安装流程，deb 装的版本也不在 updater 覆盖范围。 */
  var updateBtn = document.getElementById("btn-update");
  var updateLabel = document.getElementById("update-label");
  if (updateBtn && window.__OCEMIND_UPDATER__) {
    var pending = null;   // 查到的版本号；没有就是已是最新
    var state = "idle";   // idle | checking | found | installing | error | manual
    var errText = "";
    var failures = 0;     // 连续失败次数：第二次就不再假装网络能自己好
    var RELEASES = "https://github.com/Ocemind/ocemind-executor/releases/latest";

    updateBtn.hidden = false;

    /* 所有反馈都写在这颗按钮上：不给用户弹窗，弹窗被吞掉时就成了「点了没反应」。
       状态只有五种，每种都对应一句话，永远不停在「检查中…」。 */
    function setState(next, text, title) {
      state = next;
      updateLabel.textContent = text;
      updateLabel.style.fontWeight = next === "found" ? "600" : "";
      updateBtn.title = title || "";
      updateBtn.disabled = next === "checking" || next === "installing";
    }

    /* silent=true 是启动时的静默检查：查不到更新、或者网络不通都不打扰用户，
       只在真的有新版本时把「检查更新」换成版本号。 */
    function check(silent) {
      if (!silent) setState("checking", "检查中…");
      return invoke("check_update").then(function (version) {
        failures = 0;
        pending = version || null;
        if (pending) setState("found", "发现新版本 v" + pending + " · 点此安装");
        else if (!silent) setState("idle", "已是最新", "当前 v" + (window.__OCEMIND_VERSION__ || ""));
        else setState("idle", "检查更新");
      }).catch(function (err) {
        pending = null;
        failures += 1;
        errText = err && err.message ? err.message : String(err);
        if (silent) {                       // 启动时网络不通很常见，留个重试入口就行
          setState("idle", "检查更新", errText);
          return;
        }
        /* GitHub 在有些网络里就是连不上，重试多少次都一样。第二次失败就把
           按钮换成「打开发布页」——浏览器走系统代理，往往反而能打开。 */
        if (failures >= 2) {
          setState("manual", "连不上更新源 · 打开发布页", errText);
        } else {
          setState("error", "检查失败 · 点此重试", errText);
        }
      });
    }

    function install(version) {
      setState("installing", "下载中…");
      invoke("install_update").then(function () {
        setState("installing", "正在重启…");
      }).catch(function (err) {
        errText = err && err.message ? err.message : String(err);
        setState("error", "更新失败 · 点此重试", errText);
      });
    }

    updateBtn.addEventListener("click", function () {
      if (state === "checking" || state === "installing") return;
      if (state === "found" && pending) {
        install(pending);   // 按钮上写着「点此安装」，这一下就是确认
        return;
      }
      if (state === "manual") {
        call("open_url", { url: RELEASES });
        return;
      }
      check(false);
    });

    // 界面先渲染出来，再查更新，别拖慢启动。
    setTimeout(function () {
      check(true);
    }, 3000);
  }
})();
