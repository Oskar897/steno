/* Steno UI. Plain JavaScript, no build step, no network access beyond this server. */
"use strict";

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const pad2 = (n) => String(n).padStart(2, "0");
function clock(sec) {
  const t = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = t % 60;
  return hh ? `${hh}:${pad2(mm)}:${pad2(ss)}` : `${pad2(mm)}:${pad2(ss)}`;
}
function clockHms(sec) {
  const t = Math.max(0, Math.floor(sec || 0));
  return `${pad2(Math.floor(t / 3600))}:${pad2(Math.floor((t % 3600) / 60))}:${pad2(t % 60)}`;
}
const when = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "");
function durationOf(info) {
  const a = Date.parse(info.started_at), b = Date.parse(info.ended_at);
  return a && b && b >= a ? (b - a) / 1000 : null;
}
const clusterNo = (id) => { const m = /^[SR](\d+)$/.exec(id || ""); return m ? Number(m[1]) : null; };
function debounced(fn, ms) {
  let t = null;
  return () => { clearTimeout(t); t = setTimeout(() => guarded(fn), ms); };
}

function remember(key, value) {  // per-session (token) or persistent (prefs) storage, never fatal
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch (e) { /* storage unavailable */ }
  return null;
}
function tokenStore(value) {
  try {
    if (value === undefined) return sessionStorage.getItem("steno-token");
    sessionStorage.setItem("steno-token", value);
  } catch (e) { /* ignore */ }
  return null;
}

// ---------------------------------------------------------------- state and API

const PAGE = 3;  // meetings loaded into the feed at first, and per "Show older"
const S = {
  token: null,
  status: { recorder: {}, job: null, jobs: [], config: {} },
  projects: [], inbox: 0,
  view: "project",        // "project" | "people"
  sel: null,              // project id or "inbox"
  meetings: [],           // list for the selected project, oldest first
  shown: PAGE,
  feed: new Map(),        // session id -> {info, detail, docs, open, el, ...}
  loadingFeed: false, buffer: [], unknown: new Set(),
  stick: true, unseen: 0,
  opts: { mic: true, loop: true, keep: false },
  people: [], connected: false, fatal: false,
  jobStates: new Map(),
};

function initToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    tokenStore(fromUrl);
    S.token = fromUrl;
    history.replaceState(null, "", url.pathname);  // keep the token out of the address bar
  } else {
    S.token = tokenStore();
  }
}

async function api(method, path, body) {
  const opts = { method, headers: { "X-Steno-Token": S.token || "" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try { res = await fetch(path, opts); }
  catch (e) { throw new Error("Cannot reach Steno. Is it still running?"); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (res.status === 401) {
    S.fatal = true;
    throw new Error("Access token rejected. Open Steno again from its own window or address.");
  }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}
const missing = (e) => e && e.status === 404;  // endpoint or item not there (yet): not an error to show

function toast(text, kind) {
  const el = h("div", { class: "toast" + (kind ? " " + kind : ""), text: String(text) });
  el.addEventListener("click", () => el.remove());
  $("toasts").append(el);
  while ($("toasts").children.length > 4) $("toasts").firstChild.remove();
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 5000);
}
async function guarded(fn) {
  try { return await fn(); } catch (e) { toast(e instanceof Error ? e.message : e, "error"); return null; }
}

// ---------------------------------------------------------------- modal dialogs

function pickerAvailable() {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);
}
async function pickFolder() {  // path | null (cancelled) | undefined (no native picker)
  if (!pickerAvailable()) return undefined;
  try { return (await window.pywebview.api.pick_folder()) || null; } catch (e) { return null; }
}

/* fields: [{key, label, value, placeholder, browse, options:[{value,label}]}] -> values | null */
function formModal({ title, text, fields = [], ok = "OK" }) {
  return new Promise((resolve) => {
    const dlg = $("modal");
    const inputs = {};
    const finish = (v) => { resolve(v); if (dlg.open) dlg.close(); };
    const rows = fields.map((f) => {
      let input;
      if (f.options) {
        input = h("select", null, f.options.map((o) => h("option", { value: o.value, text: o.label })));
        input.value = f.value ?? f.options[0].value;
      } else {
        input = h("input", { class: "text", type: "text", value: f.value || "", placeholder: f.placeholder || "", autocomplete: "off" });
      }
      inputs[f.key] = input;
      const browse = f.browse && pickerAvailable()
        ? h("button", { class: "btn", type: "button", text: "Browse",
          onclick: async () => { const p = await pickFolder(); if (p) input.value = p; } }) : null;
      return h("label", null, f.label, h("div", { class: "inrow" }, input, browse));
    });
    const form = h("form", { class: "mform", onsubmit: (ev) => {
      ev.preventDefault();
      finish(Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value.trim()])));
    } },
    h("h3", { text: title }), text ? h("p", { text }) : null, rows,
    h("div", { class: "btns" },
      h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => finish(null) }),
      h("button", { class: "btn solid", type: "submit", text: ok })));
    dlg.replaceChildren(form);
    dlg.addEventListener("close", () => resolve(null), { once: true });  // Escape
    dlg.showModal();
    const first = form.querySelector("input, select");
    if (first) first.focus();
  });
}

// ---------------------------------------------------------------- projects and sidebar

const curProject = () => S.projects.find((p) => p.id === S.sel) || null;
const projectName = (id) => (id === "inbox" || !id ? "Inbox" : (S.projects.find((p) => p.id === id) || {}).name || "project");

function recProjectId() {
  const r = S.status.recorder || {};
  if (!r.session || r.state === "idle" || r.state === "error") return null;
  const key = r.project;
  if (!key) return "inbox";
  const p = S.projects.find((x) => x.id === key || x.path === key);
  return p ? p.id : key;
}

async function loadProjects() {
  try {
    const d = await api("GET", "/api/projects");
    S.projects = (d.projects || []).slice().sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
    S.inbox = d.inbox || 0;
  } catch (e) {
    if (!missing(e)) throw e;
    S.projects = []; S.inbox = 0;
  }
  const changed = ensureSelection();
  renderSide(); renderTop();
  if (changed) { S.shown = PAGE; S.stick = true; await loadFeed(); }
}

function ensureSelection() {  // returns true when the selected project had to change
  if (S.view === "people") return false;
  const valid = S.sel === "inbox" ? S.inbox > 0 : S.projects.some((p) => p.id === S.sel);
  if (valid) return false;
  const saved = remember("steno-project");
  const pick = [recProjectId(), saved, (S.projects[0] || {}).id, S.inbox > 0 ? "inbox" : null]
    .find((id) => id && (id === "inbox" ? S.inbox > 0 : S.projects.some((p) => p.id === id)));
  const next = pick || null;
  const changed = next !== S.sel;
  S.sel = next;
  if (changed) { S.feed.clear(); S.meetings = []; S.unknown.clear(); }
  return changed;
}

function selectProject(id) {
  S.view = "project"; S.sel = id; S.shown = PAGE; S.stick = true; S.unseen = 0;
  S.feed.clear(); S.meetings = []; S.unknown.clear();
  remember("steno-project", id);
  showView(); renderSide(); renderTop(); renderFeed();
  guarded(loadFeed);
}

function showPeople() {
  S.view = "people";
  showView(); renderSide();
  guarded(loadPeople);
}

function showView() {
  $("view-project").hidden = S.view !== "project";
  $("view-people").hidden = S.view !== "people";
}

function renderSide() {
  const live = recProjectId();
  const item = (id, name, count) => h("button", {
    class: "pitem" + (S.view === "project" && S.sel === id ? " active" : ""), type: "button",
    onclick: () => selectProject(id) },
  h("span", { class: "pname", text: name }),
  live === id ? h("i", { class: "dot", title: "Recording" }) : null,
  h("span", { class: "pcount", text: String(count) }));
  $("plist").replaceChildren(...S.projects.map((p) => item(p.id, p.name, p.meetings)));
  $("inbox-slot").replaceChildren(...(S.inbox > 0 ? [item("inbox", "Inbox", S.inbox)] : []));
  $("nav-people").classList.toggle("active", S.view === "people");
  $("people-count").textContent = S.people.length ? String(S.people.length) : "";
  renderSideFoot();
}

function renderSideFoot() {
  const cfg = S.status.config || {}, r = S.status.recorder || {};
  const lines = [];
  if (r.state === "recording") lines.push(h("div", null, h("b", { text: r.paused ? "PAUSED " : "REC " }), clockHms(r.elapsed_s)));
  lines.push(h("div", null, "ASR ", h("b", { text: cfg.asr_model || "-" })));
  lines.push(h("div", null, "LLM ", h("b", { text: cfg.llm_model || "-" })));
  lines.push(h("div", null, S.connected ? "CONNECTED" : "RECONNECTING", S.status.version ? ` · v${S.status.version}` : ""));
  $("side-foot").replaceChildren(...lines);
}

async function newProject() {
  const picked = await pickFolder();
  if (picked === null) return;
  const v = await formModal({
    title: "New project",
    text: "A project is a folder on disk. Meetings are saved in <folder>/Steno/.",
    fields: [
      { key: "path", label: "Parent folder", value: picked || "", placeholder: "Path of the folder to create it in", browse: true },
      { key: "name", label: "Project name", placeholder: "Name of the new folder" }],
    ok: "Create",
  });
  if (!v) return;
  if (!v.path || !v.name) { toast("Choose a parent folder and enter a name.", "error"); return; }
  const res = await guarded(() => api("POST", "/api/projects", { path: v.path, name: v.name, create: true }));
  if (res) await afterProjectAdded(res.project);
}

async function addProject() {
  let path = await pickFolder();
  if (path === null) return;
  if (path === undefined) {
    const v = await formModal({ title: "Add existing folder", fields: [{ key: "path", label: "Folder path", placeholder: "Path of an existing folder" }], ok: "Add" });
    path = v && v.path;
  }
  if (!path) return;
  const res = await guarded(() => api("POST", "/api/projects", { path }));
  if (res) await afterProjectAdded(res.project);
}

async function afterProjectAdded(project) {
  await guarded(loadProjects);
  if (project && project.id) selectProject(project.id);
}

async function forgetProject() {
  const p = curProject();
  if (!p) return;
  const ok = await formModal({ title: "Forget project", text: `Remove "${p.name}" from Steno? Its folder and meetings stay on disk.`, ok: "Forget" });
  if (!ok) return;
  if (await guarded(() => api("DELETE", `/api/projects/${encodeURIComponent(p.id)}`))) {
    S.sel = null; S.feed.clear();
    await guarded(loadProjects);
    renderTop(); renderFeed();
  }
}

// ---------------------------------------------------------------- top bar: recording controls and status strip

function renderTop() {
  const r = S.status.recorder || {}, st = r.state || "idle";
  const active = st === "starting" || st === "recording" || st === "stopping";
  const proj = curProject();
  $("proj-name").textContent = S.sel === "inbox" ? "Inbox" : proj ? proj.name : "";
  $("btn-forget").hidden = !proj;
  const noProject = !S.sel;
  $("rec-title").hidden = active;
  $("rec-title").disabled = noProject;
  const rec = $("btn-rec");
  rec.hidden = st === "recording";
  rec.disabled = noProject || st === "starting" || st === "stopping";
  $("btn-rec-label").textContent = st === "starting" ? "Loading models..." : st === "stopping" ? "Finishing..." : "Record";
  $("btn-pause").hidden = st !== "recording";
  $("btn-pause").textContent = r.paused ? "Resume" : "Pause";
  $("btn-stop").hidden = st !== "recording";
  const src = r.sources || [];
  const on = active ? { mic: src.includes("mic"), loop: src.includes("loopback"), keep: !!r.keep_audio } : S.opts;
  for (const [id, key] of [["tgl-mic", "mic"], ["tgl-loop", "loop"], ["tgl-keep", "keep"]]) {
    $(id).setAttribute("aria-pressed", String(!!on[key]));
    $(id).disabled = active;
  }
  $("empty").hidden = !(S.view === "project" && !S.sel && S.projects.length === 0 && S.inbox === 0);
  $("feed").parentElement.hidden = !$("empty").hidden;
  renderStrip();
  updateButtons();
}

function meter(label, lv) {
  if (!lv) return null;
  const n = Math.min(5, Math.round(Math.sqrt(Math.max(0, lv.level || 0)) * 5));
  return h("span", { class: "lab" }, label + " ", h("span", { class: "on", text: "▮".repeat(n) }), h("span", { class: "off", text: "▯".repeat(5 - n) }));
}

function renderStrip() {
  const r = S.status.recorder || {}, st = r.state || "idle", cfg = S.status.config || {};
  const live = st === "recording" || st === "stopping";
  const out = [];
  if (live) {
    out.push(h("span", { class: "lab" + (r.paused ? " paused" : "") }, h("i", { class: "dot" }), `${r.paused ? "PAUSED" : "REC"} ${clockHms(r.elapsed_s)}`));
  }
  const word = { idle: "READY", starting: "LOADING MODELS", stopping: `FINISHING${r.backlog ? " · " + r.backlog + " PENDING" : ""}`, error: "ERROR" }[st]
    || (r.paused ? "PAUSED" : r.backlog > 0 ? "TRANSCRIBING" : "LISTENING");
  out.push(h("span", { class: "lab" + (st === "error" ? " err" : "") }, "STATUS: " + word));
  if (live) {
    const lv = r.levels || {};
    out.push(meter("MIC", lv.mic), meter("SYS", lv.loopback));
    if (r.speaker) out.push(h("span", { class: "lab", text: "SPEAKER: " + r.speaker }));
    if (r.backlog > 0 && st === "recording") out.push(h("span", { class: "lab", text: `REFINING: ${cfg.asr_model || "ASR"} · ${r.backlog} QUEUED` }));
    const rp = recProjectId();
    if (rp && rp !== S.sel) {
      out.push(h("span", { class: "lab link", text: "IN " + projectName(rp), onclick: () => selectProject(rp) }));
    }
  }
  const job = S.status.job;
  if (job && job.state === "running") out.push(h("span", { class: "lab", text: `JOB: ${jobLabel(job)}${job.stage ? " · " + job.stage : ""}${job.total ? " " + job.done + "/" + job.total : ""}` }));
  $("strip").replaceChildren(...out.filter(Boolean));
}

async function startRecording() {
  if (!S.sel) return;
  remember("steno-opts", JSON.stringify(S.opts));
  const body = { project: S.sel, mic: S.opts.mic, loopback: S.opts.loop, keep_audio: S.opts.keep };
  const title = $("rec-title").value.trim();
  if (title) body.title = title;
  S.stick = true;
  const res = await guarded(() => api("POST", "/api/record/start", body));
  if (res) {
    if (res.recorder) S.status.recorder = res.recorder;
    $("rec-title").value = "";
    renderTop(); renderSide();
    scheduleFeed();
  }
}
const recordCmd = (cmd) => guarded(() => api("POST", "/api/record/" + cmd, {})).then((res) => {
  if (res && res.recorder) { S.status.recorder = res.recorder; renderTop(); }
});

// ---------------------------------------------------------------- feed: meetings smashed together

let feedSeq = 0;
const scheduleFeed = debounced(() => loadFeed(), 300);
const scheduleProjects = debounced(() => loadProjects(), 300);

async function loadFeed() {
  if (S.view !== "project" || !S.sel) { renderFeed(); return; }
  const my = ++feedSeq, sel = S.sel;
  let list;
  try { list = (await api("GET", `/api/projects/${encodeURIComponent(sel)}/meetings`)).meetings || []; }
  catch (e) { if (!missing(e)) throw e; list = []; }
  if (my !== feedSeq) return;
  S.meetings = list;
  const visible = list.slice(-S.shown);
  S.loadingFeed = true; S.buffer = [];
  try {
    await Promise.all(visible.map((info) => ensureMeeting(info).catch((e) => { if (!missing(e)) toast(e.message, "error"); })));
  } finally { S.loadingFeed = false; }
  if (my !== feedSeq) return;
  const keep = new Set(visible.map((i) => i.session));
  for (const k of [...S.feed.keys()]) if (!keep.has(k)) S.feed.delete(k);
  for (const ev of S.buffer) { const m = S.feed.get(ev.session); if (m && m.detail) appendSegment(m, ev.segment, true); }
  S.buffer = [];
  renderFeed();
}

async function fetchDetail(m) {
  try { m.detail = await api("GET", `/api/sessions/${encodeURIComponent(m.info.session)}`); }
  catch (e) { if (!missing(e)) throw e; m.detail = m.detail || { segments: [], speakers: [] }; }
  m.detail.segments = m.detail.segments || [];
  m.detail.speakers = m.detail.speakers || [];
  await loadDocs(m);
}

async function loadDocs(m) {
  for (const kind of ["summary", "todo"]) {
    const has = m.info["has_" + kind] || (m.detail && m.detail[kind]);
    let md = null;
    if (has) {
      try { md = (await api("GET", `/api/sessions/${encodeURIComponent(m.info.session)}/doc/${kind}`)).markdown || null; }
      catch (e) { md = (m.detail && m.detail[kind]) || null; }
    }
    m.docs[kind] = md;
  }
}

async function ensureMeeting(info) {
  let m = S.feed.get(info.session);
  if (!m) { m = { info, detail: null, docs: {}, open: {}, el: null, rows: new Map() }; S.feed.set(info.session, m); await fetchDetail(m); return; }
  const stale = m.info.recording !== info.recording || m.info.ended_at !== info.ended_at
    || m.info.has_summary !== info.has_summary || m.info.has_todo !== info.has_todo;
  m.info = info;
  if (stale || !m.detail) await fetchDetail(m);
}

function renderFeed() {
  const feed = $("feed");
  const prevTop = feed.scrollTop;
  feed.replaceChildren();
  if (!S.sel) { feed.append(h("p", { class: "hint", text: "No project selected." })); return; }
  if (S.meetings.length > S.shown) {
    feed.append(h("button", { class: "btn ghost small older", type: "button", text: `Show older meetings (${S.meetings.length - S.shown} more)`, onclick: showOlder }));
  }
  const visible = S.meetings.slice(-S.shown).map((i) => S.feed.get(i.session)).filter((m) => m && m.detail);
  if (!visible.length) feed.append(h("p", { class: "hint", text: S.loadingFeed ? "Loading..." : "No meetings yet. Press Record to start one." }));
  for (const m of visible) feed.append(buildMeeting(m));
  if (S.stick) scrollToEnd(); else feed.scrollTop = prevTop;
  updateLive(); renderJump();
}

async function showOlder() {
  const feed = $("feed");
  const below = feed.scrollHeight - feed.scrollTop;
  S.shown += PAGE; S.stick = false;
  await guarded(loadFeed);
  feed.scrollTop = feed.scrollHeight - below;
}

function jobLabel(job) {
  return { summary: "CLAUDE · SUMMARIZING", todo: "CLAUDE · EXTRACTING TO-DOS", clean: "CLAUDE · CLEANING UP", finalize: "FINALIZING" }[job.kind] || String(job.kind).toUpperCase();
}

function buildMeeting(m) {
  const info = m.info, sid = info.session;
  m.rows = new Map();
  const titleBtn = h("button", { class: "mtitle" + (info.title ? "" : " untitled"), type: "button", title: "Click to rename", text: info.title || "Meeting", onclick: () => renameMeeting(m, titleBtn) });
  m.durEl = h("span", { class: "mdur" });
  m.btns = {};
  const act = (kind, label, tip) => (m.btns[kind] = h("button", { class: "btn ghost small", type: "button", text: label, title: tip, "data-tip": tip, onclick: () => startJob(m, kind) }));
  m.jobEl = h("div", { class: "mjob" });
  m.docsEl = h("div", { class: "docs" });
  m.linesEl = h("div", { class: "lines" });
  m.el = h("section", { class: "meeting", "data-session": sid },
    h("div", { class: "mhead" },
      h("span", { class: "mdate", text: when(info.started_at) }), titleBtn, m.durEl,
      h("span", { class: "mactions" },
        act("summary", "Summary", "Summarize this meeting with Claude (transcript text only)"),
        act("todo", "To-do", "Extract explicit commitments"),
        act("clean", "Clean up", "Fix obvious transcription errors"),
        h("button", { class: "btn ghost small", type: "button", text: "Move to project...", onclick: () => moveMeeting(m) }))),
    m.jobEl, m.docsEl, m.linesEl);
  renderDocs(m);
  renderLines(m);
  updateJobLines(); updateButtons(); updateLive();
  return m.el;
}

function renderLines(m) {
  m.linesEl.replaceChildren(); m.rows = new Map();
  const segs = m.detail.segments;
  const frag = document.createDocumentFragment();
  segs.forEach((s, i) => frag.append(segRow(m, s, segs[i - 1])));
  m.linesEl.append(frag);
  syncEmptyNote(m);
}

function syncEmptyNote(m) {
  const note = m.linesEl.querySelector(".empty-meeting");
  if (m.detail.segments.length) { if (note) note.remove(); return; }
  if (!note) m.linesEl.append(h("div", { class: "empty-meeting", text: m.info.recording ? "Listening... lines appear a few seconds after you speak." : "No transcript lines." }));
}

function canName(m, seg) {
  const n = clusterNo(seg.speaker_id);
  if (n === null) return false;
  const sp = m.detail.speakers.find((x) => x.id === seg.speaker_id && x.name === seg.speaker);
  return sp ? !!sp.can_name : m.detail.view !== "final";
}

function segRow(m, seg, prev) {
  const first = !prev || prev.speaker !== seg.speaker || prev.speaker_id !== seg.speaker_id;
  const n = clusterNo(seg.speaker_id);
  const unknown = n !== null && seg.speaker === `Speaker ${n}`;
  const nameable = first && canName(m, seg);
  const who = first && seg.speaker ? h("button", {
    class: "who" + (unknown ? " unknown" : "") + (nameable ? " nameable" : ""), type: "button",
    title: nameable ? "Name this speaker" : null, text: seg.speaker + (unknown && nameable ? " ?" : "") }) : null;
  const p = h("p", { text: seg.text });
  const row = h("div", { class: "seg" + (seg.provisional ? " prov" : "") + (first ? " first" : ""), "data-seq": seg.seq },
    h("time", { class: "time", text: clock(seg.t_start) }), h("div", { class: "body" }, who, p));
  if (who && nameable) who.addEventListener("click", () => openNamer(m, row, seg));
  m.rows.set(seg.seq, p);
  return row;
}

function appendSegment(m, seg, silent) {
  const segs = m.detail.segments;
  if (segs.length && seg.seq <= segs[segs.length - 1].seq) return;  // already have it
  const prev = segs[segs.length - 1];
  segs.push(seg);
  if (!m.linesEl) return;
  m.linesEl.append(segRow(m, seg, prev));
  syncEmptyNote(m);
  if (silent) return;
  if (S.stick) scrollToEnd(); else { S.unseen += 1; renderJump(); }
}

function reviseSegment(m, seq, text) {
  const seg = m.detail.segments.find((x) => x.seq === seq);
  if (!seg || seg.text === text) return;
  seg.text = text;
  const p = m.rows && m.rows.get(seq);
  if (p) p.textContent = text;
}

async function refreshMeeting(session, openKind) {
  const m = S.feed.get(session);
  if (!m || !m.el) return;
  await fetchDetail(m);
  if (openKind && m.docs[openKind]) m.open[openKind] = true;
  m.el.replaceWith(buildMeeting(m));
  if (S.stick) scrollToEnd();
}

function updateLive() {
  const r = S.status.recorder || {};
  for (const m of S.feed.values()) {
    if (!m.durEl) continue;
    if (m.info.recording || (r.session === m.info.session && r.state !== "idle")) {
      m.durEl.replaceChildren(h("i", { class: "dot" }), r.session === m.info.session ? clockHms(r.elapsed_s) : "LIVE");
    } else {
      const d = durationOf(m.info);
      m.durEl.textContent = d == null ? "" : clock(d);
    }
  }
}

function updateJobLines() {
  const job = S.status.job;
  for (const m of S.feed.values()) {
    if (!m.jobEl) continue;
    const mine = job && job.state === "running" && job.session === m.info.session;
    m.jobEl.hidden = !mine;
    m.jobEl.textContent = mine ? jobLabel(job) + (job.stage && job.stage !== "waiting for Claude" ? " · " + job.stage.toUpperCase() : "") + (job.total ? ` ${job.done}/${job.total}` : "") : "";
  }
}

function updateButtons() {
  const busy = !!(S.status.job && S.status.job.state === "running");
  for (const m of S.feed.values()) {
    for (const b of Object.values(m.btns || {})) { b.disabled = busy; b.title = busy ? "Another job is running" : b.dataset.tip; }
  }
}

async function startJob(m, kind) {
  const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(m.info.session)}/jobs`, { kind }));
  if (res && res.job) { S.status.job = res.job; updateJobLines(); updateButtons(); renderStrip(); }
}

function renderDocs(m) {
  m.docsEl.replaceChildren();
  for (const kind of ["summary", "todo"]) {
    const md = m.docs[kind];
    if (!md) continue;
    const det = h("details", { class: "doc" }, h("summary", { text: kind === "todo" ? "To-do" : "Summary" }), h("div", { class: "md" }, renderMarkdown(md)));
    det.open = !!m.open[kind];
    det.addEventListener("toggle", () => { m.open[kind] = det.open; });
    m.docsEl.append(det);
  }
}

function renameMeeting(m, btn) {
  const input = h("input", { class: "text mtitle-in", type: "text", value: m.info.title || "", placeholder: "Meeting", maxlength: "80", "aria-label": "Meeting title" });
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const title = input.value.trim();
    if (save && title && title !== m.info.title) {
      const res = await guarded(() => api("POST", `/api/meetings/${encodeURIComponent(m.info.session)}/rename`, { title }));
      if (res) { m.info.title = title; btn.textContent = title; btn.classList.remove("untitled"); }
    }
    input.replaceWith(btn);
  };
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") finish(true); else if (ev.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
  btn.replaceWith(input);
  input.focus(); input.select();
}

async function moveMeeting(m) {
  const options = S.projects.map((p) => ({ value: p.id, label: p.name }));
  const current = S.sel;
  const v = await formModal({ title: "Move to project", text: "The meeting folder is moved to the project's Steno folder.", fields: [{ key: "project", label: "Project", options, value: current }], ok: "Move" });
  if (!v || v.project === current) return;
  const res = await guarded(() => api("POST", `/api/meetings/${encodeURIComponent(m.info.session)}/move`, { project: v.project }));
  if (res) { toast(`Moved to ${projectName(v.project)}`); await guarded(loadProjects); await guarded(loadFeed); }
}

// ---------------------------------------------------------------- naming speakers

async function openNamer(m, row, seg) {
  document.querySelectorAll(".namer").forEach((n) => n.remove());
  const n = clusterNo(seg.speaker_id);
  const input = h("input", { class: "text", type: "text", list: "people-names", maxlength: "80", placeholder: `Name for Speaker ${n}`, "aria-label": "Speaker name", autocomplete: "off" });
  const save = h("input", { type: "checkbox" });
  const form = h("form", { class: "namer", onsubmit: async (ev) => {
    ev.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(m.info.session)}/name`, { speaker: seg.speaker_id, name, save_profile: save.checked }));
    if (res) { toast(res.message || "Speaker named"); await guarded(() => refreshMeeting(m.info.session)); }
  } }, input,
  h("label", { title: "Store a voice profile on this computer so future meetings recognise this person" }, save, "Remember voice"),
  h("button", { class: "btn small solid", type: "submit", text: "Save" }),
  h("button", { class: "btn small", type: "button", text: "Cancel", onclick: () => form.remove() }));
  row.after(form);
  input.focus();
  input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") form.remove(); });
  try {
    const d = await api("GET", "/api/people");
    $("people-names").replaceChildren(...d.people.map((p) => h("option", { value: p.name })));
  } catch (e) { /* suggestions are optional */ }
}

// ---------------------------------------------------------------- scrolling

function scrollToEnd() {
  const f = $("feed");
  f.scrollTop = f.scrollHeight;
  S.unseen = 0;
  renderJump();
}
function renderJump() {
  const f = $("feed"), j = $("jump");
  const far = f.scrollHeight - f.scrollTop - f.clientHeight > 120;
  j.hidden = S.stick || !far;
  const recording = recProjectId() === S.sel && recProjectId() !== null;
  j.textContent = S.unseen ? `${S.unseen} new line${S.unseen === 1 ? "" : "s"} - jump to live` : recording ? "Jump to live" : "Jump to latest";
}
function onScroll() {
  const f = $("feed");
  S.stick = f.scrollHeight - f.scrollTop - f.clientHeight < 60;
  if (S.stick) S.unseen = 0;
  renderJump();
}

// ---------------------------------------------------------------- markdown (minimal; builds DOM, never HTML)

function inlineMd(text) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(h("strong", { text: t.slice(2, -2) }));
    else if (t.startsWith("`")) out.push(h("code", { text: t.slice(1, -1) }));
    else out.push(h("em", { text: t.slice(1, -1) }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderMarkdown(src) {
  const nodes = [];
  let list = null, kind = null;
  for (const raw of String(src).split("\n")) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (!line.trim()) { list = null; continue; }
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) { list = null; nodes.push(h("h" + m[1].length, null, inlineMd(m[2]))); continue; }
    if ((m = /^\s*(?:([-*])|(\d+)\.)\s+(?:\[( |x|X)\]\s+)?(.*)$/.exec(line))) {
      const want = m[1] ? "ul" : "ol";
      if (!list || kind !== want) { list = h(want); kind = want; nodes.push(list); }
      const task = m[3] !== undefined;
      list.append(h("li", { class: task ? "task" : null }, task ? (m[3] === " " ? "☐ " : "☑ ") : null, inlineMd(m[4])));
    } else if ((m = /^\s*>\s?(.*)$/.exec(line))) { list = null; nodes.push(h("blockquote", null, inlineMd(m[1]))); }
    else if (/^\s{2,}\S/.test(line) && list && list.lastChild) list.lastChild.append(" ", ...inlineMd(line.trim()));
    else { list = null; nodes.push(h("p", null, inlineMd(line))); }
  }
  return nodes;
}

// ---------------------------------------------------------------- people

async function loadPeople() {
  let data;
  try { data = await api("GET", "/api/people"); } catch (e) { if (!missing(e)) throw e; data = { people: [] }; }
  S.people = data.people;
  const box = $("people");
  box.replaceChildren();
  if (!S.people.length) box.append(h("p", { class: "hint", text: "No voice profiles yet. Name a speaker in a meeting and choose \"Remember voice\"." }));
  for (const p of S.people) box.append(personRow(p));
  renderSide();
}

function personRow(p) {
  const row = h("div", { class: "person" });
  const show = () => row.replaceChildren(
    h("div", { class: "pn" }, h("div", { text: p.name }), h("div", { class: "sub", text: `${p.embeddings} voice sample${p.embeddings === 1 ? "" : "s"} · updated ${when(p.updated)}` })),
    h("button", { class: "btn small", type: "button", text: "Rename", onclick: edit }),
    h("button", { class: "btn small danger", type: "button", text: "Delete", onclick: () => removePerson(p) }));
  const edit = () => {
    const input = h("input", { class: "text", type: "text", value: p.name, maxlength: "80", "aria-label": "New name" });
    row.replaceChildren(h("form", { onsubmit: async (ev) => {
      ev.preventDefault();
      const res = await guarded(() => api("POST", `/api/people/${p.id}/rename`, { name: input.value }));
      if (res) { toast(res.message); await guarded(loadPeople); }
    } }, input, h("button", { class: "btn small solid", type: "submit", text: "Save" }),
    h("button", { class: "btn small", type: "button", text: "Cancel", onclick: show })));
    input.focus(); input.select();
  };
  show();
  return row;
}

async function removePerson(p) {
  const ok = await formModal({ title: "Delete person", text: `Delete ${p.name} and their voice data? Old transcripts keep the name as plain text. This cannot be undone.`, ok: "Delete" });
  if (!ok) return;
  const res = await guarded(() => api("DELETE", `/api/people/${p.id}`));
  if (res) { toast(res.message); await guarded(loadPeople); }
}

// ---------------------------------------------------------------- live events (SSE over fetch)

function handleEvent(name, data) {
  if (name === "hello") { S.status = data; onStatus(); return; }
  if (name === "status") { Object.assign(S.status, data); onStatus(); return; }
  if (name === "segment") {
    const m = S.feed.get(data.session);
    if (m && m.detail) appendSegment(m, data.segment);
    else if (S.loadingFeed) S.buffer.push(data);
    else if (S.view === "project" && S.sel && !S.meetings.some((x) => x.session === data.session) && !S.unknown.has(data.session)) {
      S.unknown.add(data.session);  // a new meeting we have not listed yet (or one of another project)
      scheduleFeed();
    }
  } else if (name === "revision") {
    const m = S.feed.get(data.session);
    if (m && m.detail) reviseSegment(m, data.seq, data.text);
  } else if (name === "job") {
    onJob(data);
  } else if (name === "names") {
    if (S.view === "people") guarded(loadPeople);
    if (data.session) guarded(() => refreshMeeting(data.session));
    else for (const id of S.feed.keys()) guarded(() => refreshMeeting(id));
  } else if (name === "sessions" || name === "projects") {
    scheduleProjects(); scheduleFeed();
  }
}

let lastState = null, lastSession = null, lastError = null;
function onStatus() {
  const r = S.status.recorder || {};
  if (r.state === "error" && r.error && r.error !== lastError) toast(r.error, "error");
  lastError = r.state === "error" ? r.error : null;
  if (r.session && r.session !== lastSession) { scheduleFeed(); scheduleProjects(); }
  if (r.state === "recording" && lastState !== "recording") S.stick = true;
  if (r.state === "idle" && lastState && lastState !== "idle") { scheduleFeed(); scheduleProjects(); }
  lastState = r.state;
  if (r.session) lastSession = r.session;
  renderTop(); renderSideFoot(); updateLive(); updateJobLines();
}

function onJob(job) {
  const prev = S.jobStates.get(job.id);
  S.jobStates.set(job.id, job.state);
  if (job.state === "running") S.status.job = job;
  else if (S.status.job && S.status.job.id === job.id) S.status.job = null;
  updateJobLines(); updateButtons(); renderStrip();
  if (job.state !== "running" && prev !== job.state) {
    if (job.state === "error") toast(`${job.kind} failed: ${job.error}`, "error");
    else {
      toast(`${job.kind} done${job.message ? ": " + job.message : ""}`);
      guarded(() => refreshMeeting(job.session, job.kind === "summary" || job.kind === "todo" ? job.kind : null));
    }
  }
}

function dispatch(raw) {
  let name = "message", data = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return;
  try { handleEvent(name, JSON.parse(data)); } catch (e) { console.error("event", name, e); }
}

async function connect() {
  while (!S.fatal) {
    try {
      const res = await fetch("/api/events", { headers: { "X-Steno-Token": S.token || "" } });
      if (res.status === 401) { S.fatal = true; toast("Access token rejected. Open Steno again.", "error"); break; }
      if (!res.ok || !res.body) throw new Error("events " + res.status);
      S.connected = true; renderSideFoot();
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) { dispatch(buf.slice(0, i)); buf = buf.slice(i + 2); }
      }
    } catch (e) { /* fall through to reconnect */ }
    S.connected = false; renderSideFoot();
    await new Promise((r) => setTimeout(r, 1500));
  }
}

// ---------------------------------------------------------------- init

function markBrowserMode() { document.body.classList.toggle("browser", !window.pywebview); }

function init() {
  initToken();
  window.addEventListener("load", markBrowserMode);
  window.addEventListener("pywebviewready", markBrowserMode);
  $("btn-new").addEventListener("click", () => guarded(newProject));
  $("empty-new").addEventListener("click", () => guarded(newProject));
  $("btn-add").addEventListener("click", () => guarded(addProject));
  $("empty-add").addEventListener("click", () => guarded(addProject));
  $("btn-forget").addEventListener("click", () => guarded(forgetProject));
  $("nav-people").addEventListener("click", showPeople);
  $("btn-rec").addEventListener("click", () => startRecording());
  $("btn-pause").addEventListener("click", () => recordCmd((S.status.recorder || {}).paused ? "resume" : "pause"));
  $("btn-stop").addEventListener("click", () => recordCmd("stop"));
  $("rec-title").addEventListener("keydown", (ev) => { if (ev.key === "Enter") startRecording(); });
  for (const [id, key] of [["tgl-mic", "mic"], ["tgl-loop", "loop"], ["tgl-keep", "keep"]]) {
    $(id).addEventListener("click", () => { S.opts[key] = !S.opts[key]; remember("steno-opts", JSON.stringify(S.opts)); renderTop(); });
  }
  $("feed").addEventListener("scroll", onScroll, { passive: true });
  $("jump").addEventListener("click", () => { S.stick = true; scrollToEnd(); });

  if (!S.token) { S.fatal = true; toast("No access token. Open Steno from its own window or printed address.", "error"); return; }
  guarded(async () => {
    S.status = await api("GET", "/api/status");
    lastState = (S.status.recorder || {}).state;
    lastSession = (S.status.recorder || {}).session;
    const cfg = S.status.config || {};
    S.opts = { mic: cfg.capture_mic !== false, loop: cfg.capture_loopback !== false, keep: false };
    try { Object.assign(S.opts, JSON.parse(remember("steno-opts") || "{}")); } catch (e) { /* ignore bad prefs */ }
    S.opts.keep = false;  // never remembered: raw audio is sensitive
    await loadProjects();
    showView(); renderSide(); renderTop();
  });
  connect();
}

init();
