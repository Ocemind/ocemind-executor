/* Ocemind 内容执行器 — 界面逻辑
   下载器原有的三条链路（加密下载 / 平台视频 / 直链）保持原样，
   执行器能力走 /api/exec，两边共用同一个任务列表。 */

const STATUS_TEXT = {
  queued: "排队中", running: "处理中", downloading: "下载中", merging: "合并中",
  paused: "已暂停", done: "已完成", error: "失败", cancelled: "已取消",
};

const CAP_META = {
  "download.platform": { glyph: "↓", label: "下载视频" },
  "transcribe.asr":    { glyph: "文", label: "转文字" },
  "media.transcode":   { glyph: "转", label: "转码/压缩" },
  "media.subtitle":    { glyph: "字", label: "字幕" },
  "media.audio":       { glyph: "音", label: "音频后期" },
  "media.thumbnail":   { glyph: "封", label: "抽帧封面" },
  "doc.parse":         { glyph: "档", label: "文档解析" },
  "image.ocr":         { glyph: "识", label: "图片 OCR" },
  "image.edit":        { glyph: "图", label: "图像处理" },
  "image.generate":    { glyph: "绘", label: "AI 生图" },
  "audio.tts":         { glyph: "声", label: "TTS 配音" },
  "audio.voiceclone":  { glyph: "仿", label: "声音克隆" },
  "publish.browser":   { glyph: "发", label: "发布" },
};

const CAP_KINDS = {
  "video": { glyph: "▶", label: "平台视频" },
  "course": { glyph: "锁", label: "加密下载" },
  "file": { glyph: "▤", label: "直链文件" },
};

const VIDEO_EXT = /\.(mp4|mov|mkv|avi|webm|flv|wmv|m4v|ts)$/i;
const AUDIO_EXT = /\.(mp3|wav|m4a|aac|flac|ogg|opus|wma)$/i;
const DOC_EXT = /\.(pdf|docx|xlsx|csv|json|jsonl|md|txt)$/i;
const IMG_EXT = /\.(png|jpg|jpeg|webp|bmp|gif|tiff)$/i;

const state = {
  tasks: [],
  accounts: [],
  platforms: [],
  caps: [],
  settings: {},
  filter: "all",
  view: "tasks",
  activeAccount: "",
  nodes: new Map(),
  probe: null,
  busy: false,
  probeTimer: null,
  probeSeq: 0,
  loginTimer: null,
  frameTimer: null,
  frameBusy: false,
  publishDraft: null,
  accountPlatform: "bilibili",
  connTab: "mcp",
  secureProbe: null,
  models: null,
  localInstallTimer: null,
  artifacts: [],
  artifactStatus: "all",
  artifactProject: "",
  feishu: null,
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const el = {
  url: $("#url-input"), capSelect: $("#cap-select"), add: $("#btn-add"),
  paste: $("#btn-paste"), browse: $("#btn-browse"), dir: $("#dir-input"),
  pickDir: $("#btn-dir"), quality: $("#quality-select"), conn: $("#conn-input"),
  srt: $("#srt-check"), mode: $("#mode-select"), extra: $("#extra-fields"),
  list: $("#task-list"), empty: $("#empty"), toast: $("#toast"),
  probeCard: $("#probe-card"), probeThumb: $("#probe-thumb"),
  probeTitle: $("#probe-title"), probeMeta: $("#probe-meta"),
  probeLogin: $("#probe-login"),
  settingsModal: $("#settings-modal"), accountModal: $("#account-modal"),
  publishModal: $("#publish-modal"),
  secureUrl: $("#secure-url"), secureResult: $("#secure-result"),
  secureDir: $("#secure-dir"), secureWorkers: $("#secure-workers"),
  secureLine: $("#secure-line"), secureKeepTs: $("#secure-keep-ts"),
  project: $("#project-input"), sequence: $("#sequence-input"),
  secureProject: $("#secure-project"), secureSequence: $("#secure-sequence"),
};

/* ───────────────────────── 基础工具 ───────────────────────── */

function fmtBytes(n) {
  if (!n || n < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}
function fmtSpeed(n) { return n > 0 ? `${fmtBytes(n)}/s` : ""; }
function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  if (isNaN(d)) return "";
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (d.toDateString() === now.toDateString()) return hm;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}
function fmtEta(sec) {
  if (!sec || sec <= 0 || !isFinite(sec)) return "";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  if (h) return `${h} 时 ${m} 分`;
  if (m) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}
function fmtDuration(sec) {
  if (!sec) return "";
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
function esc(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let toastTimer = null;
function toast(msg, isError = false) {
  el.toast.textContent = msg;
  el.toast.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.className = "toast"; }, 2800);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

const URL_IN_TEXT = /https?:\/\/[^\s<>"'`，。！？；：、（）【】《》「」“”‘’]+/i;
const BARE_IN_TEXT = /(?:[a-z0-9-]+\.)+(?:com|cn|net|org|tv|me|io|cc|xyz)\/[^\s<>"'`，。！？；：、（）【】《》「」“”‘’]*/i;

function isUrl(text) { return URL_IN_TEXT.test(text) || BARE_IN_TEXT.test(text); }

/* 抖音的「复制链接」是一整段分享文案，提交前把真正的 URL 摘出来 */
function extractUrl(text) {
  const raw = (text || "").trim();
  const m = raw.match(URL_IN_TEXT);
  if (m) return m[0].replace(/[，。,.!！?？、;；)）\]】》>"'`]+$/, "");
  return raw;
}

/* ───────────────────────── 初始化 ───────────────────────── */

async function init() {
  bindEvents();
  await loadSystem();
  await Promise.all([loadCaps(), loadPlatforms()]);
  renderExtra();
  await Promise.all([refresh(), refreshAccounts(), refreshUsage()]);
  setInterval(refresh, 800);
  setInterval(() => { refreshAccounts(); refreshUsage(); }, 15000);

  // 支持 #accounts / #connect 等直达，命令行和文档里贴链接方便
  // 项目和序列是「最近一次」的输入，换个链接重跑同一系列时不用重填
  el.project.value = localStorage.getItem("ocemind.project") || "";
  el.sequence.value = localStorage.getItem("ocemind.sequence") || "";

  const direct = (location.hash || "").replace("#", "");
  if (["tasks", "secure", "artifacts", "models", "accounts", "connect"].includes(direct)) {
    setView(direct);
  }
}

function currentProject() { return (el.project?.value || "").trim(); }
function currentSequence() { return (el.sequence?.value || "").trim(); }

function rememberProject() {
  localStorage.setItem("ocemind.project", currentProject());
  localStorage.setItem("ocemind.sequence", currentSequence());
}

async function loadSystem() {
  try {
    const sys = await api("/api/system");
    state.settings = sys.settings;
    el.dir.value = sys.settings.defaultDir || "";
    el.conn.value = sys.settings.defaultConnections || 8;
    el.quality.value = String(sys.settings.defaultQuality || 0);
    $("#tool-ytdlp").textContent = (sys.tools.ytdlp || "未找到").split(" ")[0];
    $("#tool-ytdlp").title = sys.tools.ytdlp_cmd || "";
    $("#tool-ffmpeg").textContent = sys.tools.ffmpeg || "未找到";
    $("#tool-ffmpeg").title = sys.tools.ffmpeg_dir || "";
    const chrome = sys.settings.chromePath || "";
    $("#tool-chrome").textContent = chrome ? "已配置" : "自动查找";
    el.secureDir.value = sys.settings.defaultDir || "";
    el.secureWorkers.value = sys.settings.courseWorkers || 24;
    el.secureLine.value = sys.settings.courseLine || "";
    el.secureKeepTs.checked = !!sys.settings.courseKeepTs;
  } catch (err) {
    toast("无法连接本地服务：" + err.message, true);
  }
}

async function loadCaps() {
  try {
    const data = await api("/api/exec/capabilities");
    state.caps = data.capabilities || [];
    el.capSelect.innerHTML =
      `<option value="auto">自动识别</option>` +
      state.caps.map((c) =>
        `<option value="${c.id}">${CAP_META[c.id]?.label || c.name}</option>`).join("");
    renderCapList();
  } catch (err) {
    el.capSelect.innerHTML = `<option value="auto">自动识别</option>`;
  }
}

async function loadPlatforms() {
  try {
    const data = await api("/api/exec/platforms");
    state.platforms = data.platforms || [];
  } catch (_) { state.platforms = []; }
}

async function refreshUsage() {
  try {
    const u = await api("/api/exec/usage");
    const limit = u.budget > 0;
    const text = limit
      ? `¥${u.spent.toFixed(2)} / ¥${u.budget.toFixed(0)}`
      : `¥${u.spent.toFixed(2)}`;
    $("#budget-text").textContent = text;
    $("#budget-pill").classList.toggle("over", limit && u.spent >= u.budget);
    const modelSpent = $("#model-spent");
    if (modelSpent) modelSpent.textContent = text;
    $("#model-budget-pill")?.classList.toggle("over", limit && u.spent >= u.budget);
  } catch (_) {}
}

/* ───────────────────────── 产出 / 飞书 ───────────────────────── */

const ARTIFACT_GLYPH = {
  video: "影", audio: "声", image: "图", doc: "档", text: "文", other: "•",
};
const SYNC_TEXT = {
  local: "仅本地", partial: "部分同步", synced: "已同步", failed: "同步失败",
};

async function refreshArtifacts() {
  const query = new URLSearchParams();
  if (state.artifactStatus !== "all") query.set("status", state.artifactStatus);
  if (state.artifactProject) query.set("project", state.artifactProject);
  try {
    const data = await api("/api/exec/artifacts?" + query.toString());
    state.artifacts = data.artifacts || [];
    renderProjectOptions(data.projects || []);
    renderArtifacts();
  } catch (err) {
    toast("读取产出失败：" + err.message, true);
  }
  refreshFeishu();
}

function renderProjectOptions(projects) {
  const select = $("#artifact-project");
  select.innerHTML = `<option value="">全部项目</option>` +
    projects.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  if (projects.includes(state.artifactProject)) {
    select.value = state.artifactProject;
  } else {
    state.artifactProject = "";
    select.value = "";
  }
  const list = $("#project-list");
  if (list) {
    list.innerHTML = projects
      .map((p) => `<option value="${esc(p)}"></option>`).join("");
  }
}

function renderArtifacts() {
  const box = $("#artifact-list");
  $("#artifact-empty").classList.toggle("hidden", state.artifacts.length > 0);
  box.innerHTML = state.artifacts.map((a) => {
    const status = a.status || "local";
    const capLabel = CAP_META[a.capability]?.label
      || CAP_KINDS[a.capability]?.label || a.capability;
    const projectLabel = [a.project || "未分类", a.sequence]
      .filter(Boolean).join(" · ");
    const meta = [
      fmtTime(a.created_at), projectLabel, capLabel,
      a.size ? fmtBytes(a.size) : "",
    ].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    const remote = a.remote_url
      ? `<a class="a-link" href="${esc(a.remote_url)}" target="_blank"
            rel="noopener">打开云文档</a>` : "";
    const error = a.error ? `<div class="a-sub error">${esc(a.error)}</div>` : "";
    return `<div class="artifact">
      <div class="a-glyph">${ARTIFACT_GLYPH[a.file_kind] || "•"}</div>
      <div class="a-main">
        <div class="a-meta">${meta}</div>
        <div class="a-title" title="${esc(a.title)}">${esc(a.title)}</div>
        <div class="a-sub" title="${esc(a.path)}">${esc(a.path)}</div>
        ${error}
      </div>
      <div class="a-side">
        <span class="a-chip ${esc(status)}">${SYNC_TEXT[status] || status}</span>
        ${remote}
        ${status === "synced" ? "" :
          `<button class="btn ghost small a-sync" data-id="${esc(a.id)}">同步</button>`}
      </div>
    </div>`;
  }).join("");
}

async function refreshFeishu(fill = true) {
  try {
    const info = await api("/api/exec/feishu");
    applyFeishu(info, fill);
  } catch (_) {}
}

function applyFeishu(info, fill) {
  state.feishu = info;
  if (fill) {
    $("#fs-cli").value = info.cli || "";
    $("#fs-base").value = info.baseToken || "";
    $("#fs-table").value = info.tableId || "";
    $("#fs-folder").value = info.folderToken || "";
    $("#fs-enabled").checked = !!info.enabled;
    $("#fs-docmode").checked = (info.docMode || "text") !== "off";
  }

  const chip = $("#fs-status");
  if (!info.installed) {
    chip.className = "status-chip warn";
    chip.textContent = "未安装 lark-cli";
  } else if (info.logged_in === false) {
    chip.className = "status-chip bad";
    chip.textContent = "lark-cli 未登录";
  } else if (info.logged_in) {
    chip.className = "status-chip ok";
    chip.textContent = "飞书已连接";
  } else {
    chip.className = "status-chip warn";
    chip.textContent = "登录态待确认";
  }

  const hint = $("#fs-hint");
  let text = info.message || "";
  if (!info.installed) {
    text = "先装 lark-cli 并登录一次：npm i -g @larksuite/cli，然后 lark-cli auth login --recommend";
  } else if (!info.enabled) {
    text = text || "自动同步已关闭；产出仍会登记在本地。";
  } else if (info.logged_in) {
    text = text || "任务完成后自动写索引；文字产出另建云文档。";
  }
  hint.textContent = text;
  hint.className = "fs-hint " + (info.installed && info.logged_in ? "ok" : "warn");

  $("#fs-fields").classList.remove("hidden");
  $("#fs-fields-text").textContent =
    "索引表需要这些列（「大小」用数字列，其余都用文本列）：" +
    (info.fields || []).join(" · ");
}

async function saveFeishu(notify = true) {
  const body = {
    feishuEnabled: $("#fs-enabled").checked,
    feishuCli: $("#fs-cli").value.trim(),
    feishuBaseToken: $("#fs-base").value.trim(),
    feishuTableId: $("#fs-table").value.trim(),
    feishuFolderToken: $("#fs-folder").value.trim(),
    feishuDocMode: $("#fs-docmode").checked ? "text" : "off",
  };
  const info = await api("/api/exec/feishu", {
    method: "POST", body: JSON.stringify(body),
  });
  applyFeishu(info, false);
  if (notify) toast("飞书设置已保存");
  return info;
}

async function testFeishu() {
  try {
    await saveFeishu(false);
    const info = await api("/api/exec/feishu/test", { method: "POST" });
    applyFeishu(info, false);
    const table = info.table || {};
    if (table.ok) {
      toast("连接正常，索引表字段齐全");
    } else {
      toast(table.error || ("索引表缺列：" + (table.missing || []).join("、")), true);
      $("#fs-fields-text").textContent =
        (table.error ? table.error + "。" : "") +
        "索引表需要这些列（「大小」用数字列，其余都用文本列）：" +
        (info.fields || []).join(" · ");
    }
  } catch (err) {
    toast("测试失败：" + err.message, true);
  }
}

async function syncArtifacts(payload) {
  try {
    toast("正在同步到飞书…");
    const res = await api("/api/exec/artifacts/sync", {
      method: "POST", body: JSON.stringify(payload),
    });
    const errors = res.errors || [];
    if (res.failed || errors.length) {
      toast(`已同步 ${res.synced} 条，失败 ${res.failed || 0} 条`, true);
      const hint = $("#fs-hint");
      hint.textContent = errors[0] || "同步失败：检查 lark-cli 登录态和索引表字段";
      hint.className = "fs-hint warn";
    } else {
      toast(`已同步 ${res.synced} 条`);
    }
    await refreshArtifacts();
  } catch (err) {
    toast("同步失败：" + err.message, true);
  }
}

async function backfillArtifacts() {
  try {
    const res = await api("/api/exec/artifacts/index", { method: "POST" });
    toast(`补登记 ${res.indexed} 条产出`);
    await refreshArtifacts();
  } catch (err) {
    toast("补登记失败：" + err.message, true);
  }
}

/* ───────────────────────── 事件 ───────────────────────── */

function bindEvents() {
  $$(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  $$("#task-filter button").forEach((btn) => {
    btn.addEventListener("click", () => setFilter(btn.dataset.filter));
  });

  el.add.addEventListener("click", addTask);
  el.url.addEventListener("keydown", (e) => { if (e.key === "Enter") addTask(); });
  el.url.addEventListener("input", () => { scheduleProbe(); scheduleDetect(); });
  el.capSelect.addEventListener("change", () => { renderExtra(); scheduleProbe(); });
  el.paste.addEventListener("click", pasteFromClipboard);
  el.browse.addEventListener("click", pickFile);
  el.pickDir.addEventListener("click", pickDirectory);
  el.probeLogin.addEventListener("click", startCourseLogin);
  el.list.addEventListener("click", onListClick);

  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-close-settings").addEventListener("click", () => el.settingsModal.classList.add("hidden"));
  $("#btn-save-settings").addEventListener("click", saveSettingsForm);
  $("#set-dir-pick").addEventListener("click", async () => {
    const picked = await pickFolder($("#set-dir").value);
    if (picked) $("#set-dir").value = picked;
  });
  el.settingsModal.addEventListener("click", (e) => {
    if (e.target === el.settingsModal) el.settingsModal.classList.add("hidden");
  });

  // ---- 加密下载 ----
  $("#secure-import").addEventListener("click", async () => {
    const path = await pickCookieFile();
    if (path) importCourseCookies({ path });
  });
  $("#secure-login").addEventListener("click", startCourseLogin);
  $("#secure-check").addEventListener("click", () => refreshCourseAuth(true));
  $("#secure-cookie-save").addEventListener("click", () => {
    const text = $("#secure-cookie-text").value.trim();
    if (!text) { toast("先粘贴 cookies 内容", true); return; }
    importCourseCookies({ text });
  });
  $("#secure-probe").addEventListener("click", probeSecure);
  $("#secure-add").addEventListener("click", addSecureDownload);
  el.secureUrl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); probeSecure(); }
  });
  $("#secure-dir-pick").addEventListener("click", async () => {
    const picked = await pickFolder(el.secureDir.value);
    if (picked) {
      el.secureDir.value = picked;
      await saveSettings({ defaultDir: picked });
      el.dir.value = picked;
    }
  });
  $("#secure-workers").addEventListener("change", () => {
    saveSettings({ courseWorkers: Number(el.secureWorkers.value) || 24 });
  });
  $("#secure-line").addEventListener("change", () => {
    saveSettings({ courseLine: el.secureLine.value.trim() });
  });
  el.secureKeepTs.addEventListener("change", () => {
    saveSettings({ courseKeepTs: el.secureKeepTs.checked });
  });

  // ---- 产出 / 飞书 ----
  $("#fs-save").addEventListener("click", () => saveFeishu(true));
  $("#fs-test").addEventListener("click", testFeishu);
  $("#artifact-filter").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-status]");
    if (!btn) return;
    state.artifactStatus = btn.dataset.status;
    $$("#artifact-filter button").forEach((b) => b.classList.toggle("on", b === btn));
    refreshArtifacts();
  });
  $("#artifact-project").addEventListener("change", (e) => {
    state.artifactProject = e.target.value;
    refreshArtifacts();
  });
  $("#artifact-sync").addEventListener("click", () => syncArtifacts({}));
  $("#artifact-index").addEventListener("click", backfillArtifacts);
  $("#artifact-list").addEventListener("click", (e) => {
    const btn = e.target.closest(".a-sync");
    if (btn) syncArtifacts({ ids: [btn.dataset.id] });
  });

  // ---- 模型与接口 ----
  $("#btn-save-budget").addEventListener("click", async () => {
    await saveSettings({ monthlyBudget: Number($("#set-budget").value) || 0 });
    toast("预算已保存");
    refreshUsage();
  });

  // ---- 设置里的跳转 ----
  $("#btn-goto-secure").addEventListener("click", () => {
    el.settingsModal.classList.add("hidden");
    setView("secure");
  });
  $("#btn-goto-models").addEventListener("click", () => {
    el.settingsModal.classList.add("hidden");
    setView("models");
  });

  $("#btn-add-account").addEventListener("click", openAccountModal);
  $("#btn-add-account-2").addEventListener("click", openAccountModal);
  $("#btn-close-account").addEventListener("click", () => el.accountModal.classList.add("hidden"));
  $("#btn-create-account").addEventListener("click", createAccount);
  el.accountModal.addEventListener("click", (e) => {
    if (e.target === el.accountModal) el.accountModal.classList.add("hidden");
  });

  $("#btn-browser-go").addEventListener("click", () => {
    const url = $("#browser-url").value.trim();
    if (state.activeAccount && url) accountAction({ kind: "goto", url });
  });
  $("#browser-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#btn-browser-go").click();
  });
  $("#btn-browser-back").addEventListener("click", async () => {
    if (!state.activeAccount) return;
    await api(`/api/exec/accounts/${state.activeAccount}/close`, { method: "POST" });
    $("#browser-frame").hidden = true;
    $("#browser-empty").hidden = false;
    stopFrames();
  });
  $("#btn-browser-check").addEventListener("click", checkAccount);
  $("#btn-type-send").addEventListener("click", () => {
    const text = $("#type-input").value;
    if (text) { accountAction({ kind: "type", text }); $("#type-input").value = ""; }
  });
  $("#type-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); $("#btn-type-send").click(); }
  });
  $("#btn-key-enter").addEventListener("click", () => accountAction({ kind: "key", key: "Enter" }));
  $("#btn-key-back").addEventListener("click", () => accountAction({ kind: "key", key: "Backspace" }));
  $("#browser-frame").addEventListener("click", onFrameClick);

  $("#btn-close-publish").addEventListener("click", () => el.publishModal.classList.add("hidden"));
  $("#btn-approve-publish").addEventListener("click", approvePublish);
  el.publishModal.addEventListener("click", (e) => {
    if (e.target === el.publishModal) el.publishModal.classList.add("hidden");
  });

  $$("#conn-tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.connTab = btn.dataset.tab;
      $$("#conn-tabs button").forEach((b) => b.classList.toggle("on", b === btn));
      renderConnCode();
    });
  });
  $("#btn-token").addEventListener("click", rotateToken);
  $("#btn-test").addEventListener("click", testConnection);
  $("#btn-copy-code").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#conn-code").textContent);
      toast("已复制");
    } catch (_) { toast("复制失败，请手动选中", true); }
  });
}

/* ───────────────────────── 视图切换 ───────────────────────── */

function setView(view) {
  state.view = view;
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${view}`));
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "accounts") { refreshAccounts(); startFrames(); }
  else stopFrames();
  if (view === "connect") { loadConnect(); renderConnCode(); }
  if (view === "secure") refreshCourseAuth();
  if (view === "artifacts") refreshArtifacts();
  if (view === "models") { loadModels(); refreshUsage(); }
}

function setFilter(filter) {
  state.filter = filter;
  $$("#task-filter button").forEach((b) => b.classList.toggle("on", b.dataset.filter === filter));
  render();
}

/* ───────────────────────── 输入框智能识别 ───────────────────────── */

let detectTimer = null;
function scheduleDetect() {
  clearTimeout(detectTimer);
  detectTimer = setTimeout(() => {
    const text = el.url.value.trim();
    if (!text || el.capSelect.value !== "auto") return;
    const cap = detectCapability(text);
    if (cap) {
      const label = CAP_META[cap]?.label;
      el.url.title = `识别为：${label}`;
    }
  }, 250);
}

function detectCapability(text) {
  if (isUrl(text)) return "download.platform";
  if (VIDEO_EXT.test(text) || AUDIO_EXT.test(text)) return "transcribe.asr";
  if (DOC_EXT.test(text)) return "doc.parse";
  if (IMG_EXT.test(text)) return "image.ocr";
  return "";
}

function currentCapability() {
  const chosen = el.capSelect.value;
  if (chosen !== "auto") return chosen;
  return detectCapability(el.url.value.trim());
}

/* ───────────────────────── 能力附加选项 ───────────────────────── */

const SELECT_OPTS = {
  "media.transcode": [
    ["mp4", "转成 MP4"], ["compress", "压缩体积"], ["audio", "只留音频"],
  ],
  "media.audio": [
    ["normalize", "响度归一"], ["denoise", "降噪"], ["speed", "变速"], ["extract", "提取音轨"],
  ],
  "media.subtitle": [
    ["burn", "把字幕烧进画面"], ["translate", "翻译字幕"], ["shift", "整体平移时间"],
  ],
  "image.edit": [
    ["resize", "改尺寸"], ["crop", "居中裁剪"], ["watermark", "加水印"], ["convert", "转格式"],
  ],
};

function renderExtra() {
  const cap = currentCapability();
  el.extra.innerHTML = "";
  el.extra.classList.add("hidden");

  const needExtra = ["media.transcode", "media.audio", "media.subtitle",
                     "media.thumbnail", "image.edit", "publish.browser",
                     "media.subtitle", "audio.voiceclone"].includes(cap);
  if (!needExtra) { toggleOpts(cap); return; }

  const rows = [];
  if (SELECT_OPTS[cap]) {
    rows.push(`<div class="field"><label>处理方式</label>
      <select id="x-mode">${SELECT_OPTS[cap]
        .map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></div>`);
  }
  if (cap === "media.subtitle") {
    rows.push(`<div class="field"><label>字幕文件路径</label>
      <input id="x-srt" type="text" placeholder="D:\\字幕.srt"></div>`);
  }
  if (cap === "media.transcode") {
    rows.push(`<div class="field"><label>压缩质量 (18 好 / 28 小)</label>
      <input id="x-crf" type="number" min="16" max="32" value="23"></div>`);
  }
  if (cap === "media.thumbnail") {
    rows.push(`<div class="field"><label>抽几张</label>
      <input id="x-count" type="number" min="1" max="12" value="3"></div>`);
  }
  if (cap === "image.edit") {
    rows.push(`<div class="field"><label>宽度 (px)</label>
      <input id="x-width" type="number" min="64" max="6000" value="1280"></div>`);
  }
  if (cap === "publish.browser") {
    const opts = accountOptions();
    rows.push(`<div class="field"><label>用哪个账号</label>
      <select id="x-account">${opts}</select></div>`);
    rows.push(`<div class="field"><label>标题</label>
      <input id="x-title" type="text" maxlength="30" placeholder="不超过 30 字"></div>`);
    rows.push(`<div class="field"><label>话题</label>
      <input id="x-tags" type="text" placeholder="用空格分开，如：职场 效率"></div>`);
    rows.push(`<textarea id="x-desc" placeholder="简介（可选）"></textarea>`);
  }

  el.extra.innerHTML = rows.join("");
  el.extra.classList.remove("hidden");
  toggleOpts(cap);
}

function toggleOpts(cap) {
  const isDownload = cap === "download.platform" || cap === "";
  $("#opt-quality").classList.toggle("hidden", !isDownload);
  $("#opt-conn").classList.toggle("hidden", !isDownload);
  $("#opt-srt").classList.toggle("hidden", cap !== "transcribe.asr");
  $("#opt-mode").classList.add("hidden");
}

function accountOptions() {
  const usable = state.accounts.filter((a) => a.state !== "expired");
  if (!usable.length) return `<option value="">先去「账号」里添加一个</option>`;
  return usable.map((a) =>
    `<option value="${a.id}">${esc(a.platform_name)} · ${esc(a.label)}</option>`).join("");
}

/* ───────────────────────── 链接探测 ───────────────────────── */

function scheduleProbe() {
  clearTimeout(state.probeTimer);
  const text = el.url.value.trim();
  if (!text || !isUrl(text)) { clearProbe(); return; }
  if (currentCapability() !== "download.platform") { clearProbe(); return; }
  state.probeTimer = setTimeout(() => probeUrl(text), 650);
}

async function probeUrl(url) {
  const seq = ++state.probeSeq;
  try {
    const info = await api("/api/probe", {
      method: "POST", body: JSON.stringify({ url: extractUrl(url) }),
    });
    if (seq !== state.probeSeq) return;
    if (!info.ok) { clearProbe(); return; }
    state.probe = info;
    renderProbe(info);
    applyQualityOptions(info);
  } catch (_) {
    if (seq === state.probeSeq) clearProbe();
  }
}

function renderProbe(info) {
  if (currentCapability() !== "download.platform") { clearProbe(); return; }
  el.probeCard.classList.remove("hidden");
  if (info.kind === "course") {
    el.probeTitle.textContent = "加密下载（课程回放）";
    const bits = ["回放"];
    if (info.app_id) bits.push("app " + info.app_id);
    bits.push(info.needs_cookie ? "需要登录" : "登录态可用");
    el.probeMeta.textContent = bits.join(" · ");
    el.probeThumb.removeAttribute("src");
    el.probeLogin.classList.toggle("hidden", !info.needs_cookie);
    return;
  }
  el.probeLogin.classList.add("hidden");
  el.probeTitle.textContent = info.title || info.filename || "";
  const bits = [];
  if (info.kind === "video") {
    if (info.uploader) bits.push(info.uploader);
    if (info.duration) bits.push(fmtDuration(info.duration));
    if (info.is_playlist) bits.push(`${info.entry_count} 个视频`);
    if (info.extractor) bits.push(info.extractor);
  } else {
    bits.push("直链文件");
    if (info.size) bits.push(fmtBytes(info.size));
    bits.push(info.supports_range ? "支持多线程" : "单线程");
  }
  el.probeMeta.textContent = bits.join(" · ");
  if (info.thumbnail) el.probeThumb.src = info.thumbnail;
  else el.probeThumb.removeAttribute("src");
}

function clearProbe() {
  state.probe = null;
  el.probeCard.classList.add("hidden");
  el.probeLogin.classList.add("hidden");
}

function applyQualityOptions(info) {
  if (info.kind === "file" || info.kind === "course") {
    el.quality.innerHTML = `<option value="0">不适用</option>`;
    el.quality.disabled = true;
    return;
  }
  el.quality.disabled = false;
  const heights = (info.heights || []).filter((h) => h > 0);
  const opts = ['<option value="0">最高</option>'];
  for (const h of heights) {
    const label = h >= 2160 ? `${h}P 4K` : h >= 1440 ? `${h}P 2K` : `${h}P`;
    opts.push(`<option value="${h}">${label}</option>`);
  }
  el.quality.innerHTML = opts.join("");
  const want = String(state.settings.defaultQuality || 0);
  el.quality.value = heights.includes(+want) ? want : "0";
}

/* ───────────────────────── 添加任务 ───────────────────────── */

async function pasteFromClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    if (text) { el.url.value = text.trim(); el.url.focus(); scheduleProbe(); }
  } catch (_) {
    toast("读取剪贴板失败，请手动粘贴", true);
  }
}

async function pickFile() {
  let path = "";
  if (window.pywebview?.api?.select_file) {
    try { path = await window.pywebview.api.select_file(""); } catch (_) {}
  }
  if (!path) path = prompt("输入文件完整路径", "") || "";
  if (path) { el.url.value = path; el.url.focus(); scheduleDetect(); renderExtra(); }
}

async function pickFolder(current) {
  if (window.pywebview?.api?.select_folder) {
    try { return await window.pywebview.api.select_folder(current || ""); } catch (_) {}
  }
  return prompt("输入目录路径", current || "") || "";
}

async function pickDirectory() {
  const picked = await pickFolder(el.dir.value);
  if (picked) { el.dir.value = picked; saveSettings({ defaultDir: picked }); }
}

async function pickCookieFile() {
  if (window.pywebview?.api?.select_cookie_file) {
    try { return await window.pywebview.api.select_cookie_file(""); } catch (_) {}
  }
  return prompt("输入 cookies.txt 的完整路径", "") || "";
}

async function addTask() {
  const raw = el.url.value.trim();
  if (!raw) { el.url.focus(); return; }
  if (state.busy) return;

  const cap = currentCapability();
  if (!cap) {
    toast("看不出这是什么，先在上面选一个能力", true);
    el.capSelect.focus();
    return;
  }

  state.busy = true;
  el.add.disabled = true;
  try {
    if (cap === "download.platform") await addDownload(raw);
    else if (cap === "publish.browser") await preparePublish(raw);
    else await addCapabilityJob(cap, raw);

    rememberProject();
    el.url.value = "";
    clearProbe();
    el.extra.innerHTML = "";
    el.extra.classList.add("hidden");
    el.add.textContent = "开始";
    setFilter("all");
    await refresh();
  } catch (err) {
    toast("添加失败：" + err.message, true);
  } finally {
    state.busy = false;
    el.add.disabled = false;
  }
}

async function addDownload(url) {
  const body = {
    url: extractUrl(url),
    kind: state.probe?.kind || "auto",
    quality: Number(el.quality.value) || 0,
    dir: el.dir.value,
    connections: Number(el.conn.value) || 8,
    title: state.probe?.title || "",
    project: currentProject(),
    sequence: currentSequence(),
  };
  await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
  toast("已加入下载队列");
}

function buildBody(cap, raw) {
  const params = { dir: el.dir.value || undefined };
  let inputs = { path: raw };
  const val = (id) => $(id)?.value;

  if (cap === "transcribe.asr") {
    params.srt = !!el.srt.checked;
    params.language = state.settings.asrLanguage || "zh";
  } else if (cap === "media.transcode") {
    const mode = val("#x-mode") || "mp4";
    if (mode === "audio") params.audio_only = true;
    else if (mode === "compress") params.crf = Number(val("#x-crf")) || 26;
    if (mode === "mp4") params.crf = Number(val("#x-crf")) || 23;
  } else if (cap === "media.subtitle") {
    params.op = val("#x-mode") || "burn";
    if (params.op === "burn") params.srt = val("#x-srt") || "";
  } else if (cap === "media.audio") {
    params.op = val("#x-mode") || "normalize";
    if (params.op === "speed") params.speed = 1.25;
  } else if (cap === "media.thumbnail") {
    params.count = Number(val("#x-count")) || 3;
  } else if (cap === "image.edit") {
    params.op = val("#x-mode") || "resize";
    params.width = Number(val("#x-width")) || 1280;
  } else if (cap === "image.generate") {
    inputs = {};
    params.prompt = raw;
  } else if (cap === "audio.tts") {
    inputs = {};
    params.text = raw;
  } else if (cap === "audio.voiceclone") {
    inputs = { path: val("#x-sample") || "" };
    params.text = raw;
  }
  return {
    capability: cap, inputs, params,
    project: currentProject(), sequence: currentSequence(),
  };
}

async function addCapabilityJob(cap, raw) {
  const body = buildBody(cap, raw);
  if (!body.inputs.path && !body.params.text && !body.params.prompt) {
    throw new Error("没有可处理的输入");
  }
  await api("/api/exec/jobs", { method: "POST", body: JSON.stringify(body) });
  const name = CAP_META[cap]?.label || "任务";
  toast(`已加入队列：${name}`);
}

async function preparePublish(raw) {
  const accountId = $("#x-account")?.value;
  if (!accountId) throw new Error("先在「账号」里添加并登录一个账号");
  const title = ($("#x-title")?.value || "").trim();
  if (!title) throw new Error("发布必须有标题");
  const tags = ($("#x-tags")?.value || "").split(/[\s,，]+/).filter(Boolean);
  const meta = {
    title,
    desc: ($("#x-desc")?.value || "").trim(),
    hashtags: tags,
  };
  const record = await api("/api/exec/publish", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, files: [raw], meta }),
  });
  state.publishDraft = record;
  const acc = state.accounts.find((a) => a.id === accountId);
  $("#publish-summary").innerHTML =
    `账号：<b>${esc(acc ? acc.platform_name + " · " + acc.label : accountId)}</b><br>` +
    `文件：<b>${esc(raw.split(/[\\/]/).pop())}</b><br>` +
    `标题：<b>${esc(title)}</b><br>` +
    `话题：${tags.length ? tags.map((t) => "#" + esc(t)).join(" ") : "（无）"}`;
  $("#publish-hash").textContent = record.snapshot_hash;
  el.publishModal.classList.remove("hidden");
}

async function approvePublish() {
  const draft = state.publishDraft;
  if (!draft) return;
  const btn = $("#btn-approve-publish");
  btn.disabled = true;
  try {
    await api(`/api/exec/publish/${draft.id}/approve`, {
      method: "POST",
      body: JSON.stringify({ snapshot_hash: draft.snapshot_hash }),
    });
    el.publishModal.classList.add("hidden");
    toast("已开始发布，可以在任务列表里看进度");
    state.publishDraft = null;
    await refresh();
  } catch (err) {
    toast("发布失败：" + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ───────────────────────── 加密下载 ───────────────────────── */

async function refreshCourseAuth(notify = false) {
  try {
    const info = await api("/api/course/auth");
    const line = $("#secure-auth");
    const where = info.has_file ? `${info.path}（${info.count} 条）` : info.canonical;
    line.textContent = info.has_file
      ? `${info.status || "已读取，无有效期信息"}`
      : `还没有登录态，导入后会存到 ${where}`;
    const status = info.status || "";
    const cls = !info.has_file ? "warn"
      : status.includes("过期") ? "bad"
      : status.includes("今天") ? "warn" : "ok";
    line.className = "course-auth-line " + cls;

    // 徽标只说结论，细节留在下面那行，避免同一句话出现两次
    const chip = $("#secure-status");
    chip.className = "status-chip " + (!info.has_file ? "" : cls);
    chip.textContent = !info.has_file ? "未登录"
      : status.includes("过期") ? "登录态已过期"
      : status.includes("今天") ? "今天到期"
      : "登录态有效";

    $("#secure-login").disabled = !info.playwright;
    if (notify) toast(info.has_file ? "登录态：" + (info.status || "已读取") : "还没有登录态");
    return info;
  } catch (err) {
    if (notify) toast("读取登录态失败：" + err.message, true);
    return null;
  }
}

async function importCourseCookies(payload) {
  try {
    const res = await api("/api/course/cookies/import", {
      method: "POST", body: JSON.stringify(payload),
    });
    toast(`已导入 ${res.imported} 条 cookie`);
    $("#secure-cookie-text").value = "";
    await refreshCourseAuth();
  } catch (err) {
    toast("导入失败：" + err.message, true);
  }
}

async function probeSecure() {
  const url = el.secureUrl.value.trim();
  const box = $("#secure-result");
  if (!url) { el.secureUrl.focus(); return null; }
  box.className = "secure-result";
  box.classList.remove("hidden");
  box.textContent = "检测中…";
  try {
    const info = await api("/api/probe", {
      method: "POST", body: JSON.stringify({ url }),
    });
    if (!info.ok || info.kind !== "course") {
      state.secureProbe = null;
      box.className = "secure-result bad";
      box.textContent = info.error ||
        "这不是一条回放链接。回放链接里应该有 l_ 开头的 ID。";
      return null;
    }
    state.secureProbe = info;
    box.className = "secure-result " + (info.needs_cookie ? "warn" : "ok");
    box.innerHTML = [
      `<div class="kv"><span>回放 ID</span><b>${esc(info.alive_id)}</b></div>`,
      `<div class="kv"><span>应用</span><b>${esc(info.app_id || "未识别")}</b></div>`,
      `<div class="kv"><span>登录态</span><b>${
        info.needs_cookie ? "需要先登录一次" : "可用"}</b></div>`,
    ].join("");
    return info;
  } catch (err) {
    state.secureProbe = null;
    box.className = "secure-result bad";
    box.textContent = "检测失败：" + err.message;
    return null;
  }
}

async function addSecureDownload() {
  const url = el.secureUrl.value.trim();
  if (!url) { el.secureUrl.focus(); return; }
  const info = state.secureProbe || await probeSecure();
  if (!info) return;
  if (info.needs_cookie) {
    toast("这条需要登录：先点「在浏览器里登录一次」", true);
    return;
  }
  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        url, kind: "course", quality: 0,
        dir: el.secureDir.value,
        connections: Number(el.secureWorkers.value) || 24,
        title: "",
        project: (el.secureProject.value || "").trim(),
        sequence: (el.secureSequence.value || "").trim(),
      }),
    });
    toast("已加入下载队列");
    el.secureUrl.value = "";
    state.secureProbe = null;
    $("#secure-result").classList.add("hidden");
    setView("tasks");
  } catch (err) {
    toast("加入失败：" + err.message, true);
  }
}

async function startCourseLogin() {
  const info = state.secureProbe && state.secureProbe.kind === "course"
    ? state.secureProbe
    : (state.probe && state.probe.kind === "course" ? state.probe : {});
  if (!info.app_id) {
    toast("先在「加密下载」里粘一条回放链接，再点登录", true);
    setView("secure");
    return;
  }
  try {
    await api("/api/course/login", {
      method: "POST",
      body: JSON.stringify({ app_id: info.app_id, alive_id: info.alive_id }),
    });
    toast("浏览器已打开，登录完成后会自动保存");
    pollCourseLogin();
  } catch (err) {
    toast("登录失败：" + err.message, true);
  }
}

function pollCourseLogin() {
  clearInterval(state.loginTimer);
  state.loginTimer = setInterval(async () => {
    let s;
    try { s = await api("/api/course/login/status"); }
    catch (_) { clearInterval(state.loginTimer); return; }
    const line = $("#secure-auth");
    if (s.state === "running") {
      line.textContent = (s.log || []).slice(-1)[0] || "等待你在浏览器里完成登录…";
      line.className = "course-auth-line warn";
      return;
    }
    clearInterval(state.loginTimer);
    if (s.state === "done") {
      toast("登录态已保存");
      refreshCourseAuth();
      if (state.secureProbe) probeSecure();
    } else if (s.state === "error") {
      toast("登录失败：" + s.error, true);
      refreshCourseAuth();
    }
  }, 2000);
}

/* ───────────────────────── 模型与接口 ───────────────────────── */

const LOCAL_KIND_LABEL = { asr: "转写", ocr: "文字识别" };

async function loadModels() {
  try {
    state.models = await api("/api/exec/models");
    renderModelRows();
    renderProviders();
    renderLocalEngines();
    renderInstallStatus(state.models.localInstall);
    const budget = state.settings.monthlyBudget ?? 100;
    $("#set-budget").value = budget;
  } catch (err) {
    $("#model-rows").innerHTML =
      `<div class="muted">读取失败：${esc(err.message)}</div>`;
  }
}

function renderModelRows() {
  const caps = (state.models && state.models.capabilities) || [];
  if (!caps.length) { $("#model-rows").innerHTML = ""; return; }

  $("#model-rows").innerHTML = caps.map((cap) => {
    const choice = cap.choice || {};
    const backend = choice.backend || "auto";
    const provider = choice.provider || cap.provider || (cap.providers || [])[0] || "";
    const model = choice.model || (cap.backend === "cloud" ? cap.model : "");
    const localOptions = cap.localOptions || [];
    const localAvailable = localOptions.some((e) => e.available);

    const providerOpts = (cap.providers || []).map((p) =>
      `<option value="${p}" ${p === provider ? "selected" : ""}>${esc(providerName(p))}</option>`)
      .join("");
    const engineOpts = localOptions.map((e) =>
      `<option value="${e.id}" ${e.id === choice.localEngine ? "selected" : ""}
        ${e.available ? "" : "disabled"}>${esc(e.name)}${e.available ? "" : "（未安装）"}</option>`)
      .join("");

    const localBox = cap.cloudOnly ? "" : `
      <div class="m-slot m-local ${backend === "local" ? "" : "hidden"}">
        <select class="m-engine" title="本地引擎">${engineOpts}</select>
        <input class="m-local-model" placeholder="${esc(defaultLocalModel(cap.capability))}"
               value="${esc(choice.localModel || "")}">
      </div>`;

    const cloudBox = `
      <div class="m-slot m-cloud ${backend === "local" ? "hidden" : ""}">
        <select class="m-provider">${providerOpts}</select>
        <input class="m-model" placeholder="模型" value="${esc(model)}">
      </div>`;

    const state = cap.ready
      ? `<span class="m-state ok">${cap.backend === "local"
            ? "本地 · " + esc(cap.local_name || "") + (cap.model ? " · " + esc(cap.model) : "")
            : esc(cap.provider_name || "") + (cap.model ? " · " + esc(cap.model) : "")}</span>`
      : `<span class="m-state bad">${esc(cap.problem || "不可用")}</span>`;

    const backendOpts = [
      `<option value="auto" ${backend === "auto" ? "selected" : ""}>自动</option>`,
      cap.cloudOnly ? "" : `<option value="local" ${backend === "local" ? "selected" : ""}>本地</option>`,
      `<option value="cloud" ${backend === "cloud" ? "selected" : ""}>云端 API</option>`,
    ].join("");

    return `<div class="model-row" data-cap="${esc(cap.capability)}"
                 data-cloud-only="${cap.cloudOnly ? "1" : ""}">
      <div class="m-head">
        <b>${esc(capName(cap.capability))}</b>
        <small>${esc(cap.capability)}${cap.cloudOnly
          ? " · 只能走 API" : (localAvailable ? "" : " · 本地引擎未安装")}</small>
      </div>
      <div class="m-controls">
        <select class="m-backend">${backendOpts}</select>
        ${localBox}${cloudBox}
      </div>
      ${state}
    </div>`;
  }).join("");

  $("#model-rows").querySelectorAll(".model-row").forEach((row) => {
    const cap = row.dataset.cap;
    const backendChanged = () => {
      const bt = row.querySelector(".m-backend").value;
      row.querySelector(".m-local").classList.toggle("hidden", bt !== "local");
      row.querySelector(".m-cloud").classList.toggle("hidden", bt === "local");
      saveModelChoice(cap, row);
    };
    row.querySelector(".m-backend").addEventListener("change", backendChanged);
    row.querySelectorAll(".m-provider, .m-engine, .m-model, .m-local-model")
      .forEach((input) => input.addEventListener("change", () => saveModelChoice(cap, row)));
  });
}

async function saveModelChoice(cap, row) {
  const body = {
    backend: row.querySelector(".m-backend").value,
    provider: row.querySelector(".m-provider")?.value || "",
    model: row.querySelector(".m-model")?.value.trim() || "",
    localEngine: row.querySelector(".m-engine")?.value || "",
    localModel: row.querySelector(".m-local-model")?.value.trim() || "",
  };
  try {
    await api(`/api/exec/models/${encodeURIComponent(cap)}`, {
      method: "POST", body: JSON.stringify(body),
    });
    await loadModels();
    await loadCaps();
    toast("已切换：" + capName(cap));
  } catch (err) {
    toast("保存失败：" + err.message, true);
  }
}

function renderProviders() {
  const list = (state.models && state.models.providers) || [];
  $("#provider-rows").innerHTML = list.map((p) => `
    <div class="provider-row" data-provider="${esc(p.key)}">
      <div class="provider-head">
        <b>${esc(p.name)}</b>
        <span class="tag ${p.hasKey ? "local" : ""}">${p.hasKey ? "已配置" : "未配置"}</span>
        ${p.home ? `<a href="${esc(p.home)}" target="_blank" rel="noreferrer">拿密钥</a>` : ""}
      </div>
      <small class="muted">${esc(p.note)}</small>
      ${p.key === "custom" ? `
        <div class="row">
          <input class="p-base" type="text" placeholder="https://你的服务/v1"
                 value="${esc(p.base || "")}">
        </div>` : ""}
      <div class="row">
        <input class="p-key" type="password" spellcheck="false"
               placeholder="${p.hasKey ? "已保存，留空表示不改" : "粘贴密钥"}">
        <button class="p-save btn ghost small">保存</button>
      </div>
    </div>`).join("");

  $("#provider-rows").querySelectorAll(".provider-row").forEach((row) => {
    row.querySelector(".p-save").addEventListener("click", async () => {
      const key = row.dataset.provider;
      const provider = (state.models.providers || []).find((p) => p.key === key);
      const values = {};
      const typed = row.querySelector(".p-key").value.trim();
      if (typed) values[provider.keyField] = typed;
      const baseInput = row.querySelector(".p-base");
      if (baseInput) values.customBaseUrl = baseInput.value.trim();
      if (!Object.keys(values).length) { toast("没有要保存的内容"); return; }
      await saveSettings(values);
      toast(typed ? "密钥已保存" : "地址已保存");
      await loadModels();
    });
  });
}

function renderLocalEngines() {
  // 从各能力的 localOptions 汇总，按 id 去重
  const seen = new Map();
  for (const cap of (state.models?.capabilities || [])) {
    for (const engine of cap.localOptions || []) {
      if (!seen.has(engine.id)) {
        seen.set(engine.id, { ...engine, kind: engine.kind || guessKind(cap.capability) });
      }
    }
  }
  const rows = Array.from(seen.values());
  if (!rows.length) {
    $("#local-rows").innerHTML = `<div class="muted">没有可本地运行的能力</div>`;
    return;
  }

  $("#local-rows").innerHTML = rows.map((e) => `
    <div class="local-row">
      <div class="l-info">
        <div class="l-title">
          <b>${esc(e.name)}</b>
          <span class="tag ${e.available ? "local" : ""}">${e.available ? "已安装" : "未安装"}</span>
          <small>${esc(LOCAL_KIND_LABEL[e.kind] || "")} · ${esc(e.size || "")}</small>
        </div>
        <p class="l-note">${esc(e.note || "")}</p>
      </div>
      <div class="l-action">
        ${e.available
          ? `<span class="muted small">自动优先使用</span>`
          : `<button class="btn ghost small l-install" data-engine="${esc(e.id)}">安装</button>`}
      </div>
    </div>`).join("");

  $("#local-rows").appendChild(logBox());
  $("#local-rows").querySelectorAll(".l-install").forEach((btn) => {
    btn.addEventListener("click", () => installEngine(btn.dataset.engine, btn));
  });
}

function logBox() {
  const box = document.createElement("pre");
  box.id = "local-install-log";
  box.className = "install-log hidden";
  return box;
}

async function installEngine(engineId, btn) {
  btn.disabled = true;
  btn.textContent = "安装中…";
  try {
    await api("/api/exec/local/install", {
      method: "POST", body: JSON.stringify({ engine: engineId }),
    });
    toast("开始安装，几十秒到几分钟；装完会自动切换");
    state.installEngineId = engineId;
    pollInstall();
  } catch (err) {
    toast("安装失败：" + err.message, true);
    btn.disabled = false;
    btn.textContent = "安装";
  }
}

function pollInstall() {
  clearInterval(state.localInstallTimer);
  state.localInstallTimer = setInterval(async () => {
    let s;
    try { s = await api("/api/exec/local"); }
    catch (_) { return; }
    renderInstallStatus(s.install);
    if (!s.install.running) {
      clearInterval(state.localInstallTimer);
      if (s.install.ok) {
        toast("安装完成，已能用本地引擎");
      } else {
        toast("安装失败：" + (s.install.error || "看下面的日志"), true);
      }
      await loadModels();
      await loadCaps();
    }
  }, 1500);
}

function renderInstallStatus(install) {
  const box = $("#local-install-log");
  if (!box || !install) return;
  const hasText = install.running || (install.log || []).length;
  box.classList.toggle("hidden", !hasText);
  if (!hasText) return;
  const lines = (install.log || []).slice(-14);
  if (install.running) lines.push(`… 正在安装 ${install.package}，请稍候`);
  if (install.ok === false) lines.push("✗ " + (install.error || "失败"));
  if (install.ok === true) lines.push("✓ 安装完成");
  box.textContent = lines.join("\n");
  box.scrollTop = box.scrollHeight;
}

function capName(id) {
  const cap = state.caps.find((c) => c.id === id);
  return CAP_META[id]?.label || cap?.name || id;
}

function guessKind(capId) {
  return capId === "transcribe.asr" ? "asr" : "ocr";
}

function defaultLocalModel(capId) {
  return capId === "transcribe.asr" ? "small" : "自动";
}

function providerName(key) {
  const p = (state.models?.providers || []).find((x) => x.key === key);
  return p ? p.name : key;
}

/* ───────────────────────── 任务列表 ───────────────────────── */

const ACTIVE = ["queued", "running", "downloading", "merging", "paused"];

function matchesFilter(t) {
  if (state.filter === "all") return true;
  if (state.filter === "active") return ACTIVE.includes(t.status);
  if (state.filter === "done") return t.status === "done";
  if (state.filter === "error") return ["error", "cancelled"].includes(t.status);
  return true;
}

async function refresh() {
  if (state.busy) return;
  try {
    const data = await api("/api/tasks");
    state.tasks = data.tasks || [];
    render();
  } catch (_) {}
}

function render() {
  $("#c-all").textContent = state.tasks.length;

  const visible = state.tasks
    .filter(matchesFilter)
    .sort((a, b) => (b.created_at || 0) - (a.created_at || 0));

  el.empty.classList.toggle("hidden", visible.length > 0);

  const alive = new Set();
  visible.forEach((t, idx) => {
    alive.add(t.id);
    let node = state.nodes.get(t.id);
    if (!node) {
      node = createTaskNode(t);
      state.nodes.set(t.id, node);
      el.list.appendChild(node);
    }
    updateTaskNode(node, t);
    const current = el.list.children[idx];
    if (current !== node) el.list.insertBefore(node, current || null);
  });

  for (const [id, node] of state.nodes) {
    if (!alive.has(id)) { node.remove(); state.nodes.delete(id); }
  }
}

function createTaskNode(t) {
  const node = document.createElement("div");
  node.className = "task";
  node.dataset.id = t.id;
  node.innerHTML = `
    <div class="thumb"><span class="thumb-ico"></span><img class="thumb-img" alt="" hidden></div>
    <div class="task-main">
      <div class="task-meta"></div>
      <div class="task-top"><span class="task-title"></span><span class="pill"></span></div>
      <div class="task-sub"></div>
      <div class="progress"><i></i></div>
    </div>
    <div class="task-actions"></div>`;
  return node;
}

function setText(node, selector, text) {
  const target = node.querySelector(selector);
  if (target.dataset.v !== text) { target.textContent = text; target.dataset.v = text; }
}

function updateTaskNode(node, t) {
  node.className = "task " + t.status;

  const meta = CAP_META[t.capability] || CAP_KINDS[t.kind] || { glyph: "•" };
  const thumb = node.querySelector(".thumb");
  const img = node.querySelector(".thumb-img");
  const ico = node.querySelector(".thumb-ico");
  if (t.thumbnail) {
    if (img.dataset.src !== t.thumbnail) { img.src = t.thumbnail; img.dataset.src = t.thumbnail; }
    img.hidden = false; ico.hidden = true;
  } else {
    img.hidden = true; ico.hidden = false;
    if (ico.dataset.v !== meta.glyph) { ico.textContent = meta.glyph; ico.dataset.v = meta.glyph; }
    thumb.style.color = t.status === "error" ? "#b3402a"
      : t.status === "done" ? "#2f7d5c" : "#657176";
  }

  const title = t.title || t.filename || t.url || "未命名";
  setText(node, ".task-title", title);
  node.querySelector(".task-title").title = title;

  const pill = node.querySelector(".pill");
  pill.className = "pill " + t.status;
  pill.textContent = STATUS_TEXT[t.status] || t.status;

  let sub;
  if (t.status === "done") sub = t.path || (t.outputs || [])[0] || t.filename || "";
  else if (t.capability) sub = t.stage || (t.filename || t.url || "");
  else sub = t.filename ? `${t.filename} · ${t.dir}` : (t.url || "");
  setText(node, ".task-sub", sub);
  node.querySelector(".task-sub").title = sub;

  const bar = node.querySelector(".progress");
  const indeterminate = t.status === "running" && t.progress_known === false;
  bar.classList.toggle("indeterminate", indeterminate);
  const pct = t.status === "done" ? 100 : (t.progress || 0);
  node.querySelector(".progress i").style.width = `${Math.min(100, pct)}%`;

  renderMeta(node, t, indeterminate);
  renderActions(node, t);
}

function renderMeta(node, t, indeterminate) {
  const box = node.querySelector(".task-meta");
  const bits = [];
  const add = (text, cls) => { if (text) bits.push(cls ? `<span class="${cls}">${esc(text)}</span>` : `<span>${esc(text)}</span>`); };

  add(fmtTime(t.created_at), "when");
  if (t.project || t.sequence) {
    add([t.project, t.sequence].filter(Boolean).join(" / "), "proj");
  }

  if (t.status === "error") {
    add(t.error || "未知错误", "err");
  } else if (t.status === "cancelled") {
    add("已取消");
  } else if (t.capability) {
    const label = CAP_META[t.capability]?.label;
    if (t.status === "done") {
      const outputs = t.outputs || [];
      add(label);
      add(outputs.length > 1 ? `已生成 ${outputs.length} 个产物` : "已保存");
    } else {
      add(label);
      if (indeterminate) {
        add(t.stage || "处理中");
        add("没有确定总量，不显示假进度");
      } else {
        if (t.stage) add(t.stage);
        add(`${(t.progress || 0).toFixed(0)}%`);
      }
    }
  } else if (t.kind === "course") {
    const seg = t.segments_total ? `${t.segments_done || 0}/${t.segments_total} 片` : "";
    if (t.status === "done") { add(fmtBytes(t.size)); add("已保存"); }
    else if (t.status === "queued") add("等待开始");
    else if (t.status === "merging") add("正在合并分片…");
    else {
      add(t.stage || (t.status === "paused" ? "已暂停" : "下载中"));
      add(seg);
      add(`${(t.progress || 0).toFixed(1)}%`);
    }
  } else if (t.status === "done") {
    add(fmtBytes(t.size)); add("已保存");
  } else if (t.status === "queued") {
    add(t.kind === "video" ? "等待解析" : "等待开始");
    add(t.size ? fmtBytes(t.size) : "");
  } else if (t.status === "paused") {
    add(`${(t.progress || 0).toFixed(1)}%`);
    add(`${fmtBytes(t.downloaded)} / ${fmtBytes(t.size)}`);
  } else {
    add(`${(t.progress || 0).toFixed(1)}%`);
    add(`${fmtBytes(t.downloaded)} / ${fmtBytes(t.size)}`);
    add(fmtSpeed(t.speed));
    if (t.eta) add(`剩余 ${fmtEta(t.eta)}`);
  }

  const html = bits.join("");
  if (box.dataset.v !== html) { box.innerHTML = html; box.dataset.v = html; }
}

function renderActions(node, t) {
  const box = node.querySelector(".task-actions");
  const key = t.status;
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.innerHTML = "";

  const btn = (label, act, danger) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.dataset.act = act;
    if (danger) b.className = "danger";
    box.appendChild(b);
  };

  if (ACTIVE.includes(t.status) && t.status !== "paused") {
    btn("暂停", "pause");
    btn("取消", "cancel", true);
  } else if (t.status === "paused") {
    btn("继续", "resume");
    btn("取消", "cancel", true);
  } else if (t.status === "done") {
    btn("打开", "open");
    btn("移除", "remove", true);
  } else {
    btn("重试", "retry");
    btn("移除", "remove", true);
  }
}

async function onListClick(e) {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".task").dataset.id;
  const act = btn.dataset.act;
  btn.disabled = true;
  try {
    await api(`/api/tasks/${id}/${act}`, { method: "POST" });
    await refresh();
  } catch (err) {
    toast("操作失败：" + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ───────────────────────── 账号 ───────────────────────── */

async function refreshAccounts() {
  try {
    const data = await api("/api/exec/accounts");
    state.accounts = data.accounts || [];
    $("#c-accounts").textContent = state.accounts.length;
    renderSideAccounts();
    renderAccountPane();
  } catch (_) {}
}

function groupByPlatform(accounts) {
  const groups = new Map();
  for (const acc of accounts) {
    if (!groups.has(acc.platform_name)) groups.set(acc.platform_name, []);
    groups.get(acc.platform_name).push(acc);
  }
  return groups;
}

function renderSideAccounts() {
  const box = $("#side-accounts");
  if (!state.accounts.length) {
    box.innerHTML = `<div class="side-empty">还没有账号。<br>添加后每个平台一个独立浏览器，登一次能用几天。</div>`;
    return;
  }
  const html = [];
  for (const [name, list] of groupByPlatform(state.accounts)) {
    html.push(`<div class="side-group"><div class="side-group-name">${esc(name)}</div>`);
    for (const acc of list) {
      const days = acc.remain_days != null ? `${Math.max(0, acc.remain_days).toFixed(0)}天` : "";
      html.push(`<button class="side-account ${acc.id === state.activeAccount ? "active" : ""}"
        data-account="${acc.id}">
        <span class="dot ${acc.state}"></span>
        <span class="name">${esc(acc.label)}</span>
        <span class="days">${days}</span>
      </button>`);
    }
    html.push(`</div>`);
  }
  box.innerHTML = html.join("");
  box.querySelectorAll("[data-account]").forEach((btn) => {
    btn.addEventListener("click", () => selectAccount(btn.dataset.account, true));
  });
}

function renderAccountPane() {
  const pane = $("#account-pane");
  if (!state.accounts.length) {
    pane.innerHTML = `<div class="side-empty">还没有账号</div>`;
    return;
  }
  const html = [];
  for (const [name, list] of groupByPlatform(state.accounts)) {
    html.push(`<div class="acct-group-title">${esc(name)}</div>`);
    for (const acc of list) {
      const stateCls = acc.state === "expiring" ? "expiring"
        : acc.state === "expired" ? "expired" : "";
      html.push(`<div class="acct-item ${acc.id === state.activeAccount ? "active" : ""}"
          data-account="${acc.id}">
        <span class="dot ${acc.state}"></span>
        <span class="acct-info">
          <span class="acct-name">${esc(acc.label)}</span>
          <span class="acct-state ${stateCls}">${esc(acc.state_text)}</span>
        </span>
        <button class="acct-del" data-del="${acc.id}" title="删除账号">✕</button>
      </div>`);
    }
  }
  pane.innerHTML = html.join("");
  pane.querySelectorAll("[data-account]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("[data-del]")) return;
      selectAccount(row.dataset.account, false);
    });
  });
  pane.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteAccount(btn.dataset.del));
  });
}

function selectAccount(accountId, jump) {
  state.activeAccount = accountId;
  renderSideAccounts();
  renderAccountPane();
  const acc = state.accounts.find((a) => a.id === accountId);
  $("#browser-url").value = "";
  $("#browser-url").placeholder = acc ? acc.platform_name + " 首页" : "当前账号首页";
  $("#browser-empty").hidden = false;
  $("#browser-frame").hidden = true;
  if (jump) { setView("accounts"); }
  openAccount(accountId);
}

async function openAccount(accountId) {
  try {
    const res = await api(`/api/exec/accounts/${accountId}/open`, {
      method: "POST", body: JSON.stringify({}),
    });
    if (res.url) $("#browser-url").value = res.url;
    $("#browser-empty").hidden = true;
    $("#browser-frame").hidden = false;
    startFrames();
    setTimeout(refreshAccounts, 1500);
  } catch (err) {
    toast("打开浏览器失败：" + err.message, true);
  }
}

function startFrames() {
  stopFrames();
  if (state.view !== "accounts" || !state.activeAccount) return;
  state.frameTimer = setInterval(pullFrame, 260);
  pullFrame();
}

function stopFrames() {
  clearInterval(state.frameTimer);
  state.frameTimer = null;
}

async function pullFrame() {
  if (state.frameBusy || !state.activeAccount) return;
  state.frameBusy = true;
  try {
    const res = await fetch(`/api/exec/accounts/${state.activeAccount}/frame.jpg`, { cache: "no-store" });
    if (res.status === 200) {
      const blob = await res.blob();
      const img = $("#browser-frame");
      const url = URL.createObjectURL(blob);
      const old = img.dataset.obj;
      img.onload = () => { if (old) URL.revokeObjectURL(old); };
      img.src = url;
      img.dataset.obj = url;
      img.hidden = false;
      $("#browser-empty").hidden = true;
    }
  } catch (_) {
  } finally {
    state.frameBusy = false;
  }
}

async function onFrameClick(e) {
  if (!state.activeAccount) return;
  const img = e.currentTarget;
  const rect = img.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (img.naturalWidth / rect.width);
  const y = (e.clientY - rect.top) * (img.naturalHeight / rect.height);
  await accountAction({ kind: "click", x, y });
}

async function accountAction(payload) {
  if (!state.activeAccount) return;
  try {
    await api(`/api/exec/accounts/${state.activeAccount}/act`, {
      method: "POST", body: JSON.stringify(payload),
    });
  } catch (err) {
    toast("操作失败：" + err.message, true);
  }
}

async function checkAccount() {
  if (!state.activeAccount) return;
  try {
    const res = await api(`/api/exec/accounts/${state.activeAccount}/check`, {
      method: "POST", body: JSON.stringify({ online: true }),
    });
    toast(res.ok ? "登录状态：" + res.state_text : "检查失败：" + res.error, !res.ok);
    refreshAccounts();
  } catch (err) {
    toast("检查失败：" + err.message, true);
  }
}

function openAccountModal() {
  $("#platform-grid").innerHTML = state.platforms
    .filter((p) => p.can_publish !== false || p.key === "bilibili")
    .map((p) => `<button class="platform-btn ${p.key === state.accountPlatform ? "on" : ""}"
        data-platform="${p.key}">${esc(p.name)}</button>`).join("");
  $("#platform-grid").querySelectorAll("[data-platform]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.accountPlatform = btn.dataset.platform;
      $("#platform-grid").querySelectorAll(".platform-btn")
        .forEach((b) => b.classList.toggle("on", b === btn));
    });
  });
  $("#account-label").value = "";
  el.accountModal.classList.remove("hidden");
}

async function createAccount() {
  const label = $("#account-label").value.trim();
  const btn = $("#btn-create-account");
  btn.disabled = true;
  try {
    const acc = await api("/api/exec/accounts", {
      method: "POST",
      body: JSON.stringify({ platform: state.accountPlatform, label }),
    });
    el.accountModal.classList.add("hidden");
    await refreshAccounts();
    setView("accounts");
    selectAccount(acc.id, false);
  } catch (err) {
    toast("添加失败：" + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function deleteAccount(accountId) {
  if (!confirm("删除这个账号？浏览器目录会一起清掉，需要重新登录。")) return;
  try {
    await api(`/api/exec/accounts/${accountId}`, { method: "DELETE" });
    if (state.activeAccount === accountId) {
      state.activeAccount = "";
      stopFrames();
      $("#browser-frame").hidden = true;
      $("#browser-empty").hidden = false;
    }
    await refreshAccounts();
  } catch (err) {
    toast("删除失败：" + err.message, true);
  }
}

/* ───────────────────────── 连接 Agent ───────────────────────── */

async function loadConnect() {
  try {
    const info = await api("/api/exec/connect");
    const rows = [`<div class="addr-row"><span>本机</span><code>${esc(info.local)}</code></div>`];
    for (const item of info.lan || []) {
      const url = typeof item === "string" ? item : item.url;
      const label = typeof item === "string" ? "内网" : item.label;
      rows.push(`<div class="addr-row"><span>${esc(label)}</span><code>${esc(url)}</code></div>`);
    }
    $("#conn-addresses").innerHTML = rows.join("");
    $("#conn-token").textContent = info.token_set ? info.token_preview : "未生成";
    state.connInfo = info;
    renderConnCode();
  } catch (err) {
    $("#conn-addresses").innerHTML = `<div class="muted">读取失败：${esc(err.message)}</div>`;
  }
}

function renderConnCode() {
  const info = state.connInfo || { local: "http://127.0.0.1:8765", lan: [] };
  const url = info.local || "http://127.0.0.1:8765";
  const token = "<在「连接 Agent」里生成的令牌>";
  let text = "";
  if (state.connTab === "mcp") {
    text = JSON.stringify({
      mcpServers: {
        ocemind: {
          command: "python",
          args: ["-m", "xiaoe_dl.executor.mcp"],
          env: { OCEMIND_URL: url, OCEMIND_TOKEN: token },
        },
      },
    }, null, 2);
  } else if (state.connTab === "api") {
    text = [
      `# 能力清单`,
      `curl -H "Authorization: Bearer ${token}" ${url}/api/v1/capabilities`,
      ``,
      `# 提交任务`,
      `curl -X POST ${url}/api/v1/jobs \\`,
      `  -H "Authorization: Bearer ${token}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"capability":"transcribe.asr","inputs":{"path":"D:/a.mp4"}}'`,
      ``,
      `# 查状态`,
      `curl -H "Authorization: Bearer ${token}" ${url}/api/v1/jobs/<job_id>`,
    ].join("\n");
  } else {
    text = [
      `python -m xiaoe_dl.executor.cli capabilities`,
      `python -m xiaoe_dl.executor.cli download "https://www.bilibili.com/video/BV..."`,
      `python -m xiaoe_dl.executor.cli transcribe "D:\\视频\\访谈.mp4"`,
      `python -m xiaoe_dl.executor.cli accounts`,
      ``,
      `# 指向远端的执行器`,
      `python -m xiaoe_dl.executor.cli --url ${url} --token ${token} jobs`,
    ].join("\n");
  }
  $("#conn-code").textContent = text;
}

async function rotateToken() {
  if (!confirm("重新生成令牌？已经接入的 Agent 会立刻失效，需要更新配置。")) return;
  try {
    const res = await api("/api/exec/connect/token", { method: "POST" });
    $("#conn-token").textContent = res.token.slice(0, 4) + "…" + res.token.slice(-4);
    state.connInfo.token_set = true;
    toast("新令牌已生成，请更新 Agent 配置");
    renderConnCode();
  } catch (err) {
    toast("生成失败：" + err.message, true);
  }
}

async function testConnection() {
  const box = $("#conn-test");
  box.className = "test-result";
  box.textContent = "正在调用 /api/v1/capabilities…";
  try {
    const res = await api("/api/exec/connect/test", { method: "POST" });
    if (res.ok) {
      box.className = "test-result ok";
      box.textContent = `✓ 连通，${res.count} 项能力可用`;
    } else {
      box.className = "test-result bad";
      box.textContent = "✗ " + res.error;
    }
  } catch (err) {
    box.className = "test-result bad";
    box.textContent = "✗ " + err.message;
  }
}

function renderCapList() {
  $("#cap-count").textContent = `（${state.caps.length} 项）`;
  $("#cap-list").innerHTML = state.caps.map((cap) => {
    const tags = [`<span class="tag ${cap.backend}">${
      cap.backend === "local" ? "本地" : cap.backend === "cloud" ? "云端" : "浏览器"}</span>`];
    if (cap.paid) tags.push(`<span class="tag paid">计费</span>`);
    return `<div class="cap-item">
      <div class="cap-name">${esc(CAP_META[cap.id]?.label || cap.name)} ${tags.join("")}</div>
      <div class="cap-id">${esc(cap.id)}</div>
      <div class="cap-note">${esc(cap.notes || "")}</div>
    </div>`;
  }).join("");
}

/* ───────────────────────── 设置 ───────────────────────── */

async function saveSettings(patch) {
  try {
    state.settings = await api("/api/settings", {
      method: "POST", body: JSON.stringify({ values: patch }),
    });
  } catch (err) {
    toast("保存设置失败：" + err.message, true);
  }
}

function openSettings() {
  const s = state.settings;
  $("#set-dir").value = s.defaultDir || "";
  $("#set-conn").value = s.defaultConnections || 8;
  $("#set-concurrent").value = s.maxConcurrent || 3;
  $("#set-rate").value = s.maxRateKB || 0;
  $("#set-chunk").value = s.minChunkMB || 2;
  $("#set-cookie").value = s.cookieBrowser || "";
  $("#set-proxy-on").checked = !!s.proxyEnabled;
  $("#set-proxy-type").value = s.proxyType || "http";
  $("#set-proxy-host").value = s.proxyHost || "";
  $("#set-proxy-port").value = s.proxyPort || "";
  $("#set-ytdlp").value = s.ytdlpPath || "";
  $("#set-ffmpeg").value = s.ffmpegPath || "";
  $("#set-chrome").value = s.chromePath || "";
  $("#set-headless").checked = !!s.headless;
  el.settingsModal.classList.remove("hidden");
}

async function saveSettingsForm() {
  const values = {
    defaultDir: $("#set-dir").value.trim(),
    defaultConnections: Number($("#set-conn").value) || 8,
    maxConcurrent: Number($("#set-concurrent").value) || 3,
    maxRateKB: Number($("#set-rate").value) || 0,
    minChunkMB: Number($("#set-chunk").value) || 2,
    cookieBrowser: $("#set-cookie").value,
    proxyEnabled: $("#set-proxy-on").checked,
    proxyType: $("#set-proxy-type").value,
    proxyHost: $("#set-proxy-host").value.trim(),
    proxyPort: $("#set-proxy-port").value.trim(),
    ytdlpPath: $("#set-ytdlp").value.trim(),
    ffmpegPath: $("#set-ffmpeg").value.trim(),
    chromePath: $("#set-chrome").value.trim(),
    headless: $("#set-headless").checked,
  };
  await saveSettings(values);
  el.dir.value = values.defaultDir || el.dir.value;
  el.conn.value = values.defaultConnections;
  el.settingsModal.classList.add("hidden");
  toast("设置已保存");
  loadSystem();
}

/* ───────────────────────── 启动 ───────────────────────── */

init();
