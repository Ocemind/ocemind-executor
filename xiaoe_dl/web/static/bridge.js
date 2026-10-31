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
    updateBtn.hidden = false;
    updateBtn.addEventListener("click", function () {
      updateBtn.disabled = true;
      updateLabel.textContent = "检查中…";
      invoke("check_update").then(function (version) {
        if (!version) {
          updateLabel.textContent = "已是最新";
          updateBtn.disabled = false;
          return;
        }
        if (!window.confirm("发现新版本 " + version + "，现在下载并安装？")) {
          updateLabel.textContent = "检查更新";
          updateBtn.disabled = false;
          return;
        }
        updateLabel.textContent = "下载中…";
        return invoke("install_update").then(function () {
          updateLabel.textContent = "正在重启…";
        });
      }).catch(function (err) {
        updateLabel.textContent = "检查更新";
        updateBtn.disabled = false;
        window.alert("检查更新失败：" + (err && err.message ? err.message : err));
      });
    });
  }
})();
