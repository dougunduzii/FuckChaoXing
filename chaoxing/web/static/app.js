/* cx · 超星任务台 — 面板逻辑。vanilla ES2020，单文件，无框架、无外部依赖。
   渲染约定：一切来自网络的数据都走 createElement / textContent，绝不拼 innerHTML。 */
(() => {
"use strict";

/* ───────────────── DOM 小工具 ───────────────── */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const node = (t, c) => { const n = document.createElement(t); if (c) n.className = c; return n; };
const add = (n, kids) => {
  for (const k of kids.flat(3)) {
    if (k === null || k === undefined || k === false) continue;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return n;
};
const h = (t, c, ...kids) => add(node(t, c), kids);
const set = (n, ...kids) => { if (!n) return n; n.replaceChildren(); return add(n, kids); };
const tx = (t, c, s) => { const n = node(t, c); n.textContent = s === null || s === undefined ? "" : String(s); return n; };
const opt = (v, label) => { const o = node("option"); o.value = String(v); o.textContent = label; return o; };
const btnEl = (label, cls, fn) => { const b = h("button", "btn" + (cls ? " " + cls : ""), label); b.type = "button"; if (fn) b.onclick = fn; return b; };
const badge = (text, tone) => tx("span", "badge" + (tone ? " " + tone : ""), text);
const bar = (r, tone) => { const i = node("i", tone || ""); i.style.width = (Math.min(1, Math.max(0, r)) * 100).toFixed(1) + "%"; return h("div", "bar sm", i); };
const armBtn = (btn, label, fn) => {
  const base = btn.textContent; let armed = false, timer = 0;
  btn.addEventListener("click", () => {
    if (!armed) {
      armed = true; btn.textContent = label; btn.classList.add("danger");
      timer = setTimeout(() => { armed = false; btn.textContent = base; btn.classList.remove("danger"); }, 4000);
      return;
    }
    clearTimeout(timer); armed = false; btn.classList.remove("danger"); btn.textContent = base; fn();
  });
};
const debounce = (fn, ms) => { let t = 0; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ───────────────── 数值 / 时间 ───────────────── */
const pad2 = n => String(n).padStart(2, "0");
const numi = v => { const n = Number(v); return isFinite(n) ? Math.round(n) : 0; };
const num = v => { const n = Number(v); return isFinite(n) ? String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "—"; };
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const ratio = (p, t) => { const d = Number(t) || 0; return d > 0 ? clamp((Number(p) || 0) / d, 0, 1) : 0; };
const pct = (p, t) => Math.round(ratio(p, t) * 100);
const fix1 = v => { const n = Number(v); return isFinite(n) ? n.toFixed(1) : "—"; };
const tsMs = v => { const n = Number(v); if (!isFinite(n) || n <= 0) return 0; return n > 1e11 ? n : n * 1000; };
const hm = ms => { const d = new Date(ms); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
const dtStr = ms => { const d = new Date(ms); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
const rel = v => {
  const ms = tsMs(v); if (!ms) return "—";
  const d = Date.now() - ms;
  if (d < 10000) return "刚刚";
  const s = Math.floor(d / 1000);
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const hh = Math.floor(m / 60);
  if (hh < 24) return `${hh} 小时前`;
  const dd = Math.floor(hh / 24);
  return dd < 30 ? `${dd} 天前` : dtStr(ms);
};
const dur = v => {
  const t = Math.max(0, Math.round(Number(v) || 0)), hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = t % 60;
  return hh ? `${hh}h${pad2(mm)}m` : mm ? `${mm}m${pad2(ss)}s` : `${ss}s`;
};
const cdur = v => {
  const t = Math.max(0, Math.round(Number(v) || 0)), hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60);
  return `${hh ? hh + ":" + pad2(mm) : mm}:${pad2(t % 60)}`;
};

/* ───────────────── 常量 ───────────────── */
const TOKEN_KEY = "cx_token", PREFS_KEY = "cx_prefs";
const VIEWS = ["overview", "tasks", "logs", "runs", "settings"];
const PAGE = 100, MAX_LOG = 3000;
const TYPES = ["video", "document", "read", "emptypage", "work"];
const TYPE_LABEL = { video: "视频", document: "文档", read: "阅读", emptypage: "空页", work: "作业" };
const STATE_LABEL = { pending: "待办", queued: "排队", running: "进行中", done: "已完成", failed: "失败", skip: "跳过", skipped: "跳过", cancelled: "已取消" };
const STATE_TONE = { pending: "plain", queued: "plain", running: "run", done: "ok", failed: "err", skip: "warn", skipped: "warn", cancelled: "warn" };
const JOB_TXT = { queued: "排队", running: "运行中", done: "已完成", failed: "失败", cancelled: "已取消" };
const KIND_LABEL = { scan: "抓取课程", run: "跑任务", login: "登录", reset: "重置" };
const LEVEL_TONE = { TRACE: "debug", DEBUG: "debug", INFO: "info", SUCCESS: "success", WARNING: "warning", WARN: "warning", ERROR: "error", CRITICAL: "critical" };
const TITLES = {
  overview: ["总览", "任务点进度 · 课程 · 最近任务"],
  tasks: ["任务点", "库内任务点明细"],
  logs: ["日志", "实时输出 · SSE 断线自动降级轮询"],
  runs: ["请求记录", "最近 50 条上游调用"],
  settings: ["设置", "账号 · 运行默认值 · 关于"],
};
const FILTER_STATES = [["", "全部"], ["pending", "待办"], ["running", "进行中"], ["done", "已完成"], ["failed", "失败"], ["skip", "跳过"]];

/* ───────────────── 状态 ───────────────── */
const S = {
  token: "", view: "overview", account: "", state: null, stateErr: "", health: null,
  quiz: null, quizErr: "", busy: false, mode: "idle", es: null, backoff: 3000,
  reconnectTimer: 0, pollTimer: 0, logPolling: false, logs: [], seen: new Set(), lastSeq: 0,
  follow: true, filter: "", filters: { state: "", type: "", course: "", q: "" }, taskPage: 0,
  tasks: { items: [], total: 0, error: "", loading: false },
  runs: [], runsErr: "", jobStatus: {}, defaults: { speed: 2, lanes: 1 }, resetArm: 0,
};
const D = {};
let stateReq = null, tasksSeq = 0;

const readToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } };
const writeToken = v => { try { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* 隐私模式忽略 */ } };

function initToken() {
  const qs = new URLSearchParams(location.search);
  let t = qs.get("token") || "";
  const hash = String(location.hash || "");
  if (!t && hash.indexOf("token=") >= 0) t = decodeURIComponent(hash.slice(hash.indexOf("token=") + 6).split("&")[0]);
  if (t) { S.token = t; writeToken(t); }
  else S.token = readToken();
  if (t || location.search) {
    const u = new URL(location.href);
    u.searchParams.delete("token");
    const keep = u.hash.replace(/^#/, "").split("&").filter(p => p && p.indexOf("token=") !== 0);
    const rest = keep.join("&");
    u.hash = VIEWS.indexOf(rest) >= 0 ? "#" + rest : "";
    try { history.replaceState(null, "", u.pathname + u.search + u.hash); } catch (e) { /* 忽略 */ }
  }
}

function readPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "null");
    if (p && typeof p === "object") {
      const sp = Number(p.speed), ln = Number(p.lanes);
      if (isFinite(sp) && sp > 0) S.defaults.speed = sp;
      if (isFinite(ln) && ln > 0) S.defaults.lanes = Math.round(ln);
    }
  } catch (e) { /* 忽略 */ }
}
function writePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify({ speed: S.defaults.speed, lanes: S.defaults.lanes })); } catch (e) { /* 忽略 */ }
}

/* ───────────────── toast ───────────────── */
function toast(message, tone = "info", ttl = 4000) {
  const box = D.toasts; if (!box) return;
  const el = h("div", "toast " + tone, String(message == null ? "" : message));
  el.setAttribute("role", tone === "err" ? "alert" : "status");
  box.append(el);
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 220); }, ttl);
}

/* ───────────────── API ───────────────── */
class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status || 0;
    this.data = data || null;
    this.silent = false;
  }
}

async function api(method, path, body) {
  const init = { method, headers: {} };
  if (S.token) init.headers["X-CX-Token"] = S.token;
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, init); }
  catch (e) { throw new ApiError("网络不可达：" + (e && e.message ? e.message : "连接失败"), 0); }
  const text = await res.text().catch(() => "");
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
  if (res.status === 401) {
    openAuth((data && (data.error || data.detail)) || "需要访问口令");
    const err = new ApiError("需要访问口令", 401, data); err.silent = true; throw err;
  }
  if (!res.ok) {
    let msg = data && (data.error || data.detail);
    if (!msg) msg = "HTTP " + res.status;
    if (typeof msg !== "string") { try { msg = JSON.stringify(msg); } catch (e) { msg = "请求失败"; } }
    const err = new ApiError(msg, res.status, data);
    if (res.status === 409) err.silent = false;
    throw err;
  }
  return data === null ? {} : data;
}

function reportErr(err) {
  if (!err || err.silent) return;
  toast(err.message || String(err), "err", 6000);
}

async function withBusy(fn) {
  if (S.busy) return;
  S.busy = true; renderHeader();
  try { await fn(); }
  catch (err) { reportErr(err); }
  finally { S.busy = false; renderHeader(); }
}

/* ───────────────── state ───────────────── */
function normalizeState(raw) {
  const o = raw && typeof raw === "object" ? raw : {};
  const arr = v => (Array.isArray(v) ? v : []);
  const obj = v => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
  const sum = obj(o.summary);
  return {
    running: !!o.running,
    job: o.job && typeof o.job === "object" ? o.job : null,
    jobs: arr(o.jobs), accounts: arr(o.accounts), by_type: arr(o.by_type), courses: arr(o.courses),
    summary: { total: numi(sum.total), done: numi(sum.done), pending: numi(sum.pending), running: numi(sum.running), failed: numi(sum.failed), skip: numi(sum.skip) },
    enc_version: o.enc_version || "—",
    settings: obj(o.settings), usage: obj(o.usage),
    server: { host: String(obj(o.server).host || "127.0.0.1"), port: numi(obj(o.server).port), token_required: !!obj(o.server).token_required },
  };
}

async function fetchState(quiet) {
  if (stateReq) return stateReq;
  stateReq = (async () => {
    try {
      const q = S.account ? "?account=" + encodeURIComponent(S.account) : "";
      S.state = normalizeState(await api("GET", "/api/state" + q));
      S.stateErr = "";
    } catch (err) {
      if (!err.silent) S.stateErr = err.message || String(err);
      if (!quiet && !err.silent) reportErr(err);
    } finally { stateReq = null; }
    render();
  })();
  return stateReq;
}

async function loadHealth() {
  try { S.health = await api("GET", "/api/health"); } catch (e) { S.health = null; }
  renderSidebar(); renderAbout();
}

async function loadQuiz() {
  try { S.quiz = await api("GET", "/api/quiz/stats"); S.quizErr = ""; }
  catch (e) { S.quiz = null; S.quizErr = e.silent ? "" : e.message || "加载失败"; }
  renderQuiz();
}

/* ───────────────── 空态 / 面板 ───────────────── */
function emptyBox(title, hint, action) {
  const b = h("div", "empty", tx("p", "empty-title", title), tx("p", "empty-hint", hint));
  if (action) b.append(action);
  return b;
}
function panel(title, extra, ...body) {
  const p = h("section", "panel");
  const head = h("div", "panel-head", tx("h2", null, title));
  if (extra) head.append(extra);
  p.append(head);
  for (const b of body) if (b) p.append(b);
  return p;
}

/* ───────────────── 渲染：框架 ───────────────── */
function render() { renderBanner(); renderSidebar(); renderHeader(); renderScope(); renderView(); }

function renderBanner() {
  const b = D.banner; if (!b) return;
  if (!S.stateErr) { b.hidden = true; set(b); return; }
  b.hidden = false;
  set(b,
    h("span", "banner-txt", "无法获取面板状态：", tx("b", "mono", S.stateErr)),
    tx("span", "banner-hint", "后端可能尚未就绪（例如 /api/state 返回 404）；页面在恢复后会自动刷新。"),
    btnEl("重试", "sm", () => fetchState()));
}

function updateNav() {
  if (!D.nav) return;
  for (const b of $$(".nav-item", D.nav)) {
    if (b.dataset.view === S.view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
}

function renderSidebar() {
  const st = S.state, accs = st ? st.accounts : [];
  if (D.acctList) {
    if (!accs.length) set(D.acctList, h("li", "acct-empty", st ? "还没有账号" : "等待后端…"));
    else set(D.acctList, accs.map(a => {
      const li = h("li", "acct" + (S.account === a.name ? " on" : ""), h("span", "dot " + (a.status === "ok" ? "ok" : "err")),
        tx("span", "acct-name", a.name),
        tx("span", "acct-prog mono", `${numi(a.tasks && a.tasks.done)}/${numi(a.tasks && a.tasks.total)}`));
      li.tabIndex = 0;
      li.onclick = () => setScope(a.name);
      li.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setScope(a.name); } };
      if (a.last_err) li.title = a.last_err;
      return li;
    }));
  }
  const srv = st ? st.server : null;
  if (D.sideServer) D.sideServer.textContent = srv && srv.port ? `${srv.host}:${srv.port}` : location.host;
  if (D.sideEnc) D.sideEnc.textContent = "enc " + (st ? st.enc_version : "—");
  if (D.sideUptime) D.sideUptime.textContent = S.health ? "uptime " + dur(S.health.uptime_s) : "—";
}

function renderHeader() {
  const t = TITLES[S.view] || TITLES.overview;
  if (D.pageTitle) D.pageTitle.textContent = t[0];
  if (D.pageCrumb) D.pageCrumb.textContent = t[1];
  const st = S.state, job = st && st.job, running = !!(st && st.running);
  if (D.statusPill) {
    const tone = running ? "run" : S.stateErr ? "err" : "plain";
    let label;
    if (running) {
      const pg = (job && job.progress) || {};
      const num2 = pg.total ? ` ${numi(pg.done)}/${numi(pg.total)}` : "";
      label = ((job && job.label) || KIND_LABEL[job && job.kind] || "任务") + num2;
    }
    else if (S.stateErr) label = "后端离线";
    else if (!st) label = "未连接";
    else label = "空闲";
    D.statusPill.className = "pill " + tone;
    set(D.statusPill, h("i"), tx("span", null, label));
  }
  const busy = S.busy || running;
  for (const b of [D.btnScan, D.btnRun, D.btnDry]) if (b) b.disabled = busy;
  if (D.btnStop) D.btnStop.disabled = !running || S.busy;
  updateNav();
}

function renderScope() {
  const st = S.state, accs = st ? st.accounts : [];
  if (D.scopeAccount) {
    set(D.scopeAccount, opt("", "全部账号"), accs.map(a => opt(a.name, a.name)));
    D.scopeAccount.value = accs.some(a => a.name === S.account) ? S.account : "";
  }
}

function renderView() {
  if (!D.content) return;
  for (const v of $$(".view", D.content)) v.classList.toggle("on", v.id === "view-" + S.view);
  if (S.view === "overview") { renderOverview(); }
  else if (S.view === "tasks") { renderTaskFilters(); renderTasks(); }
  else if (S.view === "logs") renderLogs();
  else if (S.view === "runs") renderRuns();
  else renderSettings();
  updateNav();
}

/* ───────────────── 渲染：总览 ───────────────── */
function renderOverview() {
  const st = S.state;
  const sum = st ? st.summary : { total: 0, done: 0, pending: 0, running: 0, failed: 0, skip: 0 };
  if (D.kpis) {
    const cards = [
      ["总任务点", num(sum.total), `${num(sum.pending)} 待办 · ${num(sum.skip)} 跳过`, false],
      ["已完成", num(sum.done), `${pct(sum.done, sum.total)}% 完成率`, false],
      ["进行中", num(sum.running), st && st.running ? "有任务在跑" : "当前空闲", false],
      ["失败", num(sum.failed), sum.failed ? "可在任务点页筛选重试" : "没有失败记录", sum.failed > 0],
    ];
    set(D.kpis, cards.map(([l, v, s, bad]) => h("div", "kpi" + (bad ? " bad" : ""), tx("div", "kpi-label", l), tx("div", "kpi-num", v), tx("div", "kpi-sub", s))),
      h("div", "kpi-wide",
        bar(ratio(sum.done, sum.total), "ok"),
        tx("span", "mono muted", `${num(sum.done)} / ${num(sum.total)} · ${pct(sum.done, sum.total)}%`),
        tx("span", "muted tiny", "总进度")));
  }
  renderJobCard();
  renderByType();
  renderCourses();
  renderJobs();
}

function renderJobCard() {
  const slot = D.jobSlot; if (!slot) return;
  D.jobElapsed = null;
  const st = S.state;
  if (!st) { set(slot); return; }
  const job = st.job;
  if (!job) {
    const last = st.jobs[0];
    if (!last) {
      set(slot, panel("进行中", null, emptyBox("当前没有任务在跑", "先点右上角「抓取课程」同步课程列表，再用「开始刷课」处理任务点。")));
      return;
    }
    set(slot, panel("最近一次任务", tx("span", "mono muted", last.id ? "job #" + last.id : ""),
      h("div", "job-card idle",
        badge(JOB_TXT[last.status] || last.status || "—", STATE_TONE[last.status] || "plain"),
        tx("span", "job-title", last.label || KIND_LABEL[last.kind] || "任务"),
        tx("span", "muted mono", last.finished_at ? rel(last.finished_at) : rel(last.created_at)),
        last.error ? tx("span", "job-err", last.error) : null)));
    return;
  }
  const p = job.progress && typeof job.progress === "object" ? job.progress : {};
  const done = numi(p.done), total = numi(p.total), r = ratio(done, total);
  const el = tx("span", "mono muted", "");
  D.jobElapsed = { el, started: tsMs(job.started_at || job.created_at) };
  const body = h("div", "job-card",
    h("div", "job-top",
      tx("div", "job-title", job.label || KIND_LABEL[job.kind] || "任务"),
      h("div", "job-sub mono", `#${job.id} · ${KIND_LABEL[job.kind] || job.kind} · 已运行 `, el)),
    h("div", "job-bar", bar(r, "run"), tx("span", "mono", `${num(done)}/${num(total)} · ${Math.round(r * 100)}%`)),
    tx("div", "job-msg", p.message || "等待进度…"),
    h("div", "job-actions", btnEl("停止", "danger sm", doStop)));
  set(slot, panel("进行中", badge(JOB_TXT[job.status] || job.status || "运行中", "run"), body));
  tickElapsed();
}

function tickElapsed() {
  const t = D.jobElapsed; if (!t) return;
  t.el.textContent = cdur(t.started ? Math.max(0, (Date.now() - t.started) / 1000) : 0);
}

function renderByType() {
  const grid = D.typeGrid; if (!grid) return;
  const rows = S.state ? S.state.by_type : [];
  if (!rows.length) { set(grid, h("div", "pad muted", S.state ? "暂无类型统计" : "等待后端数据…")); return; }
  set(grid, rows.map(t => {
    const total = numi(t.total), done = numi(t.done), failed = numi(t.failed);
    return h("div", "type-row",
      tx("span", "type-name", TYPE_LABEL[t.type] || t.type || "—"),
      bar(ratio(done, total), "ok"),
      tx("span", "mono type-num", `${num(done)}/${num(total)}`),
      tx("span", "type-fail mono" + (failed ? " bad" : ""), `失败 ${num(failed)}`));
  }));
}

function renderCourses() {
  const body = D.courseBody; if (!body) return;
  const rows = S.state ? S.state.courses : [];
  if (D.coursesMeta) D.coursesMeta.textContent = rows.length ? `${rows.length} 门课` : "";
  if (!rows.length) {
    set(body);
    if (D.courseEmpty) {
      D.courseEmpty.hidden = false;
      set(D.courseEmpty, emptyBox(S.state ? "还没有课程" : "等待后端数据…",
        S.state ? "点顶部「抓取课程」，或用设置页登录账号后再抓取。" : "后端就绪后这里会自动出现课程列表。"));
    }
    return;
  }
  if (D.courseEmpty) D.courseEmpty.hidden = true;
  set(body, rows.map(c => {
    const tr = h("tr", "clickable");
    tr.onclick = () => { S.filters.course = String(c.id); S.filters.state = ""; S.filters.q = ""; S.taskPage = 0; go("tasks"); };
    tr.append(
      h("td", "cell-main", tx("div", "cell-title", c.title || "未命名课程"), tx("div", "cell-sub mono", c.account || "")),
      tx("td", "muted", c.teacher || "—"),
      tx("td", "mono", num(c.chapters)),
      h("td", "cell-prog", bar(Number(c.progress) || 0, "ok"), tx("span", "mono", Math.round((Number(c.progress) || 0) * 100) + "%")),
      tx("td", "mono", `${num(c.done)}/${num(c.total)}`),
      tx("td", "mono" + (numi(c.failed) ? " cell-err" : ""), numi(c.failed) ? String(c.failed) : "—"));
    return tr;
  }));
}

function renderJobs() {
  const box = D.recentJobs; if (!box) return;
  const rows = S.state ? S.state.jobs : [];
  if (!rows.length) {
    set(box);
    if (D.jobsEmpty) {
      D.jobsEmpty.hidden = false;
      set(D.jobsEmpty, emptyBox("还没有任务记录", "抓取课程或开始刷课后，这里会列出最近 20 条任务。"));
    }
    return;
  }
  if (D.jobsEmpty) D.jobsEmpty.hidden = true;
  set(box, rows.slice(0, 20).map(j => h("li", null,
    badge(JOB_TXT[j.status] || j.status || "—", STATE_TONE[j.status] || "plain"),
    tx("span", "jl-label", j.label || KIND_LABEL[j.kind] || "任务"),
    tx("span", "jl-id", j.id ? "#" + j.id : ""),
    tx("span", "jl-when", j.finished_at ? rel(j.finished_at) : j.started_at ? rel(j.started_at) : rel(j.created_at)))));
}

/* ───────────────── 渲染：任务点 ───────────────── */
function renderTaskFilters() {
  const st = S.state;
  if (D.stateChips) {
    set(D.stateChips, FILTER_STATES.map(([v, label]) => {
      const b = h("button", "chip" + (S.filters.state === v ? " on" : ""), label);
      b.type = "button";
      b.onclick = () => { S.filters.state = v; S.taskPage = 0; renderTaskFilters(); loadTasks(); };
      return b;
    }));
  }
  if (D.fType) {
    const cur = S.filters.type;
    set(D.fType, opt("", "全部类型"), TYPES.map(t => opt(t, TYPE_LABEL[t])));
    D.fType.value = TYPES.indexOf(cur) >= 0 ? cur : "";
  }
  if (D.fCourse) {
    const cur = S.filters.course;
    const cs = st ? st.courses : [];
    set(D.fCourse, opt("", "全部课程"), cs.map(c => opt(String(c.id), c.title || "课程 " + c.id)));
    D.fCourse.value = cs.some(c => String(c.id) === cur) ? cur : "";
  }
}

async function loadTasks() {
  const seq = ++tasksSeq;
  const q = new URLSearchParams();
  if (S.account) q.set("account", S.account);
  if (S.filters.state) q.set("state", S.filters.state);
  if (S.filters.type) q.set("type", S.filters.type);
  if (S.filters.course) q.set("course_pk", S.filters.course);
  if (S.filters.q) q.set("q", S.filters.q);
  q.set("limit", String(PAGE));
  q.set("offset", String(S.taskPage * PAGE));
  S.tasks.loading = true; renderTasks();
  try {
    const d = await api("GET", "/api/tasks?" + q.toString());
    if (seq !== tasksSeq) return;
    S.tasks.items = Array.isArray(d.items) ? d.items : [];
    S.tasks.total = numi(d.total);
    S.tasks.error = "";
  } catch (err) {
    if (seq !== tasksSeq) return;
    S.tasks.items = []; S.tasks.total = 0;
    S.tasks.error = err.silent ? "" : err.message || "加载失败";
  } finally {
    if (seq === tasksSeq) { S.tasks.loading = false; renderTasks(); }
  }
}

function taskRow(t) {
  const tr = h("tr");
  const state = String(t.state || "").toLowerCase();
  const play = Number(t.play_time) || 0, durS = Number(t.duration) || 0;
  const prog = h("td", "cell-prog");
  if (t.type === "video" && durS > 0) prog.append(bar(ratio(play, durS), "ok"), tx("span", "mono", `${cdur(play)}/${cdur(durS)}`));
  else prog.append(tx("span", "muted mono", "—"));
  const errCell = h("td", "cell-err");
  if (t.last_err) { const s = tx("span", null, t.last_err); s.title = t.last_err; errCell.append(s); }
  else errCell.append(tx("span", "muted", "—"));
  const titleEl = tx("div", "cell-title", t.title || "（无标题）");
  titleEl.title = t.title || "";
  tr.append(
    h("td", null, badge(TYPE_LABEL[t.type] || t.type || "—", "")),
    h("td", "cell-main", titleEl, tx("div", "cell-sub", `${t.course || "—"} · ${t.chapter || "—"}`)),
    h("td", null, badge(STATE_LABEL[state] || state || "—", STATE_TONE[state] || "plain")),
    prog,
    tx("td", "mono", num(t.attempts)),
    errCell,
    tx("td", "mono muted nowrap", rel(t.updated_ts)));
  return tr;
}

function renderTasks() {
  const body = D.taskBody; if (!body) return;
  const items = S.tasks.items;
  if (!items.length) {
    set(body);
    if (D.tasksEmpty) {
      D.tasksEmpty.hidden = false;
      const st = S.state;
      let title = "没有任务点", hint;
      if (S.tasks.error) { title = "加载失败"; hint = S.tasks.error; }
      else if (!st) hint = "后端尚未就绪（/api/state 未成功返回），恢复后自动刷新。";
      else if (!st.accounts.length) hint = "还没有账号 → 去设置页粘贴 cookie 登录，再回来抓取课程。";
      else if (S.filters.q || S.filters.state || S.filters.type || S.filters.course) hint = "当前筛选条件下没有任务点，换个筛选试试。";
      else hint = "还没有任务点 → 先到总览页点「抓取课程」。";
      set(D.tasksEmpty, emptyBox(title, hint));
    }
  } else {
    if (D.tasksEmpty) D.tasksEmpty.hidden = true;
    set(body, items.map(taskRow));
  }
  const total = S.tasks.total;
  const from = total ? S.taskPage * PAGE + 1 : 0;
  const to = Math.min(total, (S.taskPage + 1) * PAGE);
  if (D.pgInfo) D.pgInfo.textContent = total ? `第 ${num(from)}–${num(to)} / 共 ${num(total)}` : S.tasks.loading ? "加载中…" : "共 0";
  if (D.pgPrev) D.pgPrev.disabled = S.taskPage <= 0;
  if (D.pgNext) D.pgNext.disabled = (S.taskPage + 1) * PAGE >= total;
}

/* ───────────────── 渲染：日志 ───────────────── */
const matchLog = l => !S.filter || (l.text + " " + l.level).toLowerCase().indexOf(S.filter) >= 0;

function logLineEl(l) {
  return h("div", "log-line lv-" + (LEVEL_TONE[l.level] || "info"),
    tx("span", "log-seq", l.seq ? String(l.seq) : ""),
    tx("span", "log-time", l.ts ? hm(tsMs(l.ts)) : ""),
    tx("span", "log-lv", l.level || "INFO"),
    tx("span", "log-txt", l.text));
}

function renderLogs() {
  const body = D.logBody; if (!body) return;
  body.replaceChildren();
  if (!S.logs.length) {
    body.append(emptyBox("等待日志输出", "任务跑起来之后，这里会实时滚动显示上游请求与上报结果。"));
  } else {
    const frag = document.createDocumentFragment();
    let n = 0;
    for (const l of S.logs) if (matchLog(l)) { frag.append(logLineEl(l)); n++; }
    body.append(frag);
    if (!n) body.append(emptyBox("没有匹配的日志", "换个关键字，或清空过滤框。"));
  }
  if (S.follow) body.scrollTop = body.scrollHeight;
  updateFollowUI();
  setConn();
}

function appendLogsDom(items) {
  const body = D.logBody; if (!body) return;
  if (!items.length) return;
  if (!S.logs.length || body.querySelector(".empty")) body.replaceChildren();
  const frag = document.createDocumentFragment();
  let added = 0;
  for (const l of items) if (matchLog(l)) { frag.append(logLineEl(l)); added++; }
  if (added) body.append(frag);
  while (body.children.length > MAX_LOG) body.firstElementChild.remove();
  if (S.follow) body.scrollTop = body.scrollHeight;
}

function updateFollowUI() {
  if (D.logFollow) D.logFollow.checked = S.follow;
  if (D.logResume) D.logResume.hidden = S.follow;
}

function setConn() {
  const c = D.conn; if (!c) return;
  const mode = S.mode;
  const label = mode === "sse" ? "在线" : mode === "poll" ? "轮询" : "连接中";
  c.className = "conn " + (mode === "sse" ? "ok" : mode === "poll" ? "warn" : "idle");
  set(c, h("i"), tx("em", null, label));
  c.title = mode === "sse" ? "SSE 实时推送已连接" : mode === "poll" ? "SSE 断开，正在按 2 秒轮询 /api/logs" : "正在连接事件流";
}

function addLog(item) {
  if (!item) return false;
  const seq = numi(item.seq);
  if (seq && S.seen.has(seq)) return false;
  if (seq) { S.seen.add(seq); if (seq > S.lastSeq) S.lastSeq = seq; }
  S.logs.push({ seq, ts: item.ts, level: String(item.level || "INFO").toUpperCase(), text: item.text === null || item.text === undefined ? "" : String(item.text) });
  if (S.logs.length > MAX_LOG) {
    S.logs.splice(0, S.logs.length - MAX_LOG);
    if (S.seen.size > MAX_LOG * 3) S.seen = new Set(S.logs.map(l => l.seq).filter(Boolean));
  }
  return true;
}

async function pollLogs() {
  try {
    const d = await api("GET", `/api/logs?after=${S.lastSeq || 0}&limit=300`);
    const items = Array.isArray(d.items) ? d.items : [];
    const fresh = items.filter(addLog);
    if (fresh.length) appendLogsDom(fresh);
    else if (!items.length && numi(d.last) > S.lastSeq) S.lastSeq = numi(d.last);
    if (!S.logs.length) renderLogs();
  } catch (e) { /* 后端未就绪时静默，避免刷屏 */ }
}

function startPolling() {
  if (S.logPolling) return;
  S.logPolling = true;
  S.pollTimer = setInterval(pollLogs, 2000);
  pollLogs();
}
function stopPolling() {
  if (!S.logPolling) return;
  S.logPolling = false;
  clearInterval(S.pollTimer); S.pollTimer = 0;
}
function closeStream() {
  if (!S.es) return;
  try { S.es.close(); } catch (e) { /* 忽略 */ }
  S.es = null;
}
function scheduleReconnect() {
  if (S.reconnectTimer) return;
  const wait = S.backoff;
  S.backoff = Math.min(S.backoff * 2, 15000);
  S.reconnectTimer = setTimeout(() => { S.reconnectTimer = 0; connectStream(); }, wait);
}

function connectStream() {
  closeStream();
  if (typeof EventSource !== "function") { S.mode = "poll"; startPolling(); setConn(); return; }
  const url = "/api/events" + (S.token ? "?token=" + encodeURIComponent(S.token) : "");
  let src;
  try { src = new EventSource(url); } catch (e) { S.mode = "poll"; startPolling(); setConn(); return; }
  S.es = src;
  src.onopen = () => {
    S.backoff = 3000; S.mode = "sse"; stopPolling(); setConn();
    fetchState(true); pollLogs();
  };
  src.addEventListener("log", ev => {
    const d = parseFrame(ev.data);
    if (d && addLog(d)) { if (!D.logBody || D.logBody.querySelector(".empty")) renderLogs(); else appendLogsDom([d]); }
  });
  src.addEventListener("job", ev => { const d = parseFrame(ev.data); if (d) ingestJob(d); });
  src.addEventListener("state", () => fetchState(true));
  src.onerror = () => {
    closeStream();
    S.mode = "poll"; startPolling(); setConn();
    scheduleReconnect();
  };
}

function parseFrame(raw) { if (!raw) return null; try { return JSON.parse(raw); } catch (e) { return null; } }

function ingestJob(job) {
  const st = S.state;
  if (!st) { fetchState(true); return; }
  const prev = S.jobStatus[job.id];
  S.jobStatus[job.id] = job.status;
  st.jobs = [job, ...st.jobs.filter(j => j.id !== job.id)].slice(0, 20);
  const live = job.status === "running" || job.status === "queued";
  st.running = live;
  st.job = live ? job : null;
  if (prev && prev !== job.status) {
    const tone = job.status === "done" ? "ok" : job.status === "failed" ? "err" : job.status === "cancelled" ? "warn" : "info";
    toast(`${job.label || "任务"} · ${JOB_TXT[job.status] || job.status}` + (job.error ? "：" + job.error : ""), tone, job.status === "failed" ? 8000 : 4000);
    if (!live) { loadTasks(); loadRuns(); }
  }
  render();
  fetchState(true);
}

/* ───────────────── 渲染：请求记录 ───────────────── */
async function loadRuns() {
  try {
    const d = await api("GET", "/api/runs?limit=50");
    S.runs = Array.isArray(d.items) ? d.items : [];
    S.runsErr = "";
  } catch (e) {
    S.runs = []; S.runsErr = e.silent ? "" : e.message || "加载失败";
  }
  renderRuns();
}

function renderRuns() {
  const body = D.runBody; if (!body) return;
  if (!S.runs.length) {
    set(body);
    if (D.runsEmpty) {
      D.runsEmpty.hidden = false;
      set(D.runsEmpty, emptyBox(S.runsErr ? "加载失败" : "还没有请求记录",
        S.runsErr || (S.state ? "跑过任务之后，这里会显示上游调用明细（方法、路径、状态码、耗时）。" : "后端尚未就绪，恢复后自动加载。")));
    }
    return;
  }
  if (D.runsEmpty) D.runsEmpty.hidden = true;
  set(body, S.runs.map(r => {
    const code = numi(r.status);
    const when = tx("td", "mono nowrap", hm(tsMs(r.ts)));
    when.title = dtStr(tsMs(r.ts)) + " " + hm(tsMs(r.ts));
    const snip = tx("td", "cell-snip mono", r.snippet || "");
    snip.title = r.snippet || "";
    const path = tx("td", "cell-path mono", r.path || "");
    path.title = r.path || "";
    const tr = h("tr");
    tr.append(
      when,
      tx("td", null, r.account || "—"),
      tx("td", "mono", r.method || "—"),
      path,
      h("td", null, tx("span", "code " + (code < 300 ? "ok" : code < 400 ? "warn" : "err"), String(code || "—"))),
      tx("td", "mono nowrap", numi(r.elapsed_ms) + " ms"),
      snip);
    return tr;
  }));
}

/* ───────────────── 渲染：设置 ───────────────── */
function renderSettings() {
  const st = S.state;
  if (D.encCurrent) D.encCurrent.textContent = "当前：" + (st ? st.enc_version : "—");
  if (D.acctManage) {
    const accs = st ? st.accounts : [];
    if (!accs.length) set(D.acctManage, h("li", null, emptyBox("还没有账号", "先填账号标识后点「添加账号」，再用下方粘贴 cookie 登录。")));
    else set(D.acctManage, accs.map(a => {
      const li = h("li");
      const t = a.tasks || {};
      li.append(h("span", "dot " + (a.status === "ok" ? "ok" : "err")),
        tx("span", "am-name", a.name),
        badge(a.status === "ok" ? "已登录" : "需登录", a.status === "ok" ? "ok" : "err"),
        tx("span", "am-meta", `${numi(a.courses)} 门课 · ${numi(t.done)}/${numi(t.total)} 任务点 · 登录 ${a.last_login_ts ? rel(a.last_login_ts) : "—"}` + (a.last_err ? " · " + a.last_err : "")),
        btnEl("用此账号登录", "sm", () => doBrowserLogin(a.name)));
      const del = btnEl("删除", "sm", null);
      armBtn(del, "确认删除？", () => deleteAccount(a.name));
      li.append(del);
      return li;
    }));
  }
  renderQuiz();
  renderAbout();
}

function renderQuiz() {
  const box = D.quizStats; if (!box) return;
  if (!S.quiz) { set(box, tx("p", "muted", S.quizErr || "暂无数据（后端就绪后自动加载）")); return; }
  const rows = Array.isArray(S.quiz.by_source) ? S.quiz.by_source : [];
  set(box,
    h("div", "quiz-total", tx("span", "mono big", num(S.quiz.total)), tx("span", "muted", "道题")),
    h("div", "quiz-rows", rows.length
      ? rows.map(r => h("div", "quiz-row", tx("span", null, r.source || "—"), bar(ratio(r.n, S.quiz.total), "ok"), tx("span", "mono", num(r.n))))
      : tx("p", "muted tiny", "还没有题目来源统计")));
}

function renderAbout() {
  const box = D.about; if (!box) return;
  const hl = S.health || {}, st = S.state || {}, srv = st.server || {};
  const rows = [
    ["应用", hl.app || "chaoxing-web"],
    ["版本", hl.version || "—"],
    ["Python", hl.python || "—"],
    ["运行时长", hl.uptime_s !== undefined ? dur(hl.uptime_s) : "—"],
    ["服务地址", srv.port ? `${srv.host}:${srv.port}` : location.host],
    ["加密版本", st.enc_version || "—"],
    ["访问口令", srv.token_required ? "已开启" : "未开启"],
    ["今日用量", st.usage && st.usage.today_minutes !== undefined ? fix1(st.usage.today_minutes) + " 分钟" : "—"],
    ["数据目录", "由 CLI 启动参数决定（--data-dir）"],
  ];
  set(box, rows.flatMap(([k, v]) => [tx("dt", null, k), tx("dd", null, String(v))]));
}

/* ───────────────── 动作 ───────────────── */
function go(view) {
  const v = VIEWS.indexOf(view) >= 0 ? view : "overview";
  S.view = v;
  try { history.replaceState(null, "", "#" + v); } catch (e) { location.hash = v; }
  applyView();
  if (v === "tasks") loadTasks();
  else if (v === "runs") loadRuns();
  else if (v === "settings" && !S.quiz) loadQuiz();
  else if (v === "logs") renderLogs();
}

function applyView() {
  const raw = String(location.hash || "").replace(/^#/, "").split("&")[0];
  S.view = VIEWS.indexOf(raw) >= 0 ? raw : "overview";
  render();
}

function setScope(name) {
  S.account = name || "";
  S.taskPage = 0;
  render();
  fetchState(true);
  if (S.view === "tasks") loadTasks();
  if (S.mode === "sse" || S.mode === "poll") setConn();
}

const firstAccountName = () => {
  const st = S.state;
  return st && st.accounts.length ? st.accounts[0].name : "";
};

const doScan = () => withBusy(async () => {
  const d = await api("POST", "/api/scan", { name: S.account || null, include_folders: true });
  toast("已提交抓取任务" + (d && d.job ? ` · ${d.job.label || "job #" + d.job.id}` : ""), "ok");
  await fetchState(true);
});

async function doStop() {
  const b = D.btnStop; if (b) b.disabled = true;
  try { await api("POST", "/api/stop", {}); toast("已发送停止请求", "warn"); }
  catch (err) { reportErr(err); }
  finally { await fetchState(true); }
}

async function doReset() {
  try {
    const d = await api("POST", "/api/reset", { name: S.account || null, course_pk: null, types: null });
    toast(`已重置 ${numi(d.reset)} 个任务点`, "ok");
    await fetchState(true); loadTasks();
  } catch (err) { reportErr(err); }
}

async function setEnc(version) {
  try {
    const d = await api("POST", "/api/enc", { version });
    toast("加密版本已切换为 " + (d.enc_version || version), "ok");
    await fetchState(true);
  } catch (err) { reportErr(err); }
}

async function deleteAccount(name) {
  try {
    const d = await api("DELETE", "/api/accounts/" + encodeURIComponent(name));
    toast(`已删除账号 ${d.deleted || name}`, "ok");
  } catch (err) { reportErr(err); }
  await fetchState(true);
}

async function addAccount() {
  const name = (D.acctName && D.acctName.value.trim()) || "";
  if (!name) { toast("账号标识不能为空", "err"); return; }
  try {
    await api("POST", "/api/accounts", { name });
    toast(`已添加账号 ${name}`, "ok");
  } catch (err) { reportErr(err); }
  await fetchState(true);
}

async function doLogin() {
  const box = D.cookieInput; if (!box) return;
  const who = (D.acctName && D.acctName.value.trim()) || S.account || firstAccountName();
  const cookie = box.value.trim();
  if (!who) { toast("先在上面填写账号标识（例如 me）", "err"); return; }
  if (!cookie) { toast("请先粘贴 cookie", "err"); return; }
  const b = D.cookieSubmit; if (b) b.disabled = true;
  try {
    const d = await api("POST", `/api/accounts/${encodeURIComponent(who)}/login`, { cookie });
    if (d && d.ok === false) toast(`登录失败：${d.message || "cookie 无效"}`, "err", 8000);
    else toast(`登录成功${d && d.puid ? " · puid " + d.puid : ""}`, "ok");
  } catch (err) { reportErr(err); }
  finally {
    if (b) b.disabled = false;
    box.value = "";
    fetchState(true); loadTasks();
  }
}

async function doBrowserLogin(name) {
  const who = name || (D.acctName && D.acctName.value.trim()) || S.account || firstAccountName();
  if (!who) { toast("先填写账号标识再弹浏览器登录", "err"); return; }
  try {
    await api("POST", `/api/accounts/${encodeURIComponent(who)}/browser-login`, {});
    toast("已在服务器那台机器上打开浏览器，请在那里完成登录", "ok", 7000);
    await fetchState(true);
  } catch (err) { reportErr(err); }
}

function saveDefaults() {
  const speed = Number(D.defSpeed.value), lanes = Number(D.defLanes.value);
  if (!isFinite(speed) || speed <= 0 || speed > 8) { toast("倍速需在 1–8 之间", "err"); return; }
  if (!isFinite(lanes) || lanes < 1 || lanes > 8) { toast("同账号并发需在 1–8 之间", "err"); return; }
  S.defaults.speed = speed;
  S.defaults.lanes = Math.round(lanes);
  writePrefs();
  toast("默认值已保存（只存在本机浏览器）", "ok");
}

/* ───────────────── 运行对话框 ───────────────── */
function openRun(dry) {
  const dlg = D.runDialog; if (!dlg) return;
  const st = S.state;
  if (D.runTitle) D.runTitle.textContent = dry ? "试跑（dry-run）" : "开始刷课";
  if (D.runSub) D.runSub.textContent = st ? `当前账号范围：${S.account || "全部账号"}` : "后端尚未连接，提交可能失败";
  const accs = st ? st.accounts : [];
  if (D.runAccount) {
    set(D.runAccount, opt("", "全部账号"), accs.map(a => opt(a.name, a.name)));
    D.runAccount.value = accs.some(a => a.name === S.account) ? S.account : "";
  }
  if (D.runCourse) {
    const cs = (st ? st.courses : []).filter(c => !S.account || c.account === S.account);
    set(D.runCourse, opt("", "全部课程"), cs.map(c => opt(String(c.id), `${c.title || "课程 " + c.id}${c.account ? " · " + c.account : ""}`)));
    D.runCourse.value = "";
  }
  const onTypes = st && Array.isArray(st.settings.types) ? st.settings.types : TYPES;
  if (D.runTypes) set(D.runTypes, TYPES.map(t => {
    const cb = h("input");
    cb.type = "checkbox"; cb.value = t; cb.checked = onTypes.indexOf(t) >= 0;
    return h("label", "pick", cb, tx("span", null, TYPE_LABEL[t]));
  }));
  const settings = st ? st.settings : {};
  if (D.runLimit) D.runLimit.value = "";
  if (D.runSpeed) D.runSpeed.value = String(Number(settings.speed) > 0 ? settings.speed : S.defaults.speed);
  if (D.runLanes) D.runLanes.value = String(numi(settings.task_concurrency) > 0 ? numi(settings.task_concurrency) : S.defaults.lanes);
  if (D.runRetries) D.runRetries.value = "";
  if (D.runDry) D.runDry.checked = !!dry;
  if (D.runErr) { D.runErr.hidden = true; D.runErr.textContent = ""; }
  updateRunSummary();
  if (typeof dlg.showModal === "function") { if (!dlg.open) dlg.showModal(); }
  else dlg.setAttribute("open", "");
  if (D.runLimit) setTimeout(() => D.runLimit.focus(), 30);
}

function closeRun() {
  const dlg = D.runDialog; if (!dlg) return;
  if (typeof dlg.close === "function" && dlg.open) dlg.close();
  else dlg.removeAttribute("open");
}

function selectedTypes() {
  if (!D.runTypes) return [];
  return $$("input[type=checkbox]", D.runTypes).filter(cb => cb.checked).map(cb => cb.value);
}

function updateRunSummary() {
  const el = D.runSummary; if (!el) return;
  const st = S.state;
  if (!st) { el.textContent = "后端尚未就绪，无法预估待办数量"; return; }
  const types = selectedTypes();
  let n = 0;
  for (const t of st.by_type) if (!types.length || types.indexOf(t.type) >= 0) n += numi(t.pending);
  const courseId = D.runCourse ? D.runCourse.value : "";
  const course = courseId ? st.courses.find(c => String(c.id) === courseId) : null;
  el.textContent = course
    ? `已限定「${course.title || "课程 " + course.id}」；账号范围内共 ${num(n)} 个待办任务点（课程内数量以任务点页为准）`
    : `共 ${num(n)} 个待办任务点将被处理`;
}

async function submitRun() {
  const errEl = D.runErr;
  const fail = msg => { if (errEl) { errEl.hidden = false; errEl.textContent = msg; } };
  const types = selectedTypes();
  if (!types.length) return fail("至少选择一个任务类型");
  const limitRaw = D.runLimit.value.trim();
  const limit = limitRaw ? Number(limitRaw) : null;
  if (limit !== null && (!isFinite(limit) || limit <= 0)) return fail("本次上限必须是正整数");
  const speedRaw = D.runSpeed.value.trim();
  const speed = speedRaw ? Number(speedRaw) : null;
  if (speed !== null && (!isFinite(speed) || speed < 1 || speed > 8)) return fail("倍速需在 1–8 之间");
  const lanesRaw = D.runLanes.value.trim();
  const lanes = lanesRaw ? Math.round(Number(lanesRaw)) : null;
  if (lanes !== null && (!isFinite(lanes) || lanes < 1 || lanes > 8)) return fail("同账号并发需在 1–8 之间");
  const retriesRaw = D.runRetries.value.trim();
  const retries = retriesRaw ? Number(retriesRaw) : null;
  if (retries !== null && (!isFinite(retries) || retries < 0 || retries > 10)) return fail("重试次数需在 0–10 之间");
  const coursePk = D.runCourse.value ? Number(D.runCourse.value) : null;
  if (errEl) { errEl.hidden = true; errEl.textContent = ""; }
  const body = {
    name: D.runAccount.value || null,
    dry_run: !!(D.runDry && D.runDry.checked),
    types,
    course_pk: coursePk,
    limit,
    speed,
    task_concurrency: lanes,
    retries,
  };
  if (D.runSubmit) D.runSubmit.disabled = true;
  try {
    const d = await api("POST", "/api/run", body);
    toast("已提交：" + ((d && d.job && (d.job.label || "job #" + d.job.id)) || "任务"), "ok");
    closeRun();
    await fetchState(true);
    if (S.view === "tasks") loadTasks();
  } catch (err) {
    if (!err.silent) fail(err.message || "提交失败");
  } finally {
    if (D.runSubmit) D.runSubmit.disabled = false;
  }
}

/* ───────────────── 访问口令 ───────────────── */
function openAuth(msg) {
  const m = D.auth; if (!m) return;
  if (D.authMsg && msg) D.authMsg.textContent = String(msg);
  const first = m.hidden;
  m.hidden = false;
  if (first && D.authInput) { D.authInput.value = ""; setTimeout(() => D.authInput.focus(), 30); }
}

function closeAuth() { if (D.auth) D.auth.hidden = true; }

async function saveToken() {
  const v = (D.authInput && D.authInput.value.trim()) || "";
  if (!v) { if (D.authMsg) D.authMsg.textContent = "口令不能为空"; return; }
  S.token = v;
  writeToken(v);
  closeAuth();
  toast("口令已保存，正在重试", "ok");
  await Promise.all([fetchState(true), loadHealth(), pollLogs()]);
  connectStream();
}

/* ───────────────── DOM 缓存与事件 ───────────────── */
function cacheDom() {
  const ids = ["nav", "acct-list", "side-add", "side-server", "side-enc", "side-uptime", "page-title", "page-crumb",
    "scope-account", "status-pill", "btn-scan", "btn-run", "btn-dry", "btn-stop", "banner", "content", "kpis",
    "job-slot", "courses-meta", "course-body", "course-empty", "recent-jobs", "jobs-empty", "type-grid", "state-chips",
    "f-type", "f-course", "f-q", "task-body", "tasks-empty", "pg-prev", "pg-info", "pg-next", "conn", "log-filter",
    "log-follow", "log-clear", "log-body", "log-resume", "run-body", "runs-empty", "acct-name", "acct-add",
    "acct-browser", "acct-manage", "cookie-input", "cookie-submit", "cookie-clear", "def-speed", "def-lanes",
    "def-save", "enc-current", "enc-v1", "enc-v2", "reset-btn", "quiz-stats", "about", "run-dialog", "run-form",
    "run-title", "run-sub", "run-account", "run-course", "run-limit", "run-speed", "run-lanes", "run-retries",
    "run-dry", "run-types", "run-summary", "run-err", "run-cancel", "run-submit", "auth", "auth-input", "auth-msg",
    "auth-save", "toasts"];
  for (const id of ids) D[id.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = document.getElementById(id);
}

function bind() {
  window.addEventListener("hashchange", () => applyView());
  document.addEventListener("click", ev => {
    const nav = ev.target.closest ? ev.target.closest("[data-view]") : null;
    if (nav && D.nav && D.nav.contains(nav)) { ev.preventDefault(); go(nav.dataset.view); }
  });
  if (D.sideAdd) D.sideAdd.onclick = () => go("settings");
  if (D.scopeAccount) D.scopeAccount.onchange = () => setScope(D.scopeAccount.value);
  if (D.btnScan) D.btnScan.onclick = doScan;
  if (D.btnRun) D.btnRun.onclick = () => openRun(false);
  if (D.btnDry) D.btnDry.onclick = () => openRun(true);
  if (D.btnStop) D.btnStop.onclick = doStop;

  if (D.pgPrev) D.pgPrev.onclick = () => { if (S.taskPage > 0) { S.taskPage--; loadTasks(); } };
  if (D.pgNext) D.pgNext.onclick = () => { S.taskPage++; loadTasks(); };
  if (D.fType) D.fType.onchange = () => { S.filters.type = D.fType.value; S.taskPage = 0; loadTasks(); };
  if (D.fCourse) D.fCourse.onchange = () => { S.filters.course = D.fCourse.value; S.taskPage = 0; loadTasks(); };
  if (D.fQ) D.fQ.oninput = debounce(() => { S.filters.q = D.fQ.value.trim(); S.taskPage = 0; loadTasks(); }, 300);

  if (D.logFilter) D.logFilter.oninput = () => { S.filter = D.logFilter.value.trim().toLowerCase(); renderLogs(); };
  if (D.logClear) D.logClear.onclick = () => { S.logs = []; S.seen = new Set(); renderLogs(); toast("已清屏（仅本地显示）", "info", 2200); };
  if (D.logFollow) D.logFollow.onchange = () => {
    S.follow = D.logFollow.checked;
    updateFollowUI();
    if (S.follow && D.logBody) D.logBody.scrollTop = D.logBody.scrollHeight;
  };
  if (D.logResume) D.logResume.onclick = () => { S.follow = true; updateFollowUI(); if (D.logBody) D.logBody.scrollTop = D.logBody.scrollHeight; };
  if (D.logBody) D.logBody.addEventListener("scroll", () => {
    const el = D.logBody;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (!atBottom && S.follow) { S.follow = false; updateFollowUI(); }
    else if (atBottom && !S.follow) { S.follow = true; updateFollowUI(); }
  });

  if (D.acctAdd) D.acctAdd.onclick = addAccount;
  if (D.acctBrowser) D.acctBrowser.onclick = () => doBrowserLogin("");
  if (D.cookieSubmit) D.cookieSubmit.onclick = doLogin;
  if (D.cookieClear) D.cookieClear.onclick = () => { if (D.cookieInput) D.cookieInput.value = ""; };
  if (D.defSave) D.defSave.onclick = saveDefaults;
  if (D.encV1) D.encV1.onclick = () => setEnc("v1");
  if (D.encV2) D.encV2.onclick = () => setEnc("v2");
  if (D.resetBtn) armBtn(D.resetBtn, "确认重置？", doReset);

  if (D.runForm) D.runForm.addEventListener("submit", ev => { ev.preventDefault(); submitRun(); });
  if (D.runCancel) D.runCancel.onclick = closeRun;
  if (D.runTypes) D.runTypes.addEventListener("change", updateRunSummary);
  if (D.runCourse) D.runCourse.onchange = updateRunSummary;
  if (D.runAccount) D.runAccount.onchange = updateRunSummary;
  if (D.runDry) D.runDry.onchange = updateRunSummary;

  if (D.authSave) D.authSave.onclick = saveToken;
  if (D.authInput) D.authInput.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); saveToken(); } });
  document.addEventListener("keydown", ev => {
    if (ev.key !== "Escape") return;
    if (D.runDialog && D.runDialog.open) closeRun();
    else if (D.auth && !D.auth.hidden) closeAuth();
  });
}

/* ───────────────── 启动 ───────────────── */
function boot() {
  cacheDom();
  initToken();
  readPrefs();
  bind();
  if (D.defSpeed) D.defSpeed.value = String(S.defaults.speed);
  if (D.defLanes) D.defLanes.value = String(S.defaults.lanes);
  applyView();
  connectStream();
  fetchState();
  loadHealth();
  pollLogs();
  setInterval(() => { const st = S.state; if (st && st.running) fetchState(true); }, 3000);
  setInterval(tickElapsed, 1000);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
})();
