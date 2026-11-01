/* Ocemind 内容执行器 — 界面逻辑
   下载器原有的三条链路（加密下载 / 平台视频 / 直链）保持原样，
   执行器能力走 /api/exec，两边共用同一个任务列表。 */

const STATUS_TEXT = {
  queued: "排队中", running: "处理中", downloading: "下载中", merging: "合并中",
  paused: "已暂停", done: "已完成", error: "失败", cancelled: "已取消",
};

const CAP_META = {
  "download.platform": { glyph: "↓", label: "下载视频" },
  "transcribe.asr":    { glyph: "文", label: "转写" },
  "media.transcode":   { glyph: "转", label: "转码" },
  "media.subtitle":    { glyph: "字", label: "字幕" },
  "media.audio":       { glyph: "音", label: "音频后期" },
  "media.thumbnail":   { glyph: "封", label: "抽帧封面" },
  "doc.parse":         { glyph: "档", label: "文档解析" },
  "image.ocr":         { glyph: "识", label: "图片识别" },
  "image.edit":        { glyph: "图", label: "图像处理" },
  "image.generate":    { glyph: "绘", label: "生成图片" },
  "audio.tts":         { glyph: "声", label: "语音合成" },
  "audio.voiceclone":  { glyph: "仿", label: "声音克隆" },
  "publish.browser":   { glyph: "发", label: "发布" },
};

/* 发布记录的状态。后端 publish/states.py 是同一套词，改一边记得改另一边。 */
const PUB_STATE = {
  draft: "待确认", approved: "已批准", running: "发布中", verified: "已确认",
  reviewing: "审核中", manual: "需人工", scheduled: "定时待发", failed: "失败",
};

/* 动作条上的短名。全部两个字，界面上只出现这套。 */
const VERB_LABEL = {
  "download.platform": "下载", "transcribe.asr": "转写", "media.transcode": "转码",
  "media.thumbnail": "截图", "image.ocr": "识别", "doc.parse": "解析",
  "image.generate": "生成", "image.edit": "修图", "audio.tts": "配音",
  "audio.voiceclone": "克隆", "media.subtitle": "字幕", "media.audio": "音频",
  "publish.browser": "发布",
};

/* 按输入类型排的候选动作，第一个是默认。
   不问「你想干嘛」，直接给最可能是的那几个。 */
const VERB_TABLE = {
  url:   ["download.platform", "transcribe.asr", "media.thumbnail", "doc.parse"],
  video: ["transcribe.asr", "media.transcode", "media.thumbnail", "publish.browser"],
  audio: ["transcribe.asr", "media.transcode", "audio.tts", "publish.browser"],
  image: ["image.ocr", "image.generate", "image.edit", "publish.browser"],
  doc:   ["doc.parse", "transcribe.asr", "publish.browser"],
  text:  ["image.generate", "audio.tts"],
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
  view: "tools",
  tool: "",
  section: "storage",
  activeAccount: "",
  acctMode: "single",
  acctPane: "start",   // start = 新标签页，manage = 账号管理，browser = 某个账号
  deleting: "",        // 已经在「再点一次就删」状态的账号
  frameSeen: false,    // 这个账号这次打开后，有没有收到过第一帧
  publish: [],
  pubTouched: false,
  nodes: new Map(),          // 任务页那棵树
  secureNodes: new Map(),    // 爆破下载页那棵树（两页各自一份，同一任务不会互抢节点）
  probe: null,
  busy: false,
  probeTimer: null,
  probeSeq: 0,
  loginTimer: null,
  frameTimer: null,
  frameOn: false,      // 画面轮询开着没有
  frameSeq: {},        // 每个账号手上那一帧的序号，用来问「有没有新的」
  stageSize: "",       // 已经同步给浏览器的视口尺寸
  gridKey: "",         // 多开格子里现在摆的是哪几个账号
  wheelBuf: null,      // 滚轮攒一下再发，触控板一次能刷几十个事件
  keyQueue: null,      // 按键按顺序发，不然「ab」会变成「ba」
  hintTimer: null,
  publishDraft: null,
  accountPlatform: "bilibili",
  connTab: "mcp",
  secureProbe: null,
  models: null,
  localInstallTimer: null,
  artifacts: [],
  artifactStatus: "all",
  artifactProject: "",
  claiming: "",   // 正在认领的产出 id（行内展开一个小表单）
  feishu: null,
  appVersion: "",
  home: "",
  // 默认亮色；用户点过就记住，下次打开还是那个
  theme: localStorage.getItem("ocemind.theme") === "dark" ? "dark" : "light",
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const el = {
  url: $("#url-input"), capSelect: $("#cap-select"), add: $("#btn-add"),
  browse: $("#btn-browse"), dir: $("#dir-input"),
  pickDir: $("#btn-dir"), quality: $("#quality-select"), conn: $("#conn-input"),
  srt: $("#srt-check"), mode: $("#mode-select"), extra: $("#extra-fields"),
  list: $("#task-list"), empty: $("#empty"),
  secureList: $("#secure-task-list"), secureEmpty: $("#secure-empty"),
  toast: $("#toast"),
  card: $("#add-card"), verbBar: $("#verb-bar"),
  optsWrap: $("#opts-wrap"),
  probeCard: $("#probe-card"), probeThumb: $("#probe-thumb"),
  probeTitle: $("#probe-title"), probeMeta: $("#probe-meta"),
  probeLogin: $("#probe-login"),
  dupCard: $("#dup-card"), dupText: $("#dup-text"), dupBtn: $("#dup-btn"),
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

/* 错误说人话：先给一句能看懂的，再告诉下一步。原文留在 title 里。 */
/* 下载失败的原因翻成人话。顺序有讲究：先具体的（验证码/会员/地区），
   再宽泛的（403、网络），否则「403」会把「会员专享」吃掉。
   原始错误不丢，挂在行上的 title 里，鼠标停住就能看到。 */
const ERROR_HINTS = [
  [/sign in to confirm|not a bot|captcha|人机验证|验证码/i,
    "触发了人机验证：去「账号」里用浏览器登录一次再重试"],
  [/members-only|members on level|channel'?s members|join this channel|premium|subscription|会员|付费/i,
    "会员 / 付费内容，当前登录态没有权限"],
  [/private video|私密|仅好友|仅自己可见/i,
    "私密内容，这个账号没有访问权"],
  [/(not )?(been )?made this video available in your country|not available in your country|geo.?restrict|地区限制|仅限.{0,8}(大陆|中国)/i,
    "地区限制：这条要走对应地区的网络才能下"],
  [/429|too many requests|rate limit|频繁|限流|风控/i,
    "请求太频繁被限流了，等几分钟再试"],
  [/video unavailable|has been removed|已删除|不存在|404/i,
    "视频不存在或已删除，链接可能已经过期"],
  [/403|forbidden/i,
    "被站点拒绝了：多半是登录态过期或缺少 cookie"],
  [/401|unauthorized|login required|请先登录|未登录/i,
    "登录态过期了，去「账号」里重新登录一次"],
  [/unsupported url|no suitable extractor|不支持/i,
    "这个站点还下不了：先把文件保存到本地，再拖进来"],
  [/requested format|format is not available|清晰度/i,
    "要的清晰度没有，换一档再试"],
  [/no such file|not found|找不到|does not exist/i,
    "文件不在了，确认路径还在"],
  [/permission denied|access is denied|权限/i,
    "没有读写权限，换个目录试试"],
  [/no space left|空间不足|disk full/i,
    "磁盘满了，先清点空间"],
  [/timed? ?out|timeout|超时/i,
    "等太久了，网络可能不通"],
  [/connection|network|resolve|dns|网络/i,
    "网络没通，检查代理或稍后重试"],
  [/codec|decoder|invalid data|解不了/i,
    "这个格式解不了，先转成 MP4 / H.264"],
  [/ffmpeg/i,
    "转码没成功，文件可能损坏"],
];

function humanError(msg) {
  const raw = msg || "未知错误";
  for (const [re, hint] of ERROR_HINTS) if (re.test(raw)) return hint;
  return raw.length > 60 ? raw.slice(0, 60) + "…" : raw;
}

/* 原始错误常常是几十行 yt-dlp 日志。悬停提示只要最后一条有信息量的，
   太长就截断——完整内容留给日志文件。 */
function rawError(msg) {
  const raw = (msg || "").trim();
  if (!raw) return "";
  const lines = raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const line = [...lines].reverse().find((l) => !/^\[debug\]/i.test(l)) || raw;
  return line.length > 300 ? line.slice(0, 300) + "…" : line;
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
  applyTheme(state.theme);
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
  refreshEntry();

  const direct = (location.hash || "").replace("#", "");
  if (["tools", "tasks", "secure", "artifacts", "settings", "accounts", "about"].includes(direct)) {
    setView(direct);
  } else {
    renderToolsHome();
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
    state.home = sys.home || "";
    el.dir.value = sys.settings.defaultDir || "";
    el.conn.value = sys.settings.defaultConnections || 8;
    el.quality.value = String(sys.settings.defaultQuality || 0);
    // 版本号后面标一下来源：本机装的就用本机的，没有再退回包里自带的
    const mark = (source) =>
      source === "system" ? " · 本机" : source === "settings" ? " · 指定" :
      source === "bundled" ? " · 自带" : "";
    $("#tool-ytdlp").textContent = (sys.tools.ytdlp || "未找到").split(" ")[0]
      + mark(sys.tools.ytdlp_source);
    $("#tool-ytdlp").title = sys.tools.ytdlp_cmd || "";
    $("#tool-ffmpeg").textContent = (sys.tools.ffmpeg || "未找到")
      + mark(sys.tools.ffmpeg_source);
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
    const version = data.app && data.app.version;
    if (version) {
      state.appVersion = version;
      // 窗口标题就是「内容执行器 + 版本号」，例如「内容执行器 1.1.0」；
      // 侧栏那块 LOGO 只放图标和名字，版本号由标题栏和说明页负责
      document.title = `内容执行器 ${version}`;
    }
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
    // 用量不占页头，跟着「月度预算」那一行显示
    const modelSpent = $("#model-spent");
    if (modelSpent) {
      modelSpent.textContent = `本月已用 ¥${u.spent.toFixed(2)}`;
      modelSpent.classList.toggle("over", limit && u.spent >= u.budget);
    }
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
  // 「待认领」是另一个维度：没项目的产出，跟同步状态无关
  if (state.artifactStatus === "ungrouped") {
    query.set("ungrouped", "1");   // 待认领 = 没项目，再叠项目筛选只会筛出空
  } else if (state.artifactStatus !== "all") {
    query.set("status", state.artifactStatus);
  }
  if (state.artifactProject && state.artifactStatus !== "ungrouped") {
    query.set("project", state.artifactProject);
  }
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
    const claimBtn = a.project ? "" :
      `<button class="btn ghost small a-claim" data-id="${esc(a.id)}">认领</button>`;
    const claimForm = state.claiming === a.id ? `
      <div class="claim-bar" data-id="${esc(a.id)}">
        <input class="claim-proj" list="project-list" placeholder="归到哪个项目">
        <input class="claim-seq" placeholder="序列（可选）">
        <button class="btn small claim-ok">归档</button>
        <button class="btn ghost small claim-cancel">取消</button>
      </div>` : "";
    return `<div class="artifact">
      <div class="a-glyph">${ARTIFACT_GLYPH[a.file_kind] || "•"}</div>
      <div class="a-main">
        <div class="a-meta">${meta}</div>
        <div class="a-title" title="${esc(a.title)}">${esc(a.title)}</div>
        <div class="a-sub" title="${esc(a.path)}">${esc(a.path)}</div>
        ${error}
        ${claimForm}
      </div>
      <div class="a-side">
        <span class="a-chip ${esc(status)}">${SYNC_TEXT[status] || status}</span>
        ${remote}
        ${claimBtn}
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

async function claimArtifact(id, project, sequence) {
  const name = (project || "").trim();
  if (!name) { toast("先填项目名", true); return; }
  try {
    await api("/api/exec/artifacts/claim", {
      method: "POST",
      body: JSON.stringify({ ids: [id], project: name, sequence: (sequence || "").trim() }),
    });
    state.claiming = "";
    toast(`已归到「${name}」`);
    await refreshArtifacts();
  } catch (err) {
    toast("归档失败：" + err.message, true);
  }
}

async function backfillArtifacts() {
  try {
    const res = await api("/api/exec/artifacts/index", { method: "POST" });
    toast(`补录 ${res.indexed} 条产出`);
    await refreshArtifacts();
  } catch (err) {
    toast("补录失败：" + err.message, true);
  }
}

/* ───────────────────────── 事件 ───────────────────────── */

function bindEvents() {
  $$(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.sec) {
        state.section = btn.dataset.sec;
        setView("settings");
        return;
      }
      state.section = "";
      setView(btn.dataset.view);
    });
  });
  $$("#task-filter button, #secure-filter button").forEach((btn) => {
    btn.addEventListener("click", () => setFilter(btn.dataset.filter));
  });

  el.add.addEventListener("click", addTask);
  el.url.addEventListener("keydown", (e) => { if (e.key === "Enter") addTask(); });
  el.url.addEventListener("input", () => { refreshEntry(); scheduleProbe(); scheduleDetect(); });
  el.capSelect.addEventListener("change", () => { renderExtra(); scheduleProbe(); });
  // 焦点不在输入框里时也能 Ctrl+V：省得先点一下输入框
  document.addEventListener("paste", onWindowPaste);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".verb-bar")) $("#verb-menu")?.classList.add("hidden");
  });
  el.browse.addEventListener("click", pickFile);
  el.pickDir.addEventListener("click", pickDirectory);
  el.probeLogin.addEventListener("click", startCourseLogin);
  el.dupBtn.addEventListener("click", onDupAction);
  el.list.addEventListener("click", onListClick);
  el.secureList?.addEventListener("click", onListClick);

  $("#btn-save-settings").addEventListener("click", saveSettingsForm);
  $("#ytdlp-check").addEventListener("click", () =>
    ($("#ytdlp-check").dataset.mode === "update" ? onYtdlpUpdate() : onYtdlpCheck()));
  $("#ytdlp-revert").addEventListener("click", onYtdlpRevert);
  $("#gc-clean").addEventListener("click", () => cleanGc(false));
  $("#gc-orphans").addEventListener("click", () => cleanGc(true));
  $("#set-dir-pick").addEventListener("click", async () => {
    const picked = await pickFolder($("#set-dir").value);
    if (picked) { $("#set-dir").value = picked; el.dir.value = picked; }
  });
  $("#btn-tool-back").addEventListener("click", () => setView("tools"));
  // 底部这颗「设置」就是通用设置那一页，点它直接落到那一页
  $("#btn-settings").addEventListener("click", () => {
    state.section = "general";
    setView("settings");
  });
  $("#btn-about").addEventListener("click", () => setView("about"));
  $("#btn-about-home").addEventListener("click", () => setView("tools"));
  $("#btn-theme").addEventListener("click", toggleTheme);
  // 不能自更新的形态（源码跑、便携版）也留着这颗按钮，
  // 点了至少告诉人为什么没动静，而不是一片死寂
  if (!window.__OCEMIND_UPDATER__) {
    $("#btn-update").addEventListener("click", () =>
      toast("这个版本不走自动更新：源码运行或便携版，拉代码 / 下安装包"));
  }
  // 从爆破下载回执行页：它自己没有执行页，退回去就回工具首页。
  // 不然会停在一张标题写着「爆破下载」、底下却是贴链接表单的页上。
  $("#btn-back-exec").addEventListener("click", () => {
    const tool = TOOLS.find((t) => t.id === state.tool);
    if (tool && tool.cap === "__course") { state.tool = ""; setView("tools"); return; }
    setView("tasks");
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
    // 选了具体项目，就没必要再卡在「待认领」上——那两件事互斥
    if (state.artifactProject && state.artifactStatus === "ungrouped") {
      state.artifactStatus = "all";
      $$("#artifact-filter button").forEach((b) =>
        b.classList.toggle("on", b.dataset.status === "all"));
    }
    refreshArtifacts();
  });
  $("#artifact-sync").addEventListener("click", () => syncArtifacts({}));
  $("#artifact-index").addEventListener("click", backfillArtifacts);
  $("#artifact-list").addEventListener("click", (e) => {
    const sync = e.target.closest(".a-sync");
    if (sync) { syncArtifacts({ ids: [sync.dataset.id] }); return; }

    const claim = e.target.closest(".a-claim");
    if (claim) {
      state.claiming = claim.dataset.id;
      renderArtifacts();
      $(`.claim-bar[data-id="${state.claiming}"] .claim-proj`)?.focus();
      return;
    }

    const cancel = e.target.closest(".claim-cancel");
    if (cancel) {
      state.claiming = "";
      renderArtifacts();
      return;
    }

    const ok = e.target.closest(".claim-ok");
    if (ok) {
      const bar = ok.closest(".claim-bar");
      claimArtifact(bar.dataset.id,
        bar.querySelector(".claim-proj").value,
        bar.querySelector(".claim-seq").value);
    }
  });

  $("#artifact-list").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.closest(".claim-bar")) {
      const bar = e.target.closest(".claim-bar");
      claimArtifact(bar.dataset.id,
        bar.querySelector(".claim-proj").value,
        bar.querySelector(".claim-seq").value);
    } else if (e.key === "Escape" && e.target.closest(".claim-bar")) {
      state.claiming = "";
      renderArtifacts();
    }
  });

  // ---- 模型接口 ----
  $("#btn-save-budget").addEventListener("click", async () => {
    await saveSettings({ monthlyBudget: Number($("#set-budget").value) || 0 });
    toast("预算已保存");
    refreshUsage();
  });

  // 账号相关都在页子里完成，不再开弹窗
  $("#btn-new-account").addEventListener("click", openNewAccountForm);
  $("#btn-cancel-account").addEventListener("click", closeNewAccountForm);
  $("#new-acct-form").addEventListener("submit", (e) => { e.preventDefault(); createAccount(); });
  $("#btn-check-all").addEventListener("click", checkAllAccounts);

  $("#btn-browser-go").addEventListener("click", () => {
    const url = $("#browser-url").value.trim();
    if (state.activeAccount && url) accountAction({ kind: "goto", url });
  });
  $("#browser-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#btn-browser-go").click();
  });
  $("#acct-mode").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (btn) setAccountMode(btn.dataset.mode);
  });
  $("#tabstrip").addEventListener("click", (e) => {
    const close = e.target.closest("[data-tab-close]");
    if (close) { closeAccount(close.dataset.tabClose); return; }
    const pane = e.target.closest("[data-pane]");
    if (pane) { showPane(pane.dataset.pane); return; }
    const tab = e.target.closest("[data-tab]");
    if (tab) selectAccount(tab.dataset.tab, false);
  });
  $("#browser-grid").addEventListener("click", (e) => {
    const card = e.target.closest("[data-tile]");
    if (card) { setAccountMode("single"); selectAccount(card.dataset.tile, false); }
  });
  $("#pub-toggle").addEventListener("click", togglePublish);
  $("#btn-browser-check").addEventListener("click", checkAccount);
  bindStage();

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

/* ───────────────────── 工具助手：首页的工具卡 ─────────────────────
   一个工具一张卡，按用途分类。常用工具是置顶的那一行，下面的分类里
   还会再出现一次——置顶是快捷方式，不是另一份工具。 */

const TOOL_GROUPS = [
  { key: "hot", name: "常用工具" },
  { key: "download", name: "下载" },
  { key: "text", name: "转文字" },
  { key: "media", name: "媒体处理" },
  { key: "gen", name: "生成与编辑" },
  { key: "publish", name: "发布" },
  { key: "planned", name: "规划中" },
];

/* 图标跟侧栏是同一种画法：20×20 的框、细线、不填色。一个个写着「文」
   「字」「档」的方块看着像标签，不像工具。 */
const TOOLS = [
  { id: "download", name: "视频下载", group: "download", hot: true,
    cap: "download.platform", desc: "粘链接，自动认平台，选好清晰度存到本地",
    icon: `<path d="M10 3.5v8.3"/><path d="M6.3 8.5 10 12.2l3.7-3.7"/><path d="M3.6 14.2v1.2c0 .9.7 1.6 1.6 1.6h9.6c.9 0 1.6-.7 1.6-1.6v-1.2"/>` },
  { id: "course", name: "爆破下载", group: "download", hot: true,
    cap: "__course", desc: "登录课程账号，把一个系列的录播整批拉下来",
    icon: `<path d="M11.4 3.4 5.6 11.2h3.8l-.8 5.4 5.8-7.8h-3.8z"/>` },
  { id: "asr", name: "视频转文本", group: "text", hot: true,
    cap: "transcribe.asr", desc: "视频或音频转成带时间码的文字稿",
    icon: `<rect x="7.4" y="3.2" width="5.2" height="8.2" rx="2.6"/><path d="M4.8 9.4a5.2 5.2 0 0 0 10.4 0"/><path d="M10 14.6v2.2"/>` },
  { id: "subtitle", name: "生成字幕", group: "text", hot: true,
    cap: "media.subtitle", desc: "识别语音出字幕文件，也可以压进视频",
    icon: `<rect x="3" y="4.5" width="14" height="11" rx="2"/><path d="M6 10.2h3.4M11.4 10.2h2.6M6 13.2h2.6M10.4 13.2h3.6"/>` },
  { id: "doc", name: "文档解析", group: "text",
    cap: "doc.parse", desc: "PDF、Word 抽出正文，转成可编辑文本",
    icon: `<path d="M6 3.2h5l3.4 3.4v9.2c0 .6-.4 1-1 1H6c-.6 0-1-.4-1-1V4.2c0-.6.4-1 1-1z"/><path d="M11 3.4v3.2h3.2"/><path d="M7.4 10h5.2M7.4 12.8h5.2"/>` },
  { id: "ocr", name: "图片识字", group: "text", hot: true,
    cap: "image.ocr", desc: "截图里的文字提出来，中英混排也认",
    icon: `<path d="M3.6 7.2V5.4c0-1 .8-1.8 1.8-1.8h1.8M12.8 3.6h1.8c1 0 1.8.8 1.8 1.8v1.8M16.4 12.8v1.8c0 1-.8 1.8-1.8 1.8h-1.8M7.2 16.4H5.4c-1 0-1.8-.8-1.8-1.8v-1.8"/><path d="M7 8h6M10 8v5"/>` },
  { id: "transcode", name: "转码压缩", group: "media", hot: true,
    cap: "media.transcode", desc: "换容器、压体积、剪一段，优先不重编码",
    icon: `<path d="M4.2 8.6A6 6 0 0 1 15 6.4"/><path d="M15.2 3.8v3h-3"/><path d="M15.8 11.4A6 6 0 0 1 5 13.6"/><path d="M4.8 16.2v-3h3"/>` },
  { id: "audio", name: "音频处理", group: "media", hot: true,
    cap: "media.audio", desc: "抽出音轨、调响度、去底噪",
    icon: `<path d="M3.6 9.3v1.4M6.6 6.4v7.2M9.8 4.4v11.2M13 7.2v5.6M16.2 9v2"/>` },
  { id: "thumb", name: "抽帧封面", group: "media", hot: true,
    cap: "media.thumbnail", desc: "按时间点抽一帧，当封面用",
    icon: `<rect x="3" y="4.5" width="14" height="11" rx="1.8"/><circle cx="7.2" cy="8.1" r="1.1"/><path d="M4.3 14 8.6 9.7l2.5 2.5 1.7-1.7 2.9 2.9"/>` },
  { id: "genimg", name: "生成图片", group: "gen",
    cap: "image.generate", desc: "按一段描述出图",
    icon: `<rect x="3" y="5.6" width="11.4" height="10.4" rx="1.8"/><path d="M5.2 13.6 8.9 9.9l2.2 2.2"/><path d="M15.6 2.8v3.6M13.8 4.6h3.6"/>` },
  { id: "editimg", name: "图像处理", group: "gen",
    cap: "image.edit", desc: "改尺寸、裁切、批量处理",
    icon: `<path d="M6.2 2.8v10.4a1 1 0 0 0 1 1h10.4"/><path d="M2.8 6.2h10.4a1 1 0 0 1 1 1v10.4"/>` },
  { id: "tts", name: "语音合成", group: "gen",
    cap: "audio.tts", desc: "文字转成配音",
    icon: `<path d="M4 8h2.6L10.4 4.8v10.4L6.6 12H4z"/><path d="M12.8 7.4a3.6 3.6 0 0 1 0 5.2M15 5.4a6.4 6.4 0 0 1 0 9.2"/>` },
  { id: "clone", name: "声音克隆", group: "gen",
    cap: "audio.voiceclone", desc: "用一段样本复刻音色",
    icon: `<rect x="3.2" y="3.2" width="9.2" height="9.2" rx="2"/><rect x="7.6" y="7.6" width="9.2" height="9.2" rx="2"/>` },
  { id: "publish", name: "一键发布", group: "publish", hot: true,
    cap: "publish.browser", desc: "把成片发到渠道账号，标题话题自动填",
    icon: `<path d="M16.6 3.4 3.4 8.9l4.9 2.1 2.1 4.9z"/><path d="M16.6 3.4 8.5 11.2"/>` },
  { id: "rewrite", name: "文案改写", group: "planned", planned: true,
    desc: "一段文案按各平台调性改写",
    icon: `<path d="M12.4 4.1 15.9 7.6 7.6 15.9l-3.8.7.7-3.8z"/><path d="M11 5.5 14.5 9"/>` },
  { id: "title", name: "标题生成", group: "planned", planned: true,
    desc: "根据内容出几个备选标题",
    icon: `<path d="M4.4 6V4.2h11.2V6"/><path d="M10 4.2v11.6"/><path d="M7.4 15.8h5.2"/>` },
  { id: "breakdown", name: "视频拆解", group: "planned", planned: true,
    desc: "拆出分镜脚本和关键信息",
    icon: `<circle cx="6.2" cy="6.2" r="2"/><circle cx="6.2" cy="13.8" r="2"/><path d="M7.8 7.4 16 14.4M7.8 12.6 16 5.6"/>` },
  { id: "translate", name: "翻译", group: "planned", planned: true,
    desc: "字幕和文案整批翻译",
    icon: `<circle cx="10" cy="10" r="6.4"/><path d="M3.6 10h12.8"/><path d="M10 3.6c2.4 2.5 2.4 10.3 0 12.8-2.4-2.5-2.4-10.3 0-12.8z"/>` },
];

function toolIcon(tool, cls) {
  return `<svg viewBox="0 0 20 20" class="${cls}" aria-hidden="true">${tool.icon}</svg>`;
}

function toolCard(tool) {
  return `<button class="tool-card${tool.planned ? " planned" : ""}"
      ${tool.planned ? "disabled" : `data-tool="${tool.id}"`}>
    <span class="tool-icon">${toolIcon(tool, "ti")}</span>
    <span class="tool-name">${esc(tool.name)}</span>
    <span class="tool-desc">${esc(tool.desc)}</span>
    <span class="tool-go">${tool.planned ? "还没做" : "开始使用"}</span>
  </button>`;
}

/** 分类索引里的一行：小图标 + 名字。只用来认路，点它就进那个工具 */
function toolLink(tool) {
  return `<button class="tool-link${tool.planned ? " planned" : ""}"
      ${tool.planned ? "disabled" : `data-tool="${tool.id}"`}>
    ${toolIcon(tool, "ti")}<span>${esc(tool.name)}</span>
  </button>`;
}

function renderToolsHome() {
  const home = $("#tools-home");
  if (!home) return;
  const hot = TOOLS.filter((t) => t.hot);
  const groups = TOOL_GROUPS.filter((g) => g.key !== "hot");
  home.innerHTML = `
    <section class="tool-group tool-hot">
      <h2 class="tool-group-title">常用工具<span class="tool-group-n">${hot.length}</span></h2>
      <div class="tool-grid">${hot.map(toolCard).join("")}</div>
    </section>
    <section class="tool-group tool-index">
      <h2 class="tool-group-title">按分类浏览<span class="tool-group-n">${TOOLS.length}</span></h2>
      <div class="tool-cols">
        ${groups.map((g) => {
          const list = TOOLS.filter((t) => t.group === g.key);
          if (!list.length) return "";
          return `<div class="tool-col">
            <div class="tool-col-title">${g.name}<span class="tool-group-n">${list.length}</span></div>
            ${list.map(toolLink).join("")}
          </div>`;
        }).join("")}
      </div>
    </section>`;
  home.querySelectorAll("[data-tool]").forEach((card) => {
    card.addEventListener("click", () => openTool(card.dataset.tool));
  });
}

/** 工具页左边的同类工具：跟首页分组一致，当前这个高亮。
    普通工具页和爆破下载页各有一列，画的是同一份，一起填。 */
function renderToolSide(currentId) {
  const sides = $$(".tool-side");
  if (!sides.length) return;
  const body = TOOL_GROUPS
    .filter((g) => g.key !== "hot" && g.key !== "planned")
    .map((group) => {
      const list = TOOLS.filter((t) => t.group === group.key && !t.planned);
      if (!list.length) return "";
      return `<div class="side-group">
        <div class="side-group-title">${group.name}</div>
        ${list.map((t) => `<button class="side-tool${t.id === currentId ? " on" : ""}"
            data-side="${t.id}"${t.id === currentId ? ' aria-current="page"' : ""}>
          ${toolIcon(t, "ti")}<span>${esc(t.name)}</span>
        </button>`).join("")}
      </div>`;
    }).join("");
  for (const side of sides) {
    side.innerHTML = body;
    side.querySelectorAll("[data-side]").forEach((btn) => {
      btn.addEventListener("click", () => openTool(btn.dataset.side));
    });
  }
}

function openTool(toolId) {
  const tool = TOOLS.find((t) => t.id === toolId);
  if (!tool || tool.planned) return;
  state.tool = toolId;
  $("#task-title").textContent = tool.name;
  $("#task-sub").textContent = tool.desc;
  renderToolSide(toolId);
  setView("tasks");
  // 爆破下载是单独一页（右边换成它自己的三块内容），左边那列工具照旧
  if (tool.cap === "__course") { setView("secure"); return; }
  if (tool.cap && [...el.capSelect.options].some((o) => o.value === tool.cap)) {
    el.capSelect.value = tool.cap;
    renderExtra();
  }
  // 动作条和参数是常驻的：换工具就把这条摆成这个工具自己的动作；
  // 任务列表当场重画，不用等下一轮轮询，免得先看到上个工具的活儿
  refreshEntry();
  render();
  el.url.focus();
}

/* ── 深浅色 ──────────────────────────────────────────────────
   深色主题还没做：现在切换只记住选择、把 data-theme 挂到 <html> 上。
   等深色那套样式写好了，往 [data-theme="dark"] 里加就行，这里不用动。 */
function applyTheme(mode) {
  const dark = mode === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const label = $("#theme-label");
  if (label) label.textContent = dark ? "深色" : "亮色";
  const btn = $("#btn-theme");
  if (btn) btn.title = dark ? "切换到亮色" : "切换到深色";
}

function toggleTheme() {
  state.theme = state.theme === "dark" ? "light" : "dark";
  try {
    localStorage.setItem("ocemind.theme", state.theme);
  } catch (err) { /* 隐私模式下存不了，那就只在这次会话里生效 */ }
  applyTheme(state.theme);
}

function setView(view) {
  // 旧书签 / 旧链接里的 models、connect 都并到「设置」了
  if (view === "models" || view === "connect") view = "settings";
  state.view = view;
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${view}`));
  // 设置下面分成几页，进来先定好显示哪一页
  if (view === "settings") showSettingsSection(state.section);
  // 单个工具页也算在「工具助手」里，导航别跳空
  const navView = view === "tasks" ? "tools" : view;
  $$(".nav-item").forEach((b) => b.classList.toggle(
    "active", b.dataset.view === navView
      && (navView !== "settings" || b.dataset.sec === state.section)));
  // 底部那三颗：通用设置 / 指南 打开时亮一下，深浅色那颗跟视图无关
  $$(".foot-act").forEach((b) => {
    const on = b.id === "btn-settings" ? (view === "settings" && state.section === "general")
      : b.id === "btn-about" ? view === "about" : false;
    b.classList.toggle("active", on);
  });
  if (view === "tools") renderToolsHome();
  if (view === "accounts") { refreshAccounts(); startFrames(); }
  else stopFrames();
  if (view === "secure") {
    // 直接开 #secure（刷新 / 书签）时左边那列也得有内容，默认停在爆破下载上
    if (!TOOLS.some((t) => t.id === state.tool && t.cap === "__course")) {
      state.tool = "course";
      renderToolSide("course");
    }
    refreshCourseAuth();
  }
  if (view === "about") fillAbout();
  if (view === "artifacts") refreshArtifacts();
  if (view === "settings") {
    fillSettings();
    loadModels();
    refreshUsage();
    loadConnect();
    renderConnCode();
    refreshFeishu();
    renderCapList();
  }
}

/** 指南页里的路径都是真的，不从文档里抄 */
function fillAbout() {
  const set = (sel, text) => { const n = $(sel); if (n) n.textContent = text || "—"; };
  set("#about-version", state.appVersion);
  set("#about-home", state.home);
  set("#about-out", state.settings.defaultDir || el.dir.value);
}

/* 设置下面这几页各管各的：只显示点中的那一页，标题跟着换，不往下滚 */
const SET_PAGES = {
  storage: { title: "存储配置", sub: "产出落在哪、开几个线程——配一次就够了" },
  engine: { title: "驱动引擎", sub: "每个能力单独选：走本地引擎，还是走 API" },
  api: { title: "API 服务商", sub: "只填你要用的那几家，密钥只存在本机" },
  connect: { title: "接入 AI", sub: "让 Agent 调用这个执行器：API / MCP / CLI 同一条路" },
  general: { title: "通用设置", sub: "本机工具、代理、协同——不常用，出事最先看这儿" },
};

function showSettingsSection(section) {
  const key = SET_PAGES[section] ? section : "storage";
  state.section = key;
  $$("#view-settings .set-page").forEach((p) =>
    p.classList.toggle("on", p.dataset.page === key));
  const meta = SET_PAGES[key];
  const title = $("#set-title");
  if (title) title.textContent = meta.title;
  const sub = $("#set-sub");
  if (sub) sub.textContent = meta.sub;
  // 换页就回到顶上，别停在上一页滚到的地方
  const main = $("#view-settings").closest(".main");
  if (main) main.scrollTop = 0;
}

function setFilter(filter) {
  state.filter = filter;
  // 两个工具页各有一条筛子，选的是同一个数：在哪边点，另一边跟着亮
  $$("#task-filter button, #secure-filter button").forEach(
    (b) => b.classList.toggle("on", b.dataset.filter === filter));
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

/* ───────────────────────── 接活区：动作推断 ───────────────────────── */

function inputKind(text) {
  if (!text) return "";
  if (isUrl(text)) return "url";
  if (VIDEO_EXT.test(text)) return "video";
  if (AUDIO_EXT.test(text)) return "audio";
  if (IMG_EXT.test(text)) return "image";
  if (DOC_EXT.test(text)) return "doc";
  return "text";
}

/** 输入一变就重排：动作条和参数常驻，链接空着时按这个工具自己的动作摆 */
function refreshEntry() {
  const text = el.url.value.trim();
  const has = !!text;
  el.card.classList.toggle("is-empty", !has);
  if (!has) {
    // 还没粘东西：就先摆这个工具本来的那个动作，点开就知道自己是来干嘛的
    const tool = TOOLS.find((t) => t.id === state.tool);
    el.capSelect.value = (tool && tool.cap && tool.cap !== "__course") ? tool.cap : "auto";
    renderVerbBar(null);
    return;
  }
  const kind = inputKind(text);
  const auto = detectCapability(text);
  const list = VERB_TABLE[kind] || [];
  if (!list.includes(el.capSelect.value)) {
    el.capSelect.value = list.includes(auto) ? auto : (list[0] || "auto");
  }
  renderVerbBar(kind);
}

/** 工具自己的那个动作：输入框空着时动作条就摆它，别只剩一颗「更多」 */
function toolVerbs() {
  const tool = TOOLS.find((t) => t.id === state.tool);
  return (tool && tool.cap && tool.cap !== "__course") ? [tool.cap] : [];
}

function renderVerbBar(kind) {
  const base = kind ? (VERB_TABLE[kind] || []) : toolVerbs();
  const active = el.capSelect.value;
  // 从「更多」里挑的动作要留在条上，不然选完看不见自己选了啥
  const list = base.slice();
  if (active && active !== "auto" && VERB_LABEL[active] && !list.includes(active)) list.push(active);
  const rest = Object.keys(VERB_LABEL).filter((cap) => !list.includes(cap));
  const chip = (cap) => `<button class="verb${cap === active ? " on" : ""}"
      data-cap="${cap}">${VERB_LABEL[cap] || cap}</button>`;

  el.verbBar.innerHTML =
    list.map(chip).join("") +
    `<button class="verb more" id="verb-more">更多 ▾</button>` +
    `<div class="verb-menu hidden" id="verb-menu">${rest.map(chip).join("")}</div>`;

  el.verbBar.querySelectorAll("button[data-cap]").forEach((btn) => {
    btn.addEventListener("click", () => {
      el.capSelect.value = btn.dataset.cap;
      renderVerbBar(kind);
      renderExtra();
      scheduleProbe();
    });
  });
  $("#verb-more")?.addEventListener("click", (e) => {
    e.stopPropagation();
    $("#verb-menu")?.classList.toggle("hidden");
  });
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
  const hint = $("#opts-hint");
  if (hint) {
    hint.textContent = isDownload
      ? "画质和线程对下载有用，其他动作会换成各自的选项"
      : "只列这个动作会用到的项，其余用默认值";
  }
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
  const notDownload = !text || !isUrl(text) || currentCapability() !== "download.platform";
  if (notDownload) { clearProbe(); renderDup(null); return; }
  // 探测要等网络，重复提示不等——任务列表本来就在手里
  renderDup(findDuplicate(text));
  state.probeTimer = setTimeout(() => probeUrl(text), 650);
}

async function probeUrl(url) {
  const seq = ++state.probeSeq;
  try {
    const info = await api("/api/probe", {
      method: "POST", body: JSON.stringify({ url: extractUrl(url) }),
    });
    if (seq !== state.probeSeq) return;
    if (!info.ok) { renderProbeError(info.error); return; }
    state.probe = info;
    renderProbe(info);
    applyQualityOptions(info);
  } catch (err) {
    if (seq === state.probeSeq) renderProbeError(err.message);
  }
}

function renderProbe(info) {
  if (currentCapability() !== "download.platform") { clearProbe(); return; }
  el.probeCard.classList.remove("hidden");
  if (info.kind === "course") {
    el.probeTitle.textContent = "爆破下载";
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

/* ── 重复任务：同一条链接又粘了一次 ───────────────────────────
   抖音/B站分享文案里带一堆跟踪参数，直接比字符串永远不相等；这里只留
   认得出视频的那几个参数，再去掉 spm、share_* 这类尾巴。 */

const TRACK_PARAMS = /^(spm|spm_id_from|vd_source|from_source|share_source|share_medium|share_plat|share_tag|share_from|share_session_id|unique_k|unique_kw|from|source|timestamp|is_from_webapp|sender_device|xsec_source|xsec_token|si|feature|utm_\w+|app|channel)$/i;

function normalizeUrl(text) {
  const s = extractUrl(text);
  if (!s || !isUrl(s)) return "";
  const withScheme = /^https?:\/\//i.test(s) ? s : "https://" + s;
  try {
    const u = new URL(withScheme);
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    const keep = [...u.searchParams]
      .filter(([k]) => !TRACK_PARAMS.test(k))
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    const query = keep.map(([k, v]) => `${k}=${v}`).join("&");
    const path = u.pathname.replace(/\/+$/, "");
    return host + path + (query ? "?" + query : "");
  } catch (_) {
    return s.replace(/^https?:\/\//i, "").replace(/\/+$/, "");
  }
}

const DUP_STATUS = {
  queued: "排队中", downloading: "正在下载", paused: "已暂停",
  merging: "正在合并", running: "进行中", done: "已完成",
};

/* created_at 是 Unix 秒（后端 time.time()）。顺手兼容毫秒，免得再踩一次。 */
function agoText(ts) {
  let t = Number(ts) || 0;
  if (t > 1e12) t /= 1000;
  const sec = Math.max(0, Date.now() / 1000 - t);
  if (sec < 60) return "刚刚";
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  if (sec < 172800) return "昨天";
  return `${Math.floor(sec / 86400)} 天前`;
}

/* 失败/取消的不算重复——那种情况重下是正常操作，不该拦。 */
function findDuplicate(text) {
  const key = normalizeUrl(text);
  if (!key) return null;
  let best = null;
  for (const t of state.tasks) {
    if (t.capability) continue;                       // 转写/转码不是「同一条下载」
    if (t.status === "error" || t.status === "cancelled") continue;
    const same = [t.url, t.url_original].some((x) => normalizeUrl(x) === key);
    if (same && (!best || (t.created_at || 0) > (best.created_at || 0))) best = t;
  }
  return best;
}

function renderDup(task) {
  state.dup = task || null;
  if (!task) { el.dupCard.classList.add("hidden"); return; }
  const status = DUP_STATUS[task.status] || task.status;
  el.dupText.textContent = `这条 ${agoText(task.created_at)}已经下过了（${status}）`;
  const again = task.status === "done";
  el.dupBtn.textContent = again ? "再来一次" : "定位";
  el.dupBtn.dataset.act = again ? "again" : "jump";
  el.dupCard.classList.remove("hidden");
}

function jumpToTask(id) {
  const node = state.nodes.get(id);
  if (!node) { setFilter("all"); return; }
  node.scrollIntoView({ block: "center", behavior: "smooth" });
  node.classList.add("flash");
  setTimeout(() => node.classList.remove("flash"), 1400);
}

function clearProbe() {
  state.probe = null;
  el.probeCard.classList.add("hidden");
  el.probeCard.classList.remove("failed");
  el.probeLogin.classList.add("hidden");
}

/* 探测失败不能就这么静默——粘一条被风控的链接，界面上什么都不出，
   人只会以为软件坏了。把站点原话翻译成一句能照着做的 */
function shortProbeError(message) {
  const raw = String(message || "");
  if (/412|Precondition Failed/i.test(raw)) {
    return "站点返回 412：挡了这次请求。换网络或稍等再试；B站建议先去「渠道账号」登录一次";
  }
  if (/403/i.test(raw)) return "站点返回 403：这条链接不放行，多半要登录才能看";
  if (/404|not exist|不存在/i.test(raw)) return "视频不存在或已删除";
  if (/timed out|timeout|超时/i.test(raw)) return "等太久没响应，网络或站点慢，稍后再试";
  if (/Unsupported URL|no suitable/i.test(raw)) return "这个站点还认不出来，先试试直接给视频文件";
  const last = raw.split(/\r?\n/).filter(Boolean).pop() || "";
  return last.replace(/^ERROR:\s*/, "").slice(0, 90) || "读不出来，原因未知";
}

function renderProbeError(message) {
  if (currentCapability() !== "download.platform") { clearProbe(); return; }
  state.probe = null;
  el.probeCard.classList.remove("hidden");
  el.probeCard.classList.add("failed");
  el.probeLogin.classList.add("hidden");
  el.probeThumb.removeAttribute("src");
  el.probeTitle.textContent = "这条链接读不出来";
  el.probeMeta.textContent = shortProbeError(message);
  el.probeCard.title = String(message || "");
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

/** Ctrl+V 落在哪个框里：当前视图的主链接输入框，别处不接管 */
function pasteTarget() {
  const shown = (id) => !document.getElementById(id).classList.contains("hidden");
  if (shown("view-secure")) return $("#secure-url");
  if (shown("view-tasks")) return el.url;
  return null;
}

/**
 * 窗口级粘贴。焦点在输入框里就交给浏览器自己处理；不在的话（比如刚打开
 * 窗口、或者点了空白处），直接把剪贴板里的文本送进链接框。
 */
function onWindowPaste(e) {
  const tag = (e.target?.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea") return;
  const target = pasteTarget();
  const text = e.clipboardData?.getData("text/plain") || "";
  // 直接从表格、文档里复制会带一堆换行，只认第一行有内容的
  const first = text.split(/\r?\n/).map((s) => s.trim()).find(Boolean) || "";
  if (!target || !first) return;
  e.preventDefault();
  target.value = first;
  target.focus();
  target.dispatchEvent(new Event("input", { bubbles: true }));
  // 粘完链接把焦点挪到「开始」上，回车就能跑，不用再点一下
  if (target === el.url && currentCapability() === "download.platform") el.add.focus();
}

async function pickFile() {
  let path = "";
  if (window.pywebview?.api?.select_file) {
    try { path = await window.pywebview.api.select_file(""); } catch (_) {}
  }
  if (!path) path = prompt("输入文件完整路径", "") || "";
  if (path) {
    el.url.value = path;
    el.url.focus();
    refreshEntry();
    scheduleDetect();
    renderExtra();
    renderDup(null);
  }
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
    if (cap === "download.platform") {
      // 同一条下过一次就拦一下：多半是手滑又粘了一遍，白占带宽
      const dup = findDuplicate(raw);
      if (dup) {
        renderDup(dup);
        toast(`这条 ${agoText(dup.created_at)}已经下过了，真要再下一次就点「再来一次」`, true);
        return;
      }
      await addDownload(raw);
    } else if (cap === "publish.browser") await preparePublish(raw);
    else await addCapabilityJob(cap, raw);

    afterAdd();
  } catch (err) {
    toast("添加失败：" + humanError(err.message), true);
  } finally {
    state.busy = false;
    el.add.disabled = false;
  }
}

function afterAdd() {
  rememberProject();
  el.url.value = "";
  clearProbe();
  renderDup(null);
  el.extra.innerHTML = "";
  el.extra.classList.add("hidden");
  refreshEntry();
  setFilter("all");
  refresh();
}

/* 「再来一次」真的再下一次；「定位」只是跳到列表里那条任务 */
async function onDupAction() {
  const dup = state.dup;
  if (!dup) return;
  if (el.dupBtn.dataset.act === "jump") { jumpToTask(dup.id); return; }
  const raw = el.url.value.trim();
  if (!raw || state.busy) return;
  state.busy = true;
  el.add.disabled = true;
  try {
    await addDownload(raw);
    afterAdd();
  } catch (err) {
    toast("添加失败：" + humanError(err.message), true);
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
  const res = await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
  toast(res?.task?.url_original ? "短链已还原真实地址，已加入队列" : "已加入下载队列");
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
  const whenText = ($("#publish-when")?.value || "").trim();
  let scheduledAt = 0;
  if (whenText) {
    const ts = new Date(whenText).getTime();
    if (!Number.isFinite(ts)) throw new Error("定时时间看不懂");
    if (ts / 1000 <= Date.now() / 1000 + 30) throw new Error("定时时间要晚于现在");
    scheduledAt = Math.round(ts / 1000);
  }
  btn.disabled = true;
  try {
    await api(`/api/exec/publish/${draft.id}/approve`, {
      method: "POST",
      body: JSON.stringify({ snapshot_hash: draft.snapshot_hash, scheduled_at: scheduledAt || null }),
    });
    el.publishModal.classList.add("hidden");
    toast(scheduledAt
      ? `已排好：${fmtWhen(scheduledAt)} 开始准备，到点自动跑`
      : "开始准备：传完文件会停在预览页，发布那一下由你点");
    $("#publish-when").value = "";
    state.publishDraft = null;
    await refresh();
    if (state.view === "accounts") refreshPublish();
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
    toast("先在「爆破下载」里粘一条回放链接，再点登录", true);
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

/* ───────────────────────── 模型接口 ───────────────────────── */

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

/** 工具页只看这个工具的活儿；没选工具（比如带 #tasks 直达）就全给看 */
function matchesTool(t) {
  if (!state.tool) return true;
  const tool = TOOLS.find((x) => x.id === state.tool);
  if (!tool || !tool.cap) return true;
  if (tool.cap === "__course") return t.kind === "course";
  // 下载任务由下载器直接建，身上没有 capability 字段，只能按 kind 认；
  // 课程任务归「爆破下载」那一页，不在这里重复出现
  if (tool.cap === "download.platform") {
    return !t.capability && t.kind !== "course";
  }
  return t.capability === tool.cap;
}

async function refresh() {
  if (state.busy) return;
  try {
    const data = await api("/api/tasks");
    state.tasks = data.tasks || [];
    render();
    // 那条任务可能刚下完，提示里的状态跟着变
    if (state.dup) renderDup(findDuplicate(el.url.value));
  } catch (_) {}
}

function render() {
  $("#c-all").textContent = state.tasks.length;

  const sorted = state.tasks
    .filter(matchesFilter)
    .sort((a, b) => (b.created_at || 0) - (a.created_at || 0));

  renderInto(el.list, el.empty, state.nodes, sorted.filter(matchesTool));
  // 爆破下载页只列课程任务。那页不在前台也照常更新，切过去就是最新的
  if (el.secureList) {
    renderInto(el.secureList, el.secureEmpty, state.secureNodes,
               sorted.filter((t) => t.kind === "course"));
  }
}

/** 把一个筛选结果画进某一份列表：节点按 id 复用，只改内容不重建，进度条不闪 */
function renderInto(list, empty, nodes, visible) {
  empty.classList.toggle("hidden", visible.length > 0);

  const alive = new Set();
  visible.forEach((t, idx) => {
    alive.add(t.id);
    let node = nodes.get(t.id);
    if (!node) {
      node = createTaskNode(t);
      nodes.set(t.id, node);
      list.appendChild(node);
    }
    updateTaskNode(node, t);
    const current = list.children[idx];
    if (current !== node) list.insertBefore(node, current || null);
  });

  for (const [id, node] of nodes) {
    if (!alive.has(id)) { node.remove(); nodes.delete(id); }
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

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function renderMeta(node, t, indeterminate) {
  const box = node.querySelector(".task-meta");
  const bits = [];
  const add = (text, cls) => { if (text) bits.push(cls ? `<span class="${cls}">${esc(text)}</span>` : `<span>${esc(text)}</span>`); };

  add(fmtTime(t.created_at), "when");
  if (t.source === "agent") {
    add(t.order_id ? `工单 ${t.order_id}` : "Agent 工单", "order");
  }
  if (t.project || t.sequence) {
    add([t.project, t.sequence].filter(Boolean).join(" / "), "proj");
  }
  if (t.url_original) {
    const host = hostOf(t.url);
    const label = `短链已还原${host ? " · " + host : ""}`;
    bits.push(`<span class="short" title="${esc(t.url_original)} → ${esc(t.url)}">${esc(label)}</span>`);
  }

  if (t.status === "error") {
    bits.push(`<span class="err" title="${esc(rawError(t.error))}">${esc(humanError(t.error))}</span>`);
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
    // 没选过、或选的账号被删了：落到第一个开着的浏览器上，别让界面空着
    if (!state.accounts.some((a) => a.id === state.activeAccount)) {
      const first = state.accounts.find((a) => a.running) || null;
      state.activeAccount = first ? first.id : "";
    }
    renderSideAccounts();
    renderAccountPane();
    renderStartPage();
    renderManagePage();
    refreshPublish();
    renderTabs();
    renderGrid();
    renderAccountSummary();
    syncStage();
    syncUrlBar();
  } catch (_) {}
}

/* 页面副标题只回答一个问题：现在有没有事要做 */
function renderAccountSummary() {
  const total = $("#acct-total");
  if (total) total.textContent = `${state.accounts.length} 个`;
  const line = $("#acct-summary");
  if (!line) return;
  const n = state.accounts.length;
  if (!n) {
    line.textContent = "还没有账号，先在左边新建一个";
    return;
  }
  const urgent = state.accounts.filter((a) => ["expired", "expiring", "stale"].includes(a.state));
  const open = openAccounts().length;
  if (urgent.length) {
    line.textContent = `${n} 个账号，${urgent.length} 个要重新登录：${
      urgent.map((a) => a.label).join("、")}`;
    line.classList.add("warn");
  } else {
    line.textContent = `${n} 个账号登录态都还在${open ? `，${open} 个开着` : ""}`;
    line.classList.remove("warn");
  }
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
  if (!box) return;   // 侧栏不再放账号列表，账号页自己有
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

/* 账号轨按「要不要处理」排，不按平台排。平台是账号的属性，挨着名字写就够；
   分组的轴只有一个——离掉线还有多久。剩几天的数字右对齐成一条竖线，
   扫一眼就知道谁先挂。 */
const ROSTER_GROUPS = [
  { key: "urgent", name: "要处理", states: ["expired", "expiring", "stale"] },
  { key: "ok", name: "正常", states: ["ok"] },
  { key: "idle", name: "还没登录过", states: ["never"] },
];

function rosterCount(acc) {
  if (acc.state === "expired") return "已失效";
  if (acc.remain_days != null) return `剩 ${Math.max(0, acc.remain_days).toFixed(0)} 天`;
  if (acc.stale_days != null) {
    const days = Math.max(0, acc.stale_days);
    return days < 1 ? "今天登过" : `${days.toFixed(0)} 天没登`;
  }
  return "";
}

function renderAccountPane() {
  const pane = $("#account-pane");
  if (!state.accounts.length) {
    pane.innerHTML = `<div class="roster-empty">还没有账号。<br>点右上角「添加账号」开第一个。</div>`;
    return;
  }
  const buckets = new Map(ROSTER_GROUPS.map((g) => [g.key, []]));
  for (const acc of state.accounts) {
    const group = ROSTER_GROUPS.find((g) => g.states.includes(acc.state)) || ROSTER_GROUPS[1];
    buckets.get(group.key).push(acc);
  }
  const html = [];
  for (const group of ROSTER_GROUPS) {
    const list = buckets.get(group.key);
    if (!list.length) continue;
    if (group.key === "urgent") {
      list.sort((a, b) => (a.remain_days ?? 999) - (b.remain_days ?? 999));
    }
    html.push(`<div class="roster-group ${group.key}">
      <div class="roster-group-name">${group.name}<span class="roster-group-n">${list.length}</span></div>`);
    for (const acc of list) {
      const flags = [];
      if (acc.running) flags.push(`<span class="roster-flag live">开着</span>`);
      if (acc.proxy) flags.push(
        `<span class="roster-flag proxy" title="浏览器代理 ${esc(acc.proxy)}">代理</span>`);
      html.push(`<button class="roster-row${group.key === "urgent" ? " urgent" : ""}${
          acc.state === "expired" ? " dead" : ""}${
          acc.id === state.activeAccount ? " active" : ""}"
          data-account="${acc.id}"
          style="--p-default: var(--p-${esc(acc.platform)}, #8b959a)">
        <span class="roster-tick"></span>
        <span class="roster-main">
          <span class="roster-name">${esc(acc.label)}</span>
          <span class="roster-meta">${esc(acc.platform_name)}${flags.join("")}</span>
        </span>
        <span class="roster-tail">
          <span class="roster-count">${esc(rosterCount(acc))}</span>
          <span class="roster-del" data-del="${acc.id}" title="删除账号">✕</span>
        </span>
      </button>`);
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
    btn.addEventListener("click", () => requestDelete(btn.dataset.del));
  });
}

/** 地址栏跟着浏览器走；用户正在里面打字的时候别抢 */
function syncUrlBar() {
  const input = $("#browser-url");
  if (!input || document.activeElement === input) return;
  if (state.acctPane !== "browser") return;
  const acc = state.accounts.find((a) => a.id === state.activeAccount);
  const url = acc && acc.running ? (acc.current_url || "") : "";
  if (url && url !== "about:blank" && url !== input.value) input.value = url;
}

function selectAccount(accountId, jump, url) {
  if (state.activeAccount !== accountId) state.pubTouched = false;
  state.deleting = "";
  const changed = state.activeAccount !== accountId;
  state.activeAccount = accountId;
  state.acctPane = "browser";
  state.frameSeen = false;
  state.stageSize = "";
  // 换账号先把手上的旧画面撤掉：留着上一家的页面，配上这一家的名字，
  // 点下去打到的还是上一家的网页——这个坑很隐蔽
  if (changed) {
    state.frameSeq[accountId] = -1;
    const img = $("#browser-frame");
    if (img) {
      img.hidden = true;
      img.removeAttribute("src");
      if (img.dataset.obj) URL.revokeObjectURL(img.dataset.obj);
      delete img.dataset.obj;
    }
    showStageHint();
  }
  renderAccountPane();
  renderTabs();
  refreshPublish();
  const acc = state.accounts.find((a) => a.id === accountId);
  $("#browser-url").value = "";
  $("#browser-url").placeholder = acc ? acc.platform_name + " 首页" : "当前账号首页";
  if (jump) { setView("accounts"); }
  renderGrid();
  syncStage();
  openAccount(accountId, url || "");
}

async function openAccount(accountId, url) {
  try {
    const res = await api(`/api/exec/accounts/${accountId}/open`, {
      method: "POST", body: JSON.stringify(url ? { url } : {}),
    });
    if (res.url) $("#browser-url").value = res.url;
    syncStage();
    startFrames();
    // 浏览器刚起来时窗口尺寸还没定下来，等它开了再把视口对齐一次
    setTimeout(syncViewport, 800);
    setTimeout(refreshAccounts, 1500);
    // 冷启动到页面真正打开要几秒，地址栏晚一点再对一次
    setTimeout(refreshAccounts, 6000);
  } catch (err) {
    toast("打开浏览器失败：" + err.message, true);
  }
}

/* 新标签页 / 账号管理：跟浏览器一样是两个常驻页签，点开就在右边这块地里 */
function showPane(pane) {
  state.acctPane = pane === "manage" ? "manage" : "start";
  if (state.acctMode === "grid") setAccountMode("single");
  if (state.acctPane === "manage") renderManagePage();
  if (state.acctPane === "start") renderStartPage();
  syncStage();
  renderTabs();
}

const QUICK_SITES = [
  { key: "bilibili", name: "哔哩哔哩", url: "https://www.bilibili.com" },
  { key: "douyin", name: "抖音", url: "https://www.douyin.com" },
  { key: "kuaishou", name: "快手", url: "https://www.kuaishou.com" },
  { key: "xiaohongshu", name: "小红书", url: "https://www.xiaohongshu.com" },
  { key: "weibo", name: "微博", url: "https://weibo.com" },
  { key: "shipinhao", name: "视频号", url: "https://channels.weixin.qq.com" },
];

function renderStartPage() {
  const box = $("#start-page");
  if (!box) return;
  const has = state.accounts.length;
  box.innerHTML = `
    <div class="start-inner">
      <h2>${has ? "挑一个账号开始" : "还没有账号"}</h2>
      <p class="start-hint">${has
        ? "左边点一个账号，就打开它自己的浏览器；也能直接从下面挑个平台进去。"
        : "先在左下角新建一个账号，登录一次能用几天，过期会排在左边最上面。"}</p>
      <div class="quick-grid">
        ${QUICK_SITES.map((site) => {
          const acc = state.accounts.find((a) => a.platform === site.key);
          return `<button class="quick" data-quick="${site.key}">
            <span class="quick-tick" style="--p-default: var(--p-${site.key}, #8b959a)"></span>
            <span class="quick-name">${site.name}</span>
            <span class="quick-sub">${acc ? "用 " + esc(acc.label) + " 打开" : "还没有账号"}</span>
          </button>`;
        }).join("")}
      </div>
    </div>`;
  box.querySelectorAll("[data-quick]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const site = QUICK_SITES.find((s) => s.key === btn.dataset.quick);
      const acc = state.accounts.find((a) => a.platform === site.key);
      if (!acc) { openNewAccountForm(); return; }
      selectAccount(acc.id, false, site.url);
    });
  });
}

/* 账号管理：平台、登录态、代理、开和删，全在这一张表里改 */
function renderManagePage() {
  const box = $("#manage-page");
  if (!box) return;
  const rows = state.accounts.map((acc) => `
    <div class="mg-row" data-mg="${acc.id}"
         style="--p-default: var(--p-${esc(acc.platform)}, #8b959a)">
      <span class="mg-tick"></span>
      <span class="mg-main">
        <span class="mg-name">${esc(acc.label)}</span>
        <span class="mg-sub ${esc(acc.state)}">${esc(acc.platform_name === acc.label
          ? (acc.state_text || rosterCount(acc))
          : `${acc.platform_name} · ${acc.state_text || rosterCount(acc)}`)}</span>
      </span>
      <input class="mg-proxy" data-proxy="${acc.id}" spellcheck="false"
             value="${esc(acc.proxy || "")}" placeholder="直连（填 http:// 或 socks5://）">
      <span class="mg-actions">
        <button class="btn ghost small" data-mg-open="${acc.id}">打开</button>
        ${state.deleting === acc.id
          ? `<button class="btn ghost small danger" data-mg-del="${acc.id}">确认删除</button>
             <button class="btn ghost small" data-mg-cancel="${acc.id}">取消</button>`
          : `<button class="btn ghost small danger" data-mg-del="${acc.id}">删除</button>`}
      </span>
    </div>`).join("");

  box.innerHTML = `
    <div class="mg-inner">
      <div class="mg-head">
        <h2>账号管理</h2>
        <span>代理只作用于这个账号的浏览器，下载任务走设置里的全局代理。留空＝直连。</span>
      </div>
      ${state.accounts.length ? rows
        : `<p class="muted">还没有账号。左边点「＋ 新建账号」开第一个。</p>`}
    </div>`;

  box.querySelectorAll("[data-proxy]").forEach((input) => {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") {
        const acc = state.accounts.find((a) => a.id === input.dataset.proxy);
        input.value = acc?.proxy || "";
        input.blur();
      }
    });
    input.addEventListener("blur", () => saveAccountProxy(input.dataset.proxy, input.value.trim()));
  });
  box.querySelectorAll("[data-mg-open]").forEach((btn) => {
    btn.addEventListener("click", () => selectAccount(btn.dataset.mgOpen, false));
  });
  box.querySelectorAll("[data-mg-del]").forEach((btn) => {
    btn.addEventListener("click", () => requestDelete(btn.dataset.mgDel));
  });
  box.querySelectorAll("[data-mg-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => { state.deleting = ""; renderManagePage(); });
  });
}

/* ── 多开：浏览器式标签 + 并排格子 ──
   浏览器开着几个账号，上面就有几个标签；切「多开」就是把它们并排摆出来，
   点格子进单开。跟浏览器窗口的直觉一致，不用另学一套。 */

function openAccounts() {
  return state.accounts.filter((a) => a.running);
}

function setAccountMode(mode) {
  state.acctMode = mode === "grid" ? "grid" : "single";
  document.querySelectorAll("#acct-mode [data-mode]").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === state.acctMode);
  });
  const isGrid = state.acctMode === "grid";
  $("#browser-grid").hidden = !isGrid;
  // 多开时格子自己就是标签，再摆一排标签是同一件事说两遍
  $("#tabstrip-wrap").hidden = isGrid;
  if (isGrid) {
    $("#browser-empty").hidden = true;
    $("#browser-frame").hidden = true;
  } else {
    syncStage();
  }
  renderTabs();
  renderGrid();
  startFrames();
}

function syncStage() {
  const onGrid = state.acctMode === "grid";
  const pane = onGrid ? "grid" : state.acctPane;
  const running = Boolean(state.activeAccount) && openAccounts()
    .some((a) => a.id === state.activeAccount);
  $("#start-page").hidden = pane !== "start";
  $("#manage-page").hidden = pane !== "manage";
  // 浏览器起来了但还没送来第一帧时，先让占位文字顶着，别留一块白板
  $("#browser-empty").hidden = pane !== "browser" || (running && state.frameSeen);
  $("#browser-frame").hidden = pane !== "browser" || !running;
  $("#browser-grid").hidden = !onGrid;
  // 只在真看着一个页面的时候才去要画面，在「新标签页 / 账号管理」上不用
  const watching = state.view === "accounts" && (onGrid || pane === "browser");
  if (watching && !state.frameOn) startFrames();
  else if (!watching && state.frameOn) stopFrames();
  if (pane === "browser" && running) syncViewport();
  if (pane !== "browser") hideStageHint();
}

function renderTabs() {
  const strip = $("#tabstrip");
  if (!strip) return;
  const on = (pane) => state.acctPane === pane && state.acctMode !== "grid";
  const fixed = `
    <div class="tab fixed${on("start") ? " on" : ""}" data-pane="start">
      <span class="tab-plus">＋</span><span class="tab-label">新标签页</span>
    </div>
    <div class="tab fixed${on("manage") ? " on" : ""}" data-pane="manage">
      <span class="tab-label">账号管理</span>
    </div>`;
  const tabs = openAccounts().map((acc) => `
    <div class="tab ${acc.id === state.activeAccount && state.acctPane === "browser" ? "on" : ""}"
         data-tab="${acc.id}"
         style="--p-default: var(--p-${esc(acc.platform)}, #8b959a)">
      <span class="tab-tick"></span>
      <span class="tab-label">${esc(acc.label)}</span>
      <button class="tab-close" data-tab-close="${acc.id}" title="关闭这个浏览器">✕</button>
    </div>`).join("");
  strip.innerHTML = fixed + tabs;
}

function renderGrid() {
  const grid = $("#browser-grid");
  if (!grid) return;
  const open = openAccounts();
  if (!open.length) {
    state.gridKey = "";
    grid.innerHTML = `<div class="grid-empty">
      <p>还没有打开的浏览器</p>
      <small>在左边点几个账号，就能并排看几个；点格子切到单开</small></div>`;
    return;
  }
  // 还是这几块就别重建：重建会把画面连同 blob 一起扔掉，闪一下白
  const key = open.map((acc) => acc.id).join(",");
  if (state.gridKey === key && grid.querySelector("[data-frame]")) {
    open.forEach((acc) => {
      const tile = grid.querySelector(`[data-tile="${acc.id}"]`);
      if (!tile) return;
      const name = tile.querySelector(".tile-name");
      const sub = tile.querySelector(".tile-sub");
      const count = tile.querySelector(".tile-count");
      if (name) name.textContent = acc.label;
      if (sub) sub.textContent = acc.platform_name;
      if (count) count.textContent = rosterCount(acc);
      tile.classList.toggle("on", acc.id === state.activeAccount);
    });
    return;
  }
  state.gridKey = key;
  grid.innerHTML = open.map((acc) => `
    <div class="tile ${acc.id === state.activeAccount ? "on" : ""}" data-tile="${acc.id}"
         style="--p-default: var(--p-${esc(acc.platform)}, #8b959a)">
      <div class="tile-head">
        <span class="tab-tick"></span>
        <span class="tile-name">${esc(acc.label)}</span>
        <span class="tile-sub">${esc(acc.platform_name)}</span>
        <span class="tile-count">${esc(rosterCount(acc))}</span>
      </div>
      <div class="tile-body"><img data-frame="${acc.id}" alt=""></div>
    </div>`).join("");
  pullTiles();
}

async function pullTiles() {
  if (state.acctMode !== "grid") return false;
  const imgs = [...document.querySelectorAll("[data-frame]")];
  let fresh = false;
  await Promise.all(imgs.map(async (img) => {
    try {
      const since = img.dataset.seq ? Number(img.dataset.seq) : -1;
      const res = await fetch(
        `/api/exec/accounts/${img.dataset.frame}/frame.jpg?since=${since}`,
        { cache: "no-store" });
      if (res.status !== 200) {
        if (res.status !== 204) img.classList.add("dead");
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const old = img.dataset.obj;
      img.onload = () => { if (old) URL.revokeObjectURL(old); };
      img.src = url;
      img.dataset.obj = url;
      img.dataset.seq = res.headers.get("X-Frame-Seq") || "0";
      img.classList.remove("dead");
      fresh = true;
    } catch (_) {}
  }));
  return fresh;
}

async function closeAccount(accountId) {
  try {
    await api(`/api/exec/accounts/${accountId}/close`, { method: "POST" });
  } catch (err) {
    toast("关闭失败：" + err.message, true);
  }
  if (state.activeAccount === accountId) {
    state.activeAccount = "";
    state.acctPane = "start";
    stopFrames();
  }
  await refreshAccounts();
  syncStage();
}

/* 画面循环：带上一帧的序号去问，没有新帧就 204 立刻回来。
   页面一变 Chrome 自己推帧，所以「有新的」通常紧接着就会发生，
   这时马上再问一次，看起来就是连续的；没变化的时间段几乎不耗东西。 */
function startFrames() {
  stopFrames();
  if (state.view !== "accounts") return;
  state.frameOn = true;
  tickFrames();
}

function stopFrames() {
  state.frameOn = false;
  clearTimeout(state.frameTimer);
  state.frameTimer = null;
}

async function tickFrames() {
  if (!state.frameOn) return;
  const grid = state.acctMode === "grid";
  if (!grid && state.acctPane !== "browser") {
    state.frameTimer = setTimeout(tickFrames, 300);
    return;
  }
  let fresh = false;
  try {
    fresh = grid ? await pullTiles() : await pullFrame();
  } catch (_) {}
  if (!state.frameOn) return;
  state.frameTimer = setTimeout(tickFrames, fresh ? 16 : (grid ? 350 : 90));
}

async function pullFrame() {
  const id = state.activeAccount;
  if (!id || state.acctMode === "grid" || state.acctPane !== "browser") return false;
  const since = state.frameSeq[id] ?? -1;
  const res = await fetch(`/api/exec/accounts/${id}/frame.jpg?since=${since}`,
    { cache: "no-store" });
  if (id !== state.activeAccount) return false;
  if (res.status !== 200) {
    if (res.status !== 204) $("#browser-empty").hidden = false;
    return false;
  }
  const seq = Number(res.headers.get("X-Frame-Seq") || 0);
  const blob = await res.blob();
  if (id !== state.activeAccount) return false;
  const img = $("#browser-frame");
  const url = URL.createObjectURL(blob);
  const old = img.dataset.obj;
  img.onload = () => { if (old) URL.revokeObjectURL(old); };
  img.src = url;
  img.dataset.obj = url;
  img.hidden = state.acctPane !== "browser" || state.acctMode === "grid";
  state.frameSeq[id] = seq;
  if (!state.frameSeen) {
    state.frameSeen = true;
    $("#browser-empty").hidden = true;
  }
  return true;
}

/* ── 嵌在软件里的浏览器 ──
   画面是一帧帧送回来的，操作却是真的：点、滚、打字都按坐标和按键打进
   页面里，走的是桌面 Chrome 同一套输入事件。键盘交给 #stage-ime 那个
   透明输入框收——中文输入法组词、Ctrl+V 读剪贴板，都得先有一个真的
   可输入元素，收完再送进页面。 */

function stagePoint(e) {
  const img = $("#browser-frame");
  const rect = img.getBoundingClientRect();
  const w = img.naturalWidth || rect.width || 1;
  const h = img.naturalHeight || rect.height || 1;
  return {
    x: Math.round((e.clientX - rect.left) * (w / (rect.width || 1))),
    y: Math.round((e.clientY - rect.top) * (h / (rect.height || 1))),
  };
}

function stageLive() {
  return state.acctMode === "single" && state.acctPane === "browser"
    && Boolean(state.activeAccount);
}

function focusStage() {
  const ime = $("#stage-ime");
  if (ime && stageLive()) ime.focus({ preventScroll: true });
}

function hideStageHint() {
  const hint = $("#stage-hint");
  if (!hint) return;
  hint.hidden = true;
  clearTimeout(state.hintTimer);
}

function showStageHint() {
  const hint = $("#stage-hint");
  if (!hint) return;
  hint.hidden = false;
  clearTimeout(state.hintTimer);
  state.hintTimer = setTimeout(hideStageHint, 9000);
}

/** 按键一个一个发。并发发会乱序，「ab」会打成「ba」 */
function queueAction(payload) {
  state.keyQueue = (state.keyQueue || Promise.resolve())
    .then(() => accountAction(payload))
    .catch(() => {});
  return state.keyQueue;
}

function modifiersOf(e) {
  return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0)
    | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
}

function bindStage() {
  const frame = $("#browser-frame");
  const stage = $("#browser-stage");
  const ime = $("#stage-ime");
  if (!frame || !stage || !ime) return;

  frame.addEventListener("click", (e) => {
    if (!stageLive()) return;
    focusStage();
    hideStageHint();
    const p = stagePoint(e);
    queueAction({ kind: "click", x: p.x, y: p.y,
      count: Math.min(3, e.detail || 1), button: "left" });
  });
  frame.addEventListener("contextmenu", (e) => {
    if (!stageLive()) return;
    e.preventDefault();
    const p = stagePoint(e);
    queueAction({ kind: "click", x: p.x, y: p.y, button: "right" });
  });
  // 悬停要发，否则菜单永远不展开；位置没怎么变就不发，省得空转
  frame.addEventListener("mousemove", (e) => {
    if (!stageLive()) return;
    const now = Date.now();
    if (now - (state.moveAt || 0) < 100) return;
    state.moveAt = now;
    const p = stagePoint(e);
    queueAction({ kind: "move", x: p.x, y: p.y });
  });
  // 滚轮：触控板一次能刷几十个事件，攒 60ms 一起发，页面才跟手
  stage.addEventListener("wheel", (e) => {
    if (!stageLive()) return;
    e.preventDefault();
    const p = stagePoint(e);
    const buf = state.wheelBuf || { dx: 0, dy: 0, x: p.x, y: p.y, timer: null };
    buf.dx += Math.round(e.deltaX);
    buf.dy += Math.round(e.deltaY);
    buf.x = p.x;
    buf.y = p.y;
    if (!buf.timer) {
      buf.timer = setTimeout(() => {
        state.wheelBuf = null;
        queueAction({ kind: "wheel", dx: buf.dx, dy: buf.dy, x: buf.x, y: buf.y });
      }, 60);
    }
    state.wheelBuf = buf;
  }, { passive: false });

  ime.addEventListener("keydown", (e) => {
    if (!stageLive()) return;
    // 输入法正在组词：这些键归输入法，别抢
    if (e.isComposing || e.keyCode === 229 || e.key === "Process") return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === "v" || e.key === "V")) return;   // 交给 paste 事件
    e.preventDefault();
    const printable = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
    const text = printable ? e.key
      : e.key === "Enter" ? "\r" : e.key === "Tab" ? "\t" : "";
    queueAction({ kind: "key", key: e.key, code: e.code, vk: e.keyCode,
      text, modifiers: modifiersOf(e) });
    queueAction({ kind: "key", type: "up", key: e.key, code: e.code,
      vk: e.keyCode, modifiers: modifiersOf(e) });
  });
  // 输入法组完词从这里进页面；偶尔没有 keydown 的输入也走这里
  ime.addEventListener("input", (e) => {
    if (e.isComposing || !ime.value) return;
    const text = ime.value;
    ime.value = "";
    if (stageLive()) queueAction({ kind: "type", text });
  });
  ime.addEventListener("paste", (e) => {
    e.preventDefault();
    const clip = e.clipboardData || window.clipboardData;
    const text = clip ? clip.getData("text") : "";
    ime.value = "";
    if (text && stageLive()) queueAction({ kind: "type", text });
  });
  ime.addEventListener("blur", () => { ime.value = ""; });
}

/** 页面视口跟界面里这块画布对齐：不然 1280×800 的页面缩进小框里，字发虚。
 *  高分屏再乘上 devicePixelRatio，帧按物理像素出，放大窗口也不糊。 */
function syncViewport() {
  if (!stageLive()) return;
  const box = $("#browser-stage");
  if (!box) return;
  const w = Math.round(box.clientWidth);
  const h = Math.round(box.clientHeight);
  if (w < 240 || h < 200) return;
  const dpr = Math.min(2, Math.round((window.devicePixelRatio || 1) * 100) / 100);
  const key = `${w}x${h}@${dpr}`;
  if (key === state.stageSize) return;
  state.stageSize = key;
  accountAction({ kind: "resize", width: w, height: h, dpr });
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

/* 新建账号：在左栏就地展开一行，不开弹窗 */
function openNewAccountForm() {
  const form = $("#new-acct-form");
  const select = $("#account-platform-select");
  const list = state.platforms.filter((p) => p.can_publish !== false || p.key === "bilibili");
  select.innerHTML = list
    .map((p) => `<option value="${p.key}">${esc(p.name)}</option>`).join("");
  if (list.some((p) => p.key === state.accountPlatform)) {
    select.value = state.accountPlatform;
  } else if (list.length) {
    state.accountPlatform = list[0].key;
  }
  select.addEventListener("change", () => { state.accountPlatform = select.value; });
  $("#account-label").value = "";
  form.classList.remove("hidden");
  $("#btn-new-account").classList.add("hidden");
  $("#account-label").focus();
}

function closeNewAccountForm() {
  $("#new-acct-form").classList.add("hidden");
  $("#btn-new-account").classList.remove("hidden");
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
    closeNewAccountForm();
    await refreshAccounts();
    selectAccount(acc.id, false);
  } catch (err) {
    toast("添加失败：" + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* 一键检测：只看本地 cookie 的过期时间，一个个开浏览器太慢也太吵。
   真要页面级确认，去那个账号上点「验证登录」。 */
async function checkAllAccounts() {
  if (!state.accounts.length) { toast("还没有账号", true); return; }
  const btn = $("#btn-check-all");
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "检测中…";
  let failed = 0;
  try {
    for (const acc of state.accounts) {
      try {
        await api(`/api/exec/accounts/${acc.id}/check`, {
          method: "POST", body: JSON.stringify({ online: false }),
        });
      } catch (_) { failed += 1; }
    }
    await refreshAccounts();
    const urgent = state.accounts.filter((a) =>
      ["expired", "expiring", "stale"].includes(a.state));
    if (urgent.length) {
      toast(`${urgent.length} 个要重新登录：${urgent.map((a) => a.label).join("、")}`, true);
    } else {
      toast(`都还在${failed ? `，${failed} 个没查成` : ""}`);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* 发布记录：审核中 / 需人工 / 定时待发 —— 都不是「失败」 */

/* 定时时间只说到「什么时候」，太长反而看不清 */
function fmtWhen(ts) {
  const t = Number(ts) || 0;
  if (!t) return "";
  const d = new Date(t * 1000);
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now.getTime() + 86400000).toDateString() === d.toDateString();
  if (sameDay) return `今天 ${hm}`;
  if (tomorrow) return `明天 ${hm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

async function refreshPublish() {
  const list = $("#pub-list");
  if (!list) return;
  const acc = activeAccount();
  if (!acc) {
    list.innerHTML = `<div class="pub-empty">选一个账号，看它发过什么</div>`;
    $("#pub-counts").textContent = "";
    return;
  }
  try {
    const data = await api(`/api/exec/publish?account_id=${encodeURIComponent(acc.id)}`);
    state.publish = data.records || [];
    renderPublish(state.publish, data.counts || {});
  } catch (err) {
    list.innerHTML = `<div class="pub-empty">读不到发布记录：${esc(err.message)}</div>`;
  }
}

function togglePublish() {
  const panel = $("#pub-panel");
  if (!panel) return;
  state.pubTouched = true;   // 手动收起来之后就别再自动弹开了
  const collapsed = panel.classList.toggle("collapsed");
  $("#pub-toggle").setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function renderPublish(records, counts) {
  const list = $("#pub-list");
  // 收起来的那一行要能替代展开后的内容：先说什么在等你，再说还剩几条
  const parts = [];
  for (const key of ["reviewing", "manual", "failed", "scheduled"]) {
    if (counts[key]) parts.push(`${counts[key]} 条${PUB_STATE[key] || key}`);
  }
  const summary = $("#pub-counts");
  if (summary) {
    summary.textContent = records.length
      ? `${records.length} 条${parts.length ? "，其中 " + parts.join("、") : ""}`
      : "";
    summary.classList.toggle("alert", Boolean(counts.reviewing || counts.manual || counts.failed));
  }
  if (!state.pubTouched && (counts.reviewing || counts.manual || counts.failed)) {
    const panel = $("#pub-panel");
    if (panel) panel.classList.remove("collapsed");
  }
  if (!records.length) {
    list.innerHTML = `<div class="pub-empty">还没发布过。产出页的「发布」会出现在这里</div>`;
    return;
  }
  list.innerHTML = records.map((rec) => {
    const when = rec.scheduled_at && rec.state === "scheduled"
      ? `定时 ${fmtWhen(rec.scheduled_at)}`
      : agoText(rec.updated_at || rec.created_at);
    const name = rec.title || (rec.files[0] || "").split(/[\\/]/).pop() || "未命名";
    const note = rec.error || rec.hint || "";
    const btn = rec.can_cancel_schedule
      ? `<button class="btn ghost small" data-pub-cancel="${rec.id}">取消定时</button>`
      : rec.can_verify
        ? `<button class="btn ghost small" data-pub-verify="${rec.id}">复查</button>`
        : rec.can_retry
          ? `<button class="btn ghost small" data-pub-retry="${rec.id}">重新准备</button>`
          : "";
    return `<div class="pub-row">
      <span class="pub-state ${esc(rec.tone || "")}">${esc(rec.state_text || rec.state)}</span>
      <span class="pub-row-main">
        <span class="pub-row-name">${esc(name)}</span>
        <span class="pub-row-note">${esc(when)} · ${esc(note)}</span>
      </span>
      ${btn}
    </div>`;
  }).join("");
  list.querySelectorAll("[data-pub-verify],[data-pub-retry],[data-pub-cancel]").forEach((btn) => {
    const mode = btn.dataset.pubCancel ? "cancel"
      : btn.dataset.pubVerify ? "verify" : "retry";
    const id = btn.dataset.pubCancel || btn.dataset.pubVerify || btn.dataset.pubRetry;
    btn.addEventListener("click", () => publishAct(id, mode));
  });
}

async function publishAct(recordId, mode) {
  const rec = (state.publish || []).find((r) => r.id === recordId);
  if (!rec) { toast("找不到这条记录，刷新一下页面", true); return; }
  try {
    if (mode === "cancel") {
      await api(`/api/exec/publish/${recordId}/cancel-schedule`, { method: "POST" });
      toast("定时已取消，记录回到待确认");
    } else {
      const body = { snapshot_hash: rec.snapshot_hash };
      if (mode === "verify") body.verify_only = true;
      await api(`/api/exec/publish/${recordId}/approve`, {
        method: "POST", body: JSON.stringify(body),
      });
      toast(mode === "verify" ? "已去后台复查，结果会自己更新" : "已重新排队准备");
    }
    setTimeout(refreshPublish, 1200);
  } catch (err) {
    toast("操作失败：" + err.message, true);
  }
}

/* 账号代理：一个账号一个浏览器，代理跟着账号走 */

function activeAccount() {
  return state.accounts.find((a) => a.id === state.activeAccount) || null;
}

/* 代理是账号的属性，改在「账号管理」那一页，不再弹窗问 */
async function saveAccountProxy(accountId, value) {
  const acc = state.accounts.find((a) => a.id === accountId);
  if (!acc || String(acc.proxy || "") === value) return;
  try {
    const res = await api(`/api/exec/accounts/${accountId}/proxy`, {
      method: "POST", body: JSON.stringify({ proxy: value }),
    });
    await refreshAccounts();
    const text = res.proxy ? `代理已设为 ${res.display}` : "已改为直连";
    toast(res.changed
      ? `${text}${res.restarted ? "，浏览器已重开" : "，下次打开生效"}`
      : "没有改动");
  } catch (err) {
    toast("保存失败：" + err.message, true);
    renderManagePage();
  }
}

/* 两步删除：第一次只是上膛，第二次才真删。删账号会把浏览器目录一起清掉，
   这种事不该被一个手滑点掉，也不该靠系统弹窗。 */
function requestDelete(accountId) {
  if (state.deleting !== accountId) {
    state.deleting = accountId;
    const acc = state.accounts.find((a) => a.id === accountId);
    toast(`再点一次，删除「${acc ? acc.label : "这个账号"}」`);
    if (state.acctPane === "manage") renderManagePage();
    return;
  }
  deleteAccount(accountId);
}

async function deleteAccount(accountId) {
  state.deleting = "";
  try {
    await api(`/api/exec/accounts/${accountId}`, { method: "DELETE" });
    if (state.activeAccount === accountId) {
      state.activeAccount = "";
      state.acctPane = "start";
      stopFrames();
    }
    await refreshAccounts();
    syncStage();
  } catch (err) {
    toast("删除失败：" + err.message, true);
  }
}

/* ───────────────────────── 外部接入 ───────────────────────── */

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
  const token = "<在「外部接入」里生成的令牌>";
  let text = "";
  if (state.connTab === "mcp") {
    text = JSON.stringify({
      mcpServers: {
        ocemind: {
          command: "python",
          args: ["-m", "ocemind.executor.mcp"],
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
      `python -m ocemind.executor.cli capabilities`,
      `python -m ocemind.executor.cli download "https://www.bilibili.com/video/BV..."`,
      `python -m ocemind.executor.cli transcribe "D:\\视频\\访谈.mp4"`,
      `python -m ocemind.executor.cli accounts`,
      ``,
      `# 指向远端的执行器`,
      `python -m ocemind.executor.cli --url ${url} --token ${token} jobs`,
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

function fillSettings() {
  const s = state.settings;
  $("#set-dir").value = s.defaultDir || "";
  $("#set-conn").value = s.defaultConnections || 8;
  $("#set-concurrent").value = s.maxConcurrent || 3;
  $("#set-stagger").value = s.startStagger ?? 1.2;
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
  refreshYtdlp();
  refreshGc();
}

/* ── 账号浏览器目录占用 ────────────────────────────────────── */

async function refreshGc() {
  const info = $("#gc-info");
  try {
    const g = await api("/api/exec/accounts/gc");
    const parts = [`共 ${fmtBytes(g.total)}`];
    if (g.cache) parts.push(`其中缓存 ${fmtBytes(g.cache)} 可清`);
    if (g.orphan_total) parts.push(`已删账号的目录 ${fmtBytes(g.orphan_total)}`);
    let text = parts.join(" · ");
    if (g.last?.at) text += `；上次清理 ${agoText(g.last.at)}，清出 ${fmtBytes(g.last.freed)}`;
    info.textContent = text;
    $("#gc-orphans").classList.toggle("hidden", !g.orphan_total);
  } catch (err) {
    info.textContent = "统计失败：" + humanError(err.message);
  }
}

async function cleanGc(orphans) {
  try {
    const r = await api("/api/exec/accounts/gc", {
      method: "POST", body: JSON.stringify({ orphans }),
    });
    toast(r.freed ? `清出 ${fmtBytes(r.freed)}` : "没有可清理的缓存");
    await refreshGc();
  } catch (err) {
    toast("清理失败：" + humanError(err.message), true);
  }
}

/* ── yt-dlp 自更新 ─────────────────────────────────────────────
   站点改版是按周的，等软件发版太慢。这里更新的是纯 Python 包，落到数据
   目录，下次派发 yt-dlp 就用新的，不用重启、不用签名。 */

async function refreshYtdlp() {
  const label = $("#ytdlp-version");
  try {
    const s = await api("/api/tools/ytdlp");
    const src = s.source === "external" ? "已更新"
      : s.source === "bundled" ? "自带" : "未找到";
    label.textContent = `${s.version || "未知"} · ${src}`;
    label.dataset.source = s.source || "";
    $("#ytdlp-revert").classList.toggle("hidden", s.source !== "external");
  } catch (_) {
    label.textContent = "读取失败";
  }
}

function ytdlpButton(mode, text) {
  const btn = $("#ytdlp-check");
  btn.dataset.mode = mode;
  btn.textContent = text;
  btn.disabled = false;
}

async function onYtdlpCheck() {
  const btn = $("#ytdlp-check");
  const hint = $("#ytdlp-hint");
  btn.disabled = true;
  btn.textContent = "正在问 PyPI…";
  try {
    const r = await api("/api/tools/ytdlp/check", { method: "POST" });
    if (!r.ok) { hint.textContent = r.error || "连不上 PyPI，看看设置里的代理。"; ytdlpButton("check", "重试"); return; }
    if (!r.has_update) {
      hint.textContent = `已经是最新（${r.latest}）。`;
      ytdlpButton("check", "检查更新");
      return;
    }
    hint.textContent = `有新版本 ${r.latest}，现在是 ${r.current}。`;
    ytdlpButton("update", `更新到 ${r.latest}`);
  } catch (err) {
    hint.textContent = humanError(err.message);
    ytdlpButton("check", "重试");
  }
}

async function onYtdlpUpdate() {
  const btn = $("#ytdlp-check");
  const hint = $("#ytdlp-hint");
  btn.disabled = true;
  btn.textContent = "下载中…";
  try {
    const r = await api("/api/tools/ytdlp/update", { method: "POST" });
    hint.textContent = r.changed
      ? `已更新到 ${r.version}，下一个任务就生效。`
      : `已经是最新（${r.version}）。`;
    toast(r.changed ? `yt-dlp 已更新到 ${r.version}` : "已经是最新版本");
    ytdlpButton("check", "检查更新");
    await refreshYtdlp();
    await loadSystem();
  } catch (err) {
    hint.textContent = humanError(err.message);
    ytdlpButton("check", "重试");
  }
}

async function onYtdlpRevert() {
  try {
    await api("/api/tools/ytdlp/revert", { method: "POST" });
    toast("已回到自带版本");
    ytdlpButton("check", "检查更新");
    $("#ytdlp-hint").textContent = "已删掉外部版本，改用软件自带的那份。";
    await refreshYtdlp();
    await loadSystem();
  } catch (err) {
    toast("回退失败：" + humanError(err.message), true);
  }
}

async function saveSettingsForm() {
  const values = {
    defaultDir: $("#set-dir").value.trim(),
    defaultConnections: Number($("#set-conn").value) || 8,
    maxConcurrent: Number($("#set-concurrent").value) || 3,
    startStagger: Math.max(0, Number($("#set-stagger").value) || 0),
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
  };
  await saveSettings(values);
  el.dir.value = values.defaultDir || el.dir.value;
  el.conn.value = values.defaultConnections;
  toast("设置已保存");
  loadSystem();
}

/* ───────────────────────── 启动 ───────────────────────── */

init();
