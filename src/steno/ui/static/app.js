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

function pad2(n) { return String(n).padStart(2, "0"); }
function clock(sec) {
  const t = Math.max(0, Math.floor(sec));
  const hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = t % 60;
  return hh ? `${hh}:${pad2(mm)}:${pad2(ss)}` : `${pad2(mm)}:${pad2(ss)}`;
}
function clockHms(sec) {
  const t = Math.max(0, Math.floor(sec));
  return `${pad2(Math.floor(t / 3600))}:${pad2(Math.floor((t % 3600) / 60))}:${pad2(t % 60)}`;
}
function when(iso) { return iso ? iso.replace("T", " ").slice(0, 16) : ""; }
function clusterNo(id) { const m = /^[SR](\d+)$/.exec(id || ""); return m ? Number(m[1]) : null; }

function store(key, value) {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value);
  } catch (e) { /* storage unavailable: keep working without it */ }
  return null;
}
function pref(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) { /* ignore */ }
  return null;
}

// ---------------------------------------------------------------- state and API

const S = {
  token: null,
  status: { recorder: {}, job: null, config: {} },
  jobs: [],
  view: "live",
  open: null,          // detail of the transcript on screen
  openId: null,
  loading: null,       // session id being fetched (segments are buffered meanwhile)
  buffer: [],
  browseId: null,      // session opened from the Sessions list
  stick: true,         // transcript follows new lines until the user scrolls up
  unseen: 0,
  naming: null,        // speaker being named
  tab: "summary",
  sessions: [],
  people: [],
  connected: false,
  fatal: false,
};

function initToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    store("steno-token", fromUrl);
    S.token = fromUrl;
    history.replaceState(null, "", url.pathname);  // keep the token out of the address bar
  } else {
    S.token = store("steno-token");
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
  catch (e) { throw new Error("Cannot reach Steno. Is `steno ui` still running?"); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (res.status === 401) {
    S.fatal = true;
    throw new Error("Access token rejected. Open the address printed by `steno ui` again.");
  }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

function showError(err) {
  $("alert-text").textContent = err instanceof Error ? err.message : String(err);
  $("alert").hidden = false;
}
function clearError() { $("alert").hidden = true; }
async function guarded(fn) {
  try { return await fn(); } catch (e) { showError(e); return null; }
}

// ---------------------------------------------------------------- views

function setView(view) {
  S.view = view;
  document.querySelectorAll(".nav-item").forEach((b) => {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  guarded(enterView);
}

async function enterView() {
  clearError();
  const live = S.view === "live";
  const sessions = S.view === "sessions";
  $("recorder").hidden = !live;
  $("people-view").hidden = S.view !== "people";
  $("sessions-view").hidden = !(sessions && !S.browseId);
  $("transcript-wrap").hidden = !(live || (sessions && S.browseId));
  $("sess-back").hidden = !sessions;
  $("sess-kind").textContent = live ? "Live transcript" : "Session";
  if (live) {
    renderRecorder();
    const id = S.status.recorder.session;
    if (id) await openSession(id, { follow: true });
    else { clearTranscript("Start a recording and the transcript appears here as you speak."); }
  } else if (sessions) {
    if (S.browseId) await openSession(S.browseId, { follow: false });
    else { clearTranscript(); renderSide(); await loadSessions(); }
  } else {
    clearTranscript();
    renderSide();
    await loadPeople();
  }
  renderStatusbar();
}

function clearTranscript(emptyText) {
  S.open = null; S.openId = null; S.unseen = 0;
  $("transcript").replaceChildren();
  const e = $("transcript-empty");
  e.textContent = emptyText || "";
  e.hidden = !emptyText;
  $("jump").hidden = true;
  $("sess-id").textContent = "";
  $("fin-run").hidden = true;
  $("fin-speakers").parentElement.hidden = true;
  $("view-toggle").hidden = true;
  renderSide();
}

// ---------------------------------------------------------------- recorder

function renderRecorder() {
  const r = S.status.recorder || {};
  const cfg = S.status.config || {};
  const box = $("recorder");
  const state = r.state || "idle";
  box.classList.toggle("is-recording", state === "recording");
  box.classList.toggle("is-busy", state === "starting" || state === "stopping");
  box.classList.toggle("is-error", state === "error");
  const labels = { idle: "Idle", starting: "Loading models", recording: "Recording", stopping: "Finishing", error: "Error" };
  $("rec-label").textContent = labels[state] || state;
  $("rec-clock").textContent = clockHms(r.elapsed_s || 0);
  const btn = $("rec-toggle");
  const active = state === "starting" || state === "recording" || state === "stopping";
  btn.textContent = state === "recording" ? "Stop recording"
    : state === "starting" ? "Loading models..." : state === "stopping" ? "Finishing..." : "Start recording";
  btn.disabled = state === "starting" || state === "stopping";
  btn.classList.toggle("primary", state !== "recording");
  $("rec-opts").querySelectorAll("input").forEach((i) => { i.disabled = active; });
  if (!active && !box.dataset.init && cfg.capture_mic !== undefined) {
    box.dataset.init = "1";
    $("opt-mic").checked = !!cfg.capture_mic;
    $("opt-loop").checked = !!cfg.capture_loopback;
    $("opt-keep").checked = pref("steno-keep-audio") === "1";
  }
  if (active) {  // show what the running recording uses
    $("opt-mic").checked = (r.sources || []).includes("mic");
    $("opt-loop").checked = (r.sources || []).includes("loopback");
    $("opt-keep").checked = !!r.keep_audio;
  }
  const meters = $("rec-meters");
  meters.hidden = !(state === "recording" || state === "stopping");
  for (const name of ["mic", "loopback"]) {
    const m = $("meter-" + name);
    const lv = (r.levels || {})[name];
    m.hidden = !lv;
    if (lv) {
      m.querySelector(".bar i").style.setProperty("--w", String(lv.level));
      m.classList.toggle("active", !!lv.active);
    }
  }
  let now = r.speaker || "-";
  if (r.idle_s != null && r.speaker) now += `  (${Math.round(r.idle_s)} s ago)`;
  if (state === "stopping") now = `transcribing ${r.backlog || 0} pending`;
  else if (state === "recording" && r.backlog > 2) now += `  [${r.backlog} waiting]`;
  $("rec-speaker").textContent = now;
}

async function toggleRecording() {
  const r = S.status.recorder || {};
  if (r.state === "recording") {
    await guarded(() => api("POST", "/api/record/stop", {}));
    return;
  }
  pref("steno-keep-audio", $("opt-keep").checked ? "1" : "0");
  clearError();
  S.browseId = null;
  const res = await guarded(() => api("POST", "/api/record/start", {
    mic: $("opt-mic").checked, loopback: $("opt-loop").checked, keep_audio: $("opt-keep").checked,
  }));
  if (res) { S.status.recorder = res.recorder; clearTranscript("Loading models, this can take a moment on the first run..."); renderRecorder(); }
}

// ---------------------------------------------------------------- transcript

function speakerButton(seg) {
  const n = clusterNo(seg.speaker_id);
  const canName = n !== null && S.open && S.open.view === "live";
  const unknown = n !== null && seg.speaker === `Speaker ${n}`;
  const b = h("button", {
    class: "who" + (unknown ? " unknown" : "") + (canName ? "" : " static"), type: "button",
    title: canName ? "Name this speaker" : "",
  }, seg.speaker + (unknown ? " ?" : ""));
  if (canName) b.addEventListener("click", () => startNaming(seg.speaker_id, seg.speaker));
  return b;
}

function segRow(seg, prev) {
  const newSpk = !prev || prev.speaker !== seg.speaker || prev.speaker_id !== seg.speaker_id;
  const row = h("div", { class: "seg" + (seg.provisional ? " prov" : "") + (newSpk ? " new-spk" : ""), "data-seq": seg.seq },
    h("time", { text: clock(seg.t_start) }),
    h("div", { class: "body" }, newSpk ? [speakerButton(seg), h("br")] : null, h("p", { text: seg.text })));
  return row;
}

function renderTranscript() {
  const box = $("transcript");
  const keepTop = $("tscroll").scrollTop;
  box.replaceChildren();
  const segs = S.open ? S.open.segments : [];
  const frag = document.createDocumentFragment();
  segs.forEach((s, i) => frag.append(segRow(s, segs[i - 1])));
  box.append(frag);
  const empty = $("transcript-empty");
  const live = S.open && S.open.recording;
  empty.hidden = segs.length > 0;
  if (!segs.length) empty.textContent = live ? "Listening... lines appear here a few seconds after you speak." : "This session has no transcript lines.";
  if (S.stick) scrollToEnd(); else $("tscroll").scrollTop = keepTop;
}

function appendSegment(seg) {
  if (!S.open) return;
  const segs = S.open.segments;
  if (segs.length && seg.seq <= segs[segs.length - 1].seq) return;  // already have it
  const prev = segs[segs.length - 1];
  segs.push(seg);
  $("transcript-empty").hidden = true;
  $("transcript").append(segRow(seg, prev));
  bumpSpeaker(seg);
  if (S.stick) scrollToEnd();
  else { S.unseen += 1; renderJump(); }
}

function reviseSegment(seq, text) {
  if (!S.open || S.open.view !== "live") return;
  const seg = S.open.segments.find((x) => x.seq === seq);
  if (!seg || seg.text === text) return;
  seg.text = text;
  const p = document.querySelector(`#transcript .seg[data-seq="${seq}"] p`);
  if (p) p.textContent = text;
}

function bumpSpeaker(seg) {
  const list = S.open.speakers;
  let e = list.find((x) => x.id === seg.speaker_id && x.name === seg.speaker);
  if (!e) {
    const n = clusterNo(seg.speaker_id);
    e = { id: seg.speaker_id, name: seg.speaker, segments: 0, seconds: 0, provisional: false,
          can_name: n !== null && S.open.view === "live",
          named: !(n !== null && seg.speaker === `Speaker ${n}`) };
    list.push(e);
  }
  e.segments += 1;
  e.seconds += Math.max(0, seg.t_end - seg.t_start);
  e.provisional = seg.provisional;
  scheduleSide();
}

let sideTimer = null;
function scheduleSide() {
  if (sideTimer) return;
  sideTimer = setTimeout(() => { sideTimer = null; renderSpeakers(); }, 400);
}

function scrollToEnd() {
  const sc = $("tscroll");
  sc.scrollTop = sc.scrollHeight;
  S.unseen = 0;
  renderJump();
}
function renderJump() {
  const j = $("jump");
  j.hidden = S.stick || S.unseen === 0;
  j.textContent = `${S.unseen} new line${S.unseen === 1 ? "" : "s"} below`;
}
function onScroll() {
  const sc = $("tscroll");
  S.stick = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 48;  // back at the end: follow again
  if (S.stick) { S.unseen = 0; }
  renderJump();
}

// ---------------------------------------------------------------- sessions

async function openSession(id, opts) {
  const follow = !!(opts && opts.follow);
  const view = (opts && opts.view) || undefined;
  S.loading = id; S.buffer = [];
  let detail;
  try {
    detail = await api("GET", `/api/sessions/${encodeURIComponent(id)}` + (view ? `?view=${view}` : ""));
  } catch (e) {
    S.loading = null; S.buffer = [];
    throw e;
  }
  S.loading = null;
  const sameSession = S.openId === id;
  S.open = detail; S.openId = id;
  S.stick = follow ? true : (sameSession ? S.stick : false);
  if (!sameSession) $("tscroll").scrollTop = 0;
  const buffered = S.buffer; S.buffer = [];
  renderHead();
  renderTranscript();
  for (const seg of buffered) appendSegment(seg);
  renderSide();
  renderStatusbar();
}

function renderHead() {
  const d = S.open;
  if (!d) return;
  $("sess-id").textContent = d.id;
  $("sess-kind").textContent = S.view === "live" ? (d.recording ? "Live transcript" : "Last recording") : "Session";
  const tgl = $("view-toggle");
  tgl.hidden = !d.has_final;
  tgl.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === d.view)));
  $("fin-run").hidden = false;
  $("fin-speakers").parentElement.hidden = false;
  updateButtons();
}

async function loadSessions() {
  const data = await api("GET", "/api/sessions");
  S.sessions = data.sessions;
  const rows = $("sessions-rows");
  rows.replaceChildren();
  $("sessions-count").textContent = `${S.sessions.length} total`;
  if (!S.sessions.length) rows.append(h("p", { class: "empty", text: "No sessions yet. Record something on the Live tab." }));
  for (const s of S.sessions) {
    const badges = h("span", { class: "badges" },
      s.recording ? h("span", { class: "badge live", text: "recording" }) : null,
      h("span", { class: "badge" + (s.has_final ? " on" : ""), text: "final" }),
      h("span", { class: "badge" + (s.has_summary ? " on" : ""), text: "summary" }),
      h("span", { class: "badge" + (s.has_todo ? " on" : ""), text: "to-do" }),
      h("span", { class: "badge" + (s.has_audio ? " on" : ""), text: "audio" }));
    const btn = h("button", { class: "row-item", type: "button" },
      h("span", { class: "main-col" }, h("span", { class: "mono", text: s.id })),
      h("span", { class: "sub", text: `${when(s.started_at)} - ${s.segments} line${s.segments === 1 ? "" : "s"} - ${(s.sources || []).join(" + ")}` }),
      badges);
    btn.addEventListener("click", () => { S.browseId = s.id; guarded(enterView); });
    rows.append(btn);
  }
}

// ---------------------------------------------------------------- side panel

function renderSide() {
  const has = !!S.open;
  $("panel-speakers").hidden = !has;
  $("panel-llm").hidden = !has;
  $("panel-info").hidden = has;
  renderSpeakers();
  renderLlm();
  renderJob();
  renderResult();
}

function renderSpeakers() {
  const d = S.open;
  const list = $("spk-list");
  list.replaceChildren();
  if (!d) return;
  for (const sp of d.speakers) {
    const unknown = sp.can_name && !sp.named;
    const meta = `${clock(sp.seconds)} - ${sp.segments} line${sp.segments === 1 ? "" : "s"}${sp.provisional ? " - provisional" : ""}`;
    const row = h("li", { class: "spk-row" },
      h("span", { class: "spk-name" + (unknown ? " unknown" : ""), text: sp.name }),
      sp.can_name ? h("button", { class: "btn mini", type: "button", text: unknown ? "Name" : "Rename",
        onclick: () => startNaming(sp.id, sp.name) }) : null,
      h("span", { class: "spk-meta", text: meta }));
    list.append(row);
  }
  $("spk-note").textContent = d.view === "final"
    ? "Final labels cannot be named here. Switch to the Live transcript, name the speakers, then run Finalize again."
    : (d.speakers.some((s) => s.can_name && !s.named) ? "Click an unnamed speaker, or a name in the transcript, to name it." : "");
  const names = d.speakers.filter((s) => s.id && s.id !== "U" && (s.named || !s.can_name)).map((s) => s.name);
  $("todo-people").replaceChildren(...names.map((n) => h("option", { value: n })));
}

function startNaming(id, current) {
  S.naming = id;
  const n = clusterNo(id);
  $("name-title").textContent = `Name ${n !== null ? "Speaker " + n : "speaker"}`;
  $("name-input").value = /^Speaker \d+$/.test(current) ? "" : current;
  $("name-save").checked = false;
  $("name-form").hidden = false;
  $("name-input").focus();
  $("name-form").scrollIntoView({ block: "nearest" });
  guarded(async () => {  // suggestions: people who are already enrolled
    const data = await api("GET", "/api/people");
    $("people-names").replaceChildren(...data.people.map((p) => h("option", { value: p.name })));
  });
}

async function submitName(ev) {
  ev.preventDefault();
  if (!S.naming || !S.open) return;
  const id = S.open.id;
  const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(id)}/name`, {
    speaker: S.naming, name: $("name-input").value, save_profile: $("name-save").checked,
  }));
  if (res) {
    $("name-form").hidden = true; S.naming = null;
    toast(res.message);
    await guarded(() => openSession(id, { view: S.open && S.open.view }));
  }
}

function llmNotice() {
  const cfg = S.status.config || {};
  const src = S.open ? S.open.llm_source : "live";
  const clean = S.open ? S.open.clean_source : "live";
  return `Sent to Claude (${cfg.llm_model || "claude"}, via your own Claude Code login): only transcript text, never audio, never voice data. Clean up sends the ${clean} text; Summary and To-do send the ${src} text.`;
}
function cleanInfo() {
  const c = S.open && S.open.clean;
  if (!c) return "";
  const stale = c.fresh ? "" : " The text has changed since; run Clean up again to use it.";
  return `Last clean-up (${c.source} text): ${c.applied} of ${c.proposed} edit(s) applied to ${c.changed_lines} line(s), ${c.rejected} rejected.${stale}`;
}
function renderLlm() { $("llm-sent").textContent = llmNotice(); $("clean-info").textContent = cleanInfo(); updateButtons(); }

function updateButtons() {
  const busy = !!S.status.job;
  const d = S.open;
  const hint = busy ? "Another job is running" : "";
  for (const id of ["btn-clean", "btn-summary", "btn-todo", "btn-todo-person"]) {
    const b = $(id); b.disabled = busy || !d; b.title = hint;
  }
  const fin = $("fin-run");
  let why = hint;
  if (!why && d && d.recording) why = "Stop the recording first";
  if (!why && d && !d.has_audio) why = "No kept audio: record with Keep audio";
  fin.disabled = !d || !!why;
  fin.title = why;
}

function renderJob() {
  const card = $("job-card");
  const job = S.status.job || S.jobs.find((j) => j.session === S.openId) || null;
  $("panel-job").hidden = !job || !S.open;
  card.replaceChildren();
  if (!job) return;
  const pct = job.total ? job.done / job.total : 0;
  const state = job.state === "running" ? "running" : job.state === "done" ? "done" : "failed";
  card.append(h("div", { class: "job" + (job.state === "error" ? " error" : "") },
    h("div", { class: "head" }, h("span", { class: "eyebrow", text: job.kind }), h("span", { class: "eyebrow", text: state })),
    h("div", { class: "mono dim", text: job.session }),
    job.state === "running" ? [
      h("div", { class: "bar" }, (() => { const i = h("i"); i.style.setProperty("--w", String(pct)); return i; })()),
      h("div", { text: job.stage + (job.total ? ` ${job.done}/${job.total}` : "") })] : null,
    job.state === "done" ? h("div", { text: job.message }) : null,
    job.state === "error" ? h("div", { class: "err", role: "alert", text: job.error }) : null));
}

function renderResult() {
  const d = S.open;
  const panel = $("panel-result");
  const has = d && (d.summary || d.todo);
  panel.hidden = !has;
  if (!has) return;
  if (S.tab === "summary" && !d.summary) S.tab = "todo";
  if (S.tab === "todo" && !d.todo) S.tab = "summary";
  panel.querySelectorAll("[role=tab]").forEach((t) => {
    t.setAttribute("aria-selected", String(t.dataset.tab === S.tab));
    t.hidden = !d[t.dataset.tab];
  });
  $("result-md").replaceChildren(...renderMarkdown(d[S.tab] || ""));
}

// Minimal Markdown (headings, lists, quotes, bold/italic/code). Builds DOM nodes, never HTML.
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
  let list = null;
  for (const raw of src.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (!line.trim()) { list = null; continue; }
    if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) { list = null; nodes.push(h("h" + m[1].length, null, inlineMd(m[2]))); }
    else if ((m = /^\s*[-*]\s+(?:\[( |x)\]\s+)?(.*)$/.exec(line))) {
      if (!list) { list = h("ul"); nodes.push(list); }
      const box = m[1] === undefined ? "" : (m[1] === "x" ? "☑ " : "☐ ");
      list.append(h("li", null, box, inlineMd(m[2])));
    } else if ((m = /^\s*>\s?(.*)$/.exec(line))) { list = null; nodes.push(h("blockquote", null, inlineMd(m[1]))); }
    else if (/^\s{2,}\S/.test(line) && list && list.lastChild) { list.lastChild.append(" ", ...inlineMd(line.trim())); }
    else { list = null; nodes.push(h("p", null, inlineMd(line))); }
  }
  return nodes;
}

async function startJob(kind, extra) {
  if (!S.open) return;
  const id = S.open.id;
  clearError();
  const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(id)}/jobs`, { kind, ...extra }));
  if (res) { S.status.job = res.job; S.jobs = [res.job, ...S.jobs.filter((j) => j.id !== res.job.id)]; renderJob(); updateButtons(); renderStatusbar(); }
}

function runFinalize() {
  const v = $("fin-speakers").value.trim();
  const extra = {};
  if (v) {
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1 || n > 20) { showError("Speakers must be a whole number from 1 to 20 (or empty for automatic)."); return; }
    extra.speakers = n;
  }
  startJob("finalize", extra);
}

// ---------------------------------------------------------------- people

async function loadPeople() {
  const data = await api("GET", "/api/people");
  S.people = data.people;
  const rows = $("people-rows");
  rows.replaceChildren();
  $("people-count").textContent = `${S.people.length} enrolled`;
  $("people-empty").hidden = S.people.length > 0;
  for (const p of S.people) rows.append(personRow(p));
}

function personRow(p) {
  const row = h("div", { class: "row-item person" });
  const show = () => {
    row.replaceChildren(
      h("span", { class: "main-col" }, h("span", { text: p.name })),
      h("span", { class: "sub", text: `${p.embeddings} voice samples - updated ${when(p.updated)}` }),
      h("span", { class: "actions" },
        h("button", { class: "btn mini", type: "button", text: "Rename", onclick: edit }),
        h("button", { class: "btn mini danger", type: "button", text: "Delete", onclick: () => remove(p) })));
  };
  const edit = () => {
    const input = h("input", { class: "text", type: "text", value: p.name, maxlength: "80", "aria-label": "New name" });
    const form = h("form", { onsubmit: async (ev) => {
      ev.preventDefault();
      const res = await guarded(() => api("POST", `/api/people/${p.id}/rename`, { name: input.value }));
      if (res) { toast(res.message); await guarded(loadPeople); }
    } }, input, h("button", { class: "btn mini primary", type: "submit", text: "Save" }),
    h("button", { class: "btn mini", type: "button", text: "Cancel", onclick: show }));
    row.replaceChildren(h("span", { class: "main-col" }, form), h("span"), h("span"));
    input.focus(); input.select();
  };
  show();
  return row;
}

function confirmDialog(title, text, okLabel) {
  return new Promise((resolve) => {
    const dlg = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = okLabel;
    dlg.returnValue = "";  // Escape leaves the previous value in place otherwise
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.showModal();
  });
}

async function remove(p) {
  const ok = await confirmDialog("Delete person",
    `Delete ${p.name} and their voice data? Old transcripts keep the name as plain text. This cannot be undone.`, "Delete");
  if (!ok) return;
  const res = await guarded(() => api("DELETE", `/api/people/${p.id}`));
  if (res) { toast(res.message); await guarded(loadPeople); }
}

// ---------------------------------------------------------------- status bar, toast

function renderStatusbar() {
  const r = S.status.recorder || {};
  const rec = $("sb-rec");
  const labels = { recording: `REC ${clockHms(r.elapsed_s || 0)}`, starting: "STARTING", stopping: "FINISHING", error: "ERROR", idle: "IDLE" };
  rec.textContent = labels[r.state] || "IDLE";
  rec.classList.toggle("hot", r.state === "recording" || r.state === "stopping" || r.state === "starting");
  const sid = (r.state === "recording" || r.state === "stopping") ? r.session : (S.openId || r.session);
  $("sb-saved").textContent = sid ? `Saved in session ${sid}${r.keep_audio && r.session === sid ? " (audio kept)" : ""}` : "No session";
  const job = S.status.job;
  const jb = $("sb-job");
  jb.textContent = job ? `JOB ${job.kind} - ${job.session} - ${job.stage}${job.total ? " " + job.done + "/" + job.total : ""}` : "No job running";
  jb.classList.toggle("hot", !!job);
  const c = $("sb-conn");
  c.textContent = S.connected ? "Connected" : "Reconnecting";
  c.classList.toggle("hot", !S.connected);
}

let toastTimer = null;
function toast(text) {
  const el = $("sb-job");
  el.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(renderStatusbar, 6000);
}

// ---------------------------------------------------------------- live events (SSE over fetch)

function handleEvent(name, data) {
  if (name === "hello") { S.status = data; onStatus(true); return; }
  if (name === "status") {
    Object.assign(S.status, data);
    onStatus(false);
  } else if (name === "segment") {
    if (data.session === S.openId && !S.loading) appendSegment(data.segment);
    else if (data.session === S.loading) S.buffer.push(data.segment);
  } else if (name === "revision") {
    if (data.session === S.openId && !S.loading) reviseSegment(data.seq, data.text);
  } else if (name === "job") {
    onJob(data);
  } else if (name === "names") {
    if (S.view === "people") guarded(loadPeople);
    if (S.openId && (data.session === S.openId || data.session === null) && !S.loading) {
      guarded(() => openSession(S.openId, { view: S.open && S.open.view }));
    }
  } else if (name === "sessions") {
    if (S.view === "sessions" && !S.browseId) guarded(loadSessions);
  }
}

let lastRecState = null, lastRecSession = null;
function onStatus(fresh) {
  const r = S.status.recorder || {};
  if (r.state === "error" && lastRecState !== "error" && r.error) showError(r.error);
  if (S.view === "live") {
    renderRecorder();
    const changed = r.session && r.session !== lastRecSession;
    if (r.session && (changed || fresh) && r.session !== S.openId && !S.loading) {
      guarded(() => openSession(r.session, { follow: true }));
    }
    if (r.state === "recording" && lastRecState !== "recording") S.stick = true;
    if (r.state === "idle" && lastRecState === "stopping" && S.openId === r.session) {
      guarded(() => openSession(r.session, { view: S.open && S.open.view }));  // final bookkeeping: ended_at etc.
    }
  }
  lastRecState = r.state;
  lastRecSession = r.session || lastRecSession;
  updateButtons();
  renderStatusbar();
}

function onJob(job) {
  const prev = S.jobs.find((j) => j.id === job.id);
  S.jobs = [job, ...S.jobs.filter((j) => j.id !== job.id)].slice(0, 20);
  S.status.job = job.state === "running" ? job : (S.status.job && S.status.job.id !== job.id ? S.status.job : null);
  renderJob(); updateButtons(); renderStatusbar();
  if (job.state !== "running" && (!prev || prev.state === "running")) {
    if (job.state === "error") showError(`${job.kind} failed: ${job.error}`);
    else toast(`${job.kind} done: ${job.message}`);
    if (S.openId === job.session && !S.loading) {
      const view = job.kind === "finalize" ? "final" : (S.open && S.open.view);
      guarded(() => openSession(job.session, { view }));
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
      if (res.status === 401) { S.fatal = true; showError("Access token rejected. Open the address printed by `steno ui` again."); break; }
      if (!res.ok || !res.body) throw new Error("events " + res.status);
      S.connected = true; renderStatusbar();
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
    S.connected = false; renderStatusbar();
    await new Promise((r) => setTimeout(r, 1500));
  }
}

// ---------------------------------------------------------------- init

function init() {
  initToken();
  document.querySelectorAll(".nav-item").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.view === "sessions") S.browseId = null;
    setView(b.dataset.view);
  }));
  $("alert-close").addEventListener("click", clearError);
  $("rec-toggle").addEventListener("click", toggleRecording);
  $("tscroll").addEventListener("scroll", onScroll, { passive: true });
  $("jump").addEventListener("click", () => { S.stick = true; scrollToEnd(); });
  $("sess-back").addEventListener("click", () => { S.browseId = null; guarded(enterView); });
  $("view-toggle").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-v]");
    if (b && S.open && b.dataset.v !== S.open.view) guarded(() => openSession(S.open.id, { view: b.dataset.v }));
  });
  $("fin-run").addEventListener("click", runFinalize);
  $("btn-clean").addEventListener("click", () => startJob("clean"));
  $("btn-summary").addEventListener("click", () => startJob("summary"));
  $("btn-todo").addEventListener("click", () => startJob("todo"));
  $("btn-todo-person").addEventListener("click", () => {
    const p = $("todo-person").value.trim();
    if (!p) { showError("Enter a person's name first."); return; }
    startJob("todo", { person: p });
  });
  $("name-form").addEventListener("submit", submitName);
  $("name-cancel").addEventListener("click", () => { $("name-form").hidden = true; S.naming = null; });
  $("panel-result").querySelectorAll("[role=tab]").forEach((t) => t.addEventListener("click", () => { S.tab = t.dataset.tab; renderResult(); }));

  if (!S.token) {
    S.fatal = true;
    showError("No access token. Open the address printed by `steno ui`.");
    return;
  }
  guarded(async () => {
    S.status = await api("GET", "/api/status");
    S.jobs = S.status.jobs || [];
    lastRecState = S.status.recorder.state;
    lastRecSession = S.status.recorder.session;
    setView("live");
  });
  connect();
}

init();
