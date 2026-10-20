/* TDM Lite 前端逻辑 */

const STATUS_TEXT = {
  queued: "排队中", downloading: "下载中", merging: "合并中",
  paused: "已暂停", done: "已完成", error: "失败", cancelled: "已取消",
};

const state = {
  tasks: [],
  filter: "all",
  settings: {},
  probe: null,
  nodes: new Map(),
  busy: false,
  probeTimer: null,
  probeSeq: 0,
};

const $ = (sel) => document.querySelector(sel);
const el = {
  url: $("#url-input"), add: $("#btn-add"), paste: $("#btn-paste"),
  dir: $("#dir-input"), pickDir: $("#btn-dir"), quality: $("#quality-select"),
  conn: $("#conn-input"), list: $("#task-list"), empty: $("#empty"),
  probeCard: $("#probe-card"), probeThumb: $("#probe-thumb"),
  probeTitle: $("#probe-title"), probeMeta: $("#probe-meta"),
  title: $("#page-title"), modal: $("#settings-modal"), toast: $("#toast"),
  ytdlp: $("#tool-ytdlp"), ffmpeg: $("#tool-ffmpeg"),
};

/* ---------------- 工具函数 ---------------- */

function fmtBytes(n) {
  if (!n || n < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

function fmtSpeed(n) {
  return n > 0 ? `${fmtBytes(n)}/s` : "—";
}

function fmtEta(sec) {
  if (!sec || sec <= 0 || !isFinite(sec)) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}时${m}分`;
  if (m > 0) return `${m}分${s}秒`;
  return `${s}秒`;
}

function fmtDuration(sec) {
  if (!sec) return "";
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

let toastTimer = null;
function toast(msg, isError = false) {
  el.toast.textContent = msg;
  el.toast.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.className = "toast"; }, 2600);
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
  return res.json();
}

/* ---------------- 初始化 ---------------- */

async function init() {
  try {
    const sys = await api("/api/system");
    state.settings = sys.settings;
    el.dir.value = sys.settings.defaultDir || "";
    el.conn.value = sys.settings.defaultConnections || 8;
    el.quality.value = String(sys.settings.defaultQuality || 0);
    const yt = sys.tools.ytdlp || "未找到";
    el.ytdlp.textContent = yt.split(" ")[0];
    el.ytdlp.title = sys.tools.ytdlp_cmd || "";
    el.ffmpeg.textContent = sys.tools.ffmpeg || "未找到";
    el.ffmpeg.title = sys.tools.ffmpeg_dir || "";
  } catch (err) {
    toast("无法连接本地服务：" + err.message, true);
  }
  bindEvents();
  await refresh();
  setInterval(refresh, 700);
}

function bindEvents() {
  el.add.addEventListener("click", addTask);
  el.url.addEventListener("keydown", (e) => { if (e.key === "Enter") addTask(); });
  el.url.addEventListener("input", scheduleProbe);
  el.paste.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) { el.url.value = text.trim(); scheduleProbe(); el.url.focus(); }
    } catch (_) {
      toast("读取剪贴板失败，请手动粘贴", true);
      el.url.focus();
    }
  });
  el.pickDir.addEventListener("click", async () => {
    const picked = await pickFolder(el.dir.value);
    if (picked) { el.dir.value = picked; saveSettings({ defaultDir: picked }); }
  });
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-close-settings").addEventListener("click", () => el.modal.classList.add("hidden"));
  $("#btn-save-settings").addEventListener("click", saveSettingsForm);
  $("#set-dir-pick").addEventListener("click", async () => {
    const picked = await pickFolder($("#set-dir").value);
    if (picked) $("#set-dir").value = picked;
  });
  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal) el.modal.classList.add("hidden");
  });
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setFilter(btn.dataset.filter));
  });
  el.list.addEventListener("click", onListClick);
}

async function pickFolder(current) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.select_folder) {
    try { return await window.pywebview.api.select_folder(current || ""); } catch (_) {}
  }
  return prompt("输入目录路径", current || "") || "";
}

/* ---------------- 链接探测 ---------------- */

function scheduleProbe() {
  clearTimeout(state.probeTimer);
  const url = el.url.value.trim();
  if (!url) { clearProbe(); return; }
  state.probeTimer = setTimeout(() => probeUrl(url), 650);
}

async function probeUrl(url) {
  const seq = ++state.probeSeq;
  try {
    const info = await api("/api/probe", { method: "POST", body: JSON.stringify({ url }) });
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
  el.probeCard.classList.remove("hidden");
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
  if (info.thumbnail) { el.probeThumb.src = info.thumbnail; }
  else { el.probeThumb.removeAttribute("src"); }
}

function clearProbe() {
  state.probe = null;
  el.probeCard.classList.add("hidden");
  resetQualityOptions();
}

function applyQualityOptions(info) {
  if (info.kind === "file") {
    el.quality.innerHTML = '<option value="0">不适用（直链）</option>';
    el.quality.disabled = true;
    return;
  }
  el.quality.disabled = false;
  const heights = (info.heights || []).filter((h) => h > 0);
  const opts = ['<option value="0">最高画质</option>'];
  for (const h of heights) {
    const label = h >= 2160 ? `${h}P (4K)` : h >= 1440 ? `${h}P (2K)` : `${h}P`;
    opts.push(`<option value="${h}">${label}</option>`);
  }
  el.quality.innerHTML = opts.join("");
  const want = String(state.settings.defaultQuality || 0);
  el.quality.value = heights.includes(+want) ? want : "0";
}

function resetQualityOptions() {
  el.quality.disabled = false;
  el.quality.innerHTML = [
    '<option value="0">最高画质</option>',
    '<option value="2160">4K 2160P</option>',
    '<option value="1440">2K 1440P</option>',
    '<option value="1080">1080P</option>',
    '<option value="720">720P</option>',
    '<option value="480">480P</option>',
    '<option value="360">360P</option>',
  ].join("");
  el.quality.value = String(state.settings.defaultQuality || 0);
}

/* ---------------- 添加任务 ---------------- */

async function addTask() {
  const url = el.url.value.trim();
  if (!url) { el.url.focus(); return; }
  if (state.busy) return;
  state.busy = true;
  el.add.disabled = true;
  try {
    const body = {
      url,
      kind: state.probe && state.probe.kind ? state.probe.kind : "auto",
      quality: Number(el.quality.value) || 0,
      dir: el.dir.value,
      connections: Number(el.conn.value) || 8,
      title: state.probe && state.probe.title ? state.probe.title : "",
    };
    await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
    el.url.value = "";
    clearProbe();
    setFilter("all");
    await refresh();
    toast("已加入下载队列");
  } catch (err) {
    toast("添加失败：" + err.message, true);
  } finally {
    state.busy = false;
    el.add.disabled = false;
  }
}

/* ---------------- 列表渲染 ---------------- */

function setFilter(filter) {
  state.filter = filter;
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.filter === filter);
  });
  const titles = { all: "全部任务", downloading: "下载中", done: "已完成", error: "失败 / 已取消" };
  el.title.textContent = titles[filter] || "全部任务";
  const subs = {
    all: "粘贴链接即可开始，视频会自动选择最佳音视频并合并",
    downloading: "正在下载与排队中的任务",
    done: "已完成的文件",
    error: "失败或已取消的任务，可一键重试",
  };
  $("#page-sub").textContent = subs[filter] || "";
  render();
}

function matchesFilter(t) {
  if (state.filter === "all") return true;
  if (state.filter === "downloading") return ["queued", "downloading", "merging", "paused"].includes(t.status);
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
  } catch (_) { /* 服务未就绪时静默 */ }
}

function render() {
  const counts = { all: 0, downloading: 0, done: 0, error: 0 };
  for (const t of state.tasks) {
    counts.all++;
    if (["queued", "downloading", "merging", "paused"].includes(t.status)) counts.downloading++;
    else if (t.status === "done") counts.done++;
    else if (["error", "cancelled"].includes(t.status)) counts.error++;
  }
  $("#c-all").textContent = counts.all;
  $("#c-downloading").textContent = counts.downloading;
  $("#c-done").textContent = counts.done;
  $("#c-error").textContent = counts.error;

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
    // 保持顺序
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
      <div class="task-top"><span class="task-title"></span><span class="pill"></span></div>
      <div class="task-sub"></div>
      <div class="progress"><i></i></div>
      <div class="task-meta"></div>
    </div>
    <div class="task-actions"></div>`;
  return node;
}

function setText(node, selector, text, key) {
  const target = node.querySelector(selector);
  if (target.dataset.v !== text) { target.textContent = text; target.dataset.v = text; }
}

function updateTaskNode(node, t) {
  node.className = "task " + t.status;

  const thumb = node.querySelector(".thumb");
  const img = node.querySelector(".thumb-img");
  const ico = node.querySelector(".thumb-ico");
  if (t.thumbnail) {
    if (img.dataset.src !== t.thumbnail) { img.src = t.thumbnail; img.dataset.src = t.thumbnail; }
    img.hidden = false; ico.hidden = true;
  } else {
    img.hidden = true; ico.hidden = false;
    const glyph = t.kind === "video" ? "▶" : "▤";
    if (ico.dataset.v !== glyph) { ico.textContent = glyph; ico.dataset.v = glyph; }
  }

  const title = t.title || t.filename || t.url;
  setText(node, ".task-title", title);
  node.querySelector(".task-title").title = title;

  const pill = node.querySelector(".pill");
  pill.className = "pill " + t.status;
  pill.textContent = STATUS_TEXT[t.status] || t.status;

  let sub;
  if (t.status === "done") sub = t.path || t.filename || t.url;
  else sub = t.filename ? `${t.filename}  ·  ${t.dir}` : t.url;
  setText(node, ".task-sub", sub);
  node.querySelector(".task-sub").title = sub;

  const pct = t.status === "done" ? 100 : (t.progress || 0);
  node.querySelector(".progress i").style.width = `${Math.min(100, pct)}%`;

  renderMeta(node, t);
  renderActions(node, t);
}

function renderMeta(node, t) {
  const meta = node.querySelector(".task-meta");
  meta.innerHTML = "";
  const add = (text, cls) => {
    const s = document.createElement("span");
    if (cls) s.className = cls;
    s.textContent = text;
    meta.appendChild(s);
  };

  if (t.status === "error") { add(t.error || "未知错误", "err"); return; }
  if (t.status === "cancelled") { add("已取消"); return; }
  if (t.status === "done") {
    add(fmtBytes(t.size));
    if (t.kind === "file") add(`${t.connections || 8} 线程`);
    add("已保存");
    return;
  }
  if (t.status === "queued") {
    add(t.kind === "video" ? "等待解析" : "等待开始");
    if (t.size) add(fmtBytes(t.size));
    return;
  }
  if (t.status === "paused") {
    add(`${(t.progress || 0).toFixed(1)}%`);
    add(`${fmtBytes(t.downloaded)} / ${fmtBytes(t.size)}`);
    return;
  }
  if (t.status === "merging") { add("正在合并音视频轨…"); return; }

  const pct = t.size ? (t.progress || 0).toFixed(1) : "0.0";
  add(`${pct}%`);
  add(`${fmtBytes(t.downloaded)} / ${fmtBytes(t.size)}`);
  add(fmtSpeed(t.speed));
  if (t.eta) add(`剩余 ${fmtEta(t.eta)}`);
  if (t.kind === "file") add(`${t.connections || 8} 线程`);
}

function renderActions(node, t) {
  const box = node.querySelector(".task-actions");
  const key = t.status;
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.innerHTML = "";

  const btn = (icon, title, act, danger) => {
    const b = document.createElement("button");
    b.textContent = icon;
    b.title = title;
    if (danger) b.className = "danger";
    b.dataset.act = act;
    box.appendChild(b);
  };

  if (["downloading", "merging", "queued"].includes(t.status)) {
    btn("⏸", "暂停", "pause");
    btn("✕", "取消", "cancel", true);
  } else if (t.status === "paused") {
    btn("▶", "继续", "resume");
    btn("✕", "取消", "cancel", true);
  } else if (t.status === "done") {
    btn("📂", "打开文件位置", "open");
    btn("🗑", "从列表移除", "remove", true);
  } else {
    btn("↻", "重试", "retry");
    btn("🗑", "移除", "remove", true);
  }
}

async function onListClick(e) {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".task").dataset.id;
  btn.disabled = true;
  try {
    await api(`/api/tasks/${id}/${btn.dataset.act}`, { method: "POST" });
    await refresh();
  } catch (err) {
    toast("操作失败：" + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- 设置 ---------------- */

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
  el.modal.classList.remove("hidden");
}

async function saveSettings(patch) {
  try {
    state.settings = await api("/api/settings", {
      method: "POST", body: JSON.stringify({ values: patch }),
    });
  } catch (err) {
    toast("保存设置失败：" + err.message, true);
  }
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
  };
  await saveSettings(values);
  el.dir.value = values.defaultDir || el.dir.value;
  el.conn.value = values.defaultConnections;
  el.modal.classList.add("hidden");
  toast("设置已保存");
  refreshTools();
}

async function refreshTools() {
  try {
    const sys = await api("/api/system");
    el.ytdlp.textContent = (sys.tools.ytdlp || "未找到").split(" ")[0];
    el.ffmpeg.textContent = sys.tools.ffmpeg || "未找到";
  } catch (_) {}
}

init();
