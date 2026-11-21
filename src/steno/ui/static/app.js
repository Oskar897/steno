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

// Line icons (24x24, stroked), drawn as DOM nodes: no HTML strings, no external files.
const ICONS = {
  menu: "M4 6h16M4 12h16M4 18h16",
  plus: "M12 5v14M5 12h14",
  x: "M6 6l12 12M18 6L6 18",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  system: "M3 5h18v11H3zM8 20h8M12 16v4",
  keep: "M4 8h16v12H4zM3 4h18v4H3zM10 12h4",
  pause: "M9 5v14M15 5v14",
  play: "M8 5.5v13l10-6.5z",
  stop: "M6 6h12v12H6z",
  people: "M15 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 19v-1a4 4 0 0 0-3-3.8M15.5 3.2a3.5 3.5 0 0 1 0 6.6",
  settings: "M4 6h9M17 6h3M15 4v4M4 12h3M11 12h9M9 10v4M4 18h11M19 18h1M17 16v4",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  folderPlus: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13h6",
  folderOpen: "M4 19a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5l2 2h7a1 1 0 0 1 1 1v2M4 19h14.2a1 1 0 0 0 1-.8l1.6-6.2a1 1 0 0 0-1-1.2H7.8a1 1 0 0 0-1 .8L4 19z",
  inbox: "M3 13h5l1.5 3h5l1.5-3h5M5.5 5h13L21 13v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5z",
  chevron: "M6 9l6 6 6-6",
  tag: "M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9zM7.5 7.5h.01",
  heading: "M5 6h14M12 6v13M9 19h6",
  login: "M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5M10 8l-4 4 4 4M6 12h10",
  refresh: "M20 11a8 8 0 0 0-14.5-4M4 4v3.5h3.5M4 13a8 8 0 0 0 14.5 4M20 20v-3.5h-3.5",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  spark: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  todo: "M10 6h10M10 12h10M10 18h10M4 6l1.2 1.2L7.5 5M4 12l1.2 1.2L7.5 11M4 18l1.2 1.2L7.5 17",
  wand: "M4 20L15 9M14 4v2M18 8h2M17.5 4.5l-1.4 1.4M13 9l2 2",
  move: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 13h6M13 10.5l2.5 2.5-2.5 2.5",
  pencil: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  down: "M12 5v14M6 13l6 6 6-6",
  check: "M5 12.5l4.5 4.5L19 7",
  alert: "M12 8v5M12 16.5v.01M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  forget: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9.5 11.5l5 5M14.5 11.5l-5 5",
  copy: "M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1",
  archive: "M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM3 4h18v4H3zM10 12h4",
  unarchive: "M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM3 4h18v4H3zM12 17v-5M9.5 14.5L12 12l2.5 2.5",
  sidebar: "M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM9 4v16",
  winMin: "M6.5 12h11",
  winMax: "M7 7h10v10H7z",
  winRestore: "M8.5 9.5h8v8h-8zM10.5 9.5V7.5h6a1 1 0 0 1 1 1v6h-1",
  winClose: "M7.5 7.5l9 9M16.5 7.5l-9 9",
};
function icon(name, cls) {
  const NS = document.querySelector("svg.logo").namespaceURI;  // the SVG namespace, without a URL literal in the source
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "ic" + (cls ? " " + cls : ""));
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", ICONS[name] || "");
  if (name === "more") path.setAttribute("stroke-width", "3");
  svg.append(path);
  return svg;
}
function fillIcons(root) {  // <x data-icon="name"> placeholders in the static page
  for (const el of (root || document).querySelectorAll("[data-icon]")) {
    if (!el.querySelector("svg")) el.prepend(icon(el.dataset.icon));
  }
}

// Speaker colours: 8 clearly different hues (CSS tokens --spk-1..8, tuned per theme in style.css),
// handed out per meeting in order of first appearance, so two speakers of one meeting never share a
// colour (up to 8; after that they repeat). No name / "?" / "Unknown" get the neutral --spk-0.
// `scope` is any object that lives as long as one shown meeting (its `spk` map is created on use);
// renderLines() empties the map before it walks the transcript in order.
const SPEAKER_HUES = 8;
const LIVE_SCOPE = {};  // for the dock's "who is speaking" when that meeting is not the one shown
const noSpeaker = (name) => !String(name || "").trim() || /^(\?+|unknown|unknown speaker)$/i.test(String(name).trim());
function speakerColor(scope, name) {
  if (noSpeaker(name)) return "var(--spk-0)";
  const map = scope.spk || (scope.spk = new Map());
  const key = String(name).trim();
  if (!map.has(key)) map.set(key, map.size % SPEAKER_HUES + 1);
  return `var(--spk-${map.get(key)})`;
}
const initials = (name) => {
  const n = String(name || "?").trim();
  const num = /(\d+)\s*$/.exec(n);
  if (/^speaker\b/i.test(n) && num) return num[1];
  return n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "?";
};

function niceDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return when(iso);
  const now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (same(d, now)) return `Today ${t}`;
  if (same(d, y)) return `Yesterday ${t}`;
  const opts = { weekday: "short", day: "numeric", month: "short" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return `${d.toLocaleDateString("en-GB", opts)} · ${t}`;
}

/* Short, relative label for lists: Today, Yesterday, Monday (2-6 days), Last week, 2 weeks ago,
   3 weeks ago, Last month, March (this year), March 2025 (older). `now` is injectable for tests. */
function relDate(iso, now = new Date()) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = (x) => Date.UTC(x.getFullYear(), x.getMonth(), x.getDate());  // calendar days, DST-proof
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff <= 0) return "Today";  // a clock a little ahead counts as today
  if (diff === 1) return "Yesterday";
  if (diff < 7) return d.toLocaleDateString("en-GB", { weekday: "long" });
  if (diff < 14) return "Last week";
  if (diff < 28) return `${Math.floor(diff / 7)} weeks ago`;
  const months = (now.getFullYear() - d.getFullYear()) * 12 + now.getMonth() - d.getMonth();
  if (months <= 0) return "4 weeks ago";
  if (months === 1) return "Last month";
  const month = d.toLocaleDateString("en-GB", { month: "long" });
  return d.getFullYear() === now.getFullYear() ? month : `${month} ${d.getFullYear()}`;
}

/* The labels above change with the date, not with data: repaint the lists when the day changes
   (a timer at midnight, and when the window is shown or focused again after a sleep). */
let dayStamp = "";
function watchDay() {
  const stamp = () => { const n = new Date(); return `${n.getFullYear()}-${n.getMonth()}-${n.getDate()}`; };
  const check = () => {
    const now = stamp();
    if (dayStamp && now !== dayStamp && typeof renderSide === "function" && !DRAG.active) renderSide();
    dayStamp = now;
  };
  const arm = () => {
    const n = new Date();
    const next = new Date(n.getFullYear(), n.getMonth(), n.getDate() + 1, 0, 0, 2);
    setTimeout(() => { check(); arm(); }, Math.max(1000, next - n));
  };
  check(); arm();
  window.addEventListener("focus", check);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
}

// ---------------------------------------------------------------- popover menu

let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  const { el, anchor } = openMenu;
  openMenu = null;
  anchor.setAttribute("aria-expanded", "false");
  el.classList.add("closing");
  setTimeout(() => el.remove(), 130);
}
/* items: [{label, sub, icon, onclick, disabled, danger, checked, keep}] | "-" | {header} */
function showMenu(anchor, build) {
  if (openMenu && openMenu.anchor === anchor) { closeMenu(); return; }
  closeMenu();
  const el = h("div", { class: "menu", role: "menu" });
  const fill = () => {
    el.replaceChildren(...build().map((it) => {
      if (it === "-") return h("hr");
      if (it.header) return h("div", { class: "mh", text: it.header });
      const toggle = it.checked !== undefined;
      const b = h("button", {
        class: "mi" + (it.danger ? " danger" : "") + (it.checked ? " checked" : ""), type: "button",
        role: toggle ? "menuitemcheckbox" : "menuitem", "aria-checked": toggle ? String(!!it.checked) : null,
        disabled: !!it.disabled, title: it.title || null },
      it.icon ? icon(it.icon) : null,
      h("span", { class: "mi-text" }, it.label, it.sub ? h("span", { class: "mi-sub", text: it.sub }) : null),
      toggle ? h("span", { class: "check" }) : null);
      b.addEventListener("click", () => {
        if (it.keep) { it.onclick(); fill(); return; }
        closeMenu(); it.onclick();
      });
      return b;
    }));
  };
  fill();
  document.body.append(el);
  const r = anchor.getBoundingClientRect(), mw = el.offsetWidth, mh = el.offsetHeight;
  const up = r.bottom + mh + 8 > window.innerHeight && r.top - mh - 8 > 0;
  el.classList.toggle("up", up);
  el.style.left = Math.max(8, Math.min(window.innerWidth - mw - 8, r.right - mw)) + "px";
  el.style.top = (up ? r.top - mh - 6 : r.bottom + 6) + "px";
  anchor.setAttribute("aria-expanded", "true");
  openMenu = { el, anchor };
  const first = el.querySelector(".mi:not(:disabled)");
  if (first) first.focus({ preventScroll: true });
}
document.addEventListener("pointerdown", (ev) => {
  if (openMenu && !openMenu.el.contains(ev.target) && !openMenu.anchor.contains(ev.target)) closeMenu();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { if (openMenu) { closeMenu(); ev.preventDefault(); } else closeDrawer(); }
});
window.addEventListener("resize", () => {
  closeMenu();
  if (!narrow()) closeDrawer();
  if (S.stick && S.view === "project") scrollToEnd(); else renderJump();
});

// ---------------------------------------------------------------- drawer (narrow windows)

const narrow = () => window.matchMedia("(max-width: 560px)").matches;
function openDrawer() { $("side").classList.add("open"); $("scrim").hidden = false; }
function closeDrawer() { $("side").classList.remove("open"); $("scrim").hidden = true; }

// ---------------------------------------------------------------- window buttons (desktop app with its own frame)

function initWindowButtons() {
  const call = (name, ...args) => {
    const api = window.pywebview && window.pywebview.api;
    return api && api[name] ? api[name](...args) : Promise.resolve(false);
  };
  for (const b of document.querySelectorAll("#winctl [data-win]")) {
    b.addEventListener("click", () => call("window_control", b.dataset.win));
  }
  const syncMax = async () => {
    if (!document.documentElement.classList.contains("custom-frame")) return;
    let max = false;
    try { max = !!(await call("window_maximized")); } catch (e) { /* not ready */ }
    const btn = $("win-max");
    if (btn.dataset.max === String(max)) return;
    btn.dataset.max = String(max);
    btn.replaceChildren(icon(max ? "winRestore" : "winMax"));
    btn.title = max ? "Restore" : "Maximize";
    btn.setAttribute("aria-label", btn.title);
    document.documentElement.classList.toggle("maximized", max);
  };
  window.addEventListener("resize", debounced(syncMax, 120));
  new MutationObserver(syncMax).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
}

// ---------------------------------------------------------------- resizable sidebar (wide windows)

const SIDE_MIN = 190, SIDE_MAX = 460, SIDE_HIDE = 120;
let sideW = Number(remember("steno-side-w")) || 252;
function applySide(hidden, persist = true) {
  sideW = Math.max(SIDE_MIN, Math.min(SIDE_MAX, Math.round(sideW)));
  document.documentElement.style.setProperty("--side-w", sideW + "px");
  document.body.classList.toggle("side-hidden", !!hidden);
  if (persist) { remember("steno-side-w", String(sideW)); remember("steno-side-hidden", hidden ? "1" : null); }
}
const sideHidden = () => document.body.classList.contains("side-hidden");
function toggleSide() { if (narrow()) { if ($("side").classList.contains("open")) closeDrawer(); else openDrawer(); } else applySide(!sideHidden()); }
function menuButton() { if (narrow()) openDrawer(); else applySide(false); }

function initResizer() {
  const bar = $("resizer");
  applySide(remember("steno-side-hidden") === "1", false);
  let dragging = false, startW = sideW;
  bar.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    dragging = true; startW = sideW; bar.setPointerCapture(ev.pointerId);
    document.body.classList.add("resizing");
    ev.preventDefault();
  });
  bar.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const w = ev.clientX - 8;
    if (w < SIDE_HIDE) { sideW = startW; applySide(true, false); return; }
    sideW = w; applySide(false, false);
  });
  const end = () => {
    if (!dragging) return;
    dragging = false; document.body.classList.remove("resizing");
    applySide(sideHidden());
  };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
  bar.addEventListener("dblclick", () => applySide(!sideHidden()));
  bar.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowLeft") { if (sideW <= SIDE_MIN) applySide(true); else { sideW -= 20; applySide(false); } ev.preventDefault(); }
    else if (ev.key === "ArrowRight") { if (sideHidden()) applySide(false); else { sideW += 20; applySide(false); } ev.preventDefault(); }
    else if (ev.key === "Enter" || ev.key === " ") { applySide(!sideHidden()); ev.preventDefault(); }
  });
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === "b") { toggleSide(); ev.preventDefault(); }
  });
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

const SIDE_MEETINGS = 6;  // meetings listed under a project before "Show all"
function loadExpanded() {
  try { return new Set(JSON.parse(remember("steno-expanded") || "[]")); } catch (e) { return new Set(); }
}
function loadFolded() {
  try { return new Set(JSON.parse(remember("steno-folded") || "[]")); } catch (e) { return new Set(); }
}
const S = {
  token: null,
  status: { recorder: {}, job: null, jobs: [], config: {} },
  projects: [], inbox: 0,
  view: "project",        // "project" | "people" | "settings"
  sel: null,              // project id or "inbox"
  meeting: null,          // open meeting (session id), "new" (ready to record) or null (pick one)
  pendingRec: false,      // Record pressed: open the new meeting as soon as it is listed
  meetings: [],           // list for the selected project, oldest first
  lists: new Map(),       // project id -> meetings (oldest first), for the sidebar
  expanded: loadExpanded(), showAll: new Set(), showArchived: new Set(),
  folded: loadFolded(),   // "pid::type" groups closed in the sidebar
  newType: new Map(),     // project id -> meeting type for the next recording ("" = none)
  feed: new Map(),        // session id -> {info, detail, docs, open, el, ...}
  loadingFeed: false, buffer: [], unknown: new Set(),
  stick: true, unseen: 0,
  opts: { mic: true, loop: true, keep: false },
  people: [], connected: false, fatal: false,
  jobStates: new Map(),
  // Claude Code: {state: unknown | ok | logged_out | missing | error, method, plan, detail}; `llmWait` = the time
  // until which we poll for the end of a sign-in; `autoTitle` = the dock's switch for the next meeting
  llm: { state: "unknown", method: "", plan: "", detail: "" }, llmWait: 0, llmPoll: false,
  autoTitle: true, autoTitleDefault: true,
};
// Drag a meeting to another place (see "drag to move" below): `press` while the long-press is
// pending, `active` while dragging, `suppress` swallows the click that ends a drag.
const DRAG = { press: null, active: null, suppress: false };

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

/* action: {label, run} adds a small button to the toast (kept up a little longer). */
function toast(text, kind, action) {
  const el = h("div", { class: "toast" + (kind ? " " + kind : "") }, icon(kind === "error" ? "alert" : "check"), h("span", { text: String(text) }));
  const dismiss = () => { if (el.classList.contains("out")) return; el.classList.add("out"); setTimeout(() => el.remove(), 230); };
  el.addEventListener("click", dismiss);
  if (action) {
    el.append(h("button", { class: "toast-act", type: "button", text: action.label,
      onclick: (ev) => { ev.stopPropagation(); dismiss(); action.run(); } }));
  }
  $("toasts").append(el);
  while ($("toasts").children.length > 4) $("toasts").firstChild.remove();
  setTimeout(dismiss, kind === "error" ? 9000 : action ? 8000 : 4500);
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
function formModal({ title, text, fields = [], ok = "OK", danger = false }) {
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
      if (f.options) input.className = "text";
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
      h("button", { class: "btn " + (danger ? "danger" : "primary"), type: "submit", text: ok })));
    dlg.replaceChildren(form);
    dlg.addEventListener("close", () => resolve(null), { once: true });  // Escape
    dlg.showModal();
    const first = form.querySelector("input, select");
    if (first) first.focus();
  });
}

// ---------------------------------------------------------------- projects and sidebar

const curProject = () => S.projects.find((p) => p.id === S.sel) || null;
const INBOX_NAME = "Inbox / TBD";
// The server calls an unnamed meeting "Meeting" (projects.DEFAULT_TITLE); the app shows it as untitled.
const DEFAULT_TITLE = "Meeting";
const titleOf = (info) => (info && info.title && info.title !== DEFAULT_TITLE ? info.title : "");
const projectName = (id) => (id === "inbox" || !id ? INBOX_NAME : (S.projects.find((p) => p.id === id) || {}).name || "project");

const projTypes = (pid) => ((S.projects.find((p) => p.id === pid) || {}).types || []);
/* The meeting type the next recording in a project goes to: chosen (+ or the dock's menu),
   else the open meeting's type, else the last one used, else none. */
function recType(pid) {
  const open = pid === S.sel && S.meeting && S.meeting !== "new" ? S.feed.get(S.meeting) : null;
  const t = S.newType.has(pid) ? S.newType.get(pid)
    : open ? open.info.type || "" : remember("steno-type-" + pid) || "";
  return projTypes(pid).some((x) => x.name === t) ? t : "";
}
function setRecType(pid, type) {
  S.newType.set(pid, type || "");
  remember("steno-type-" + pid, type || null);
}
function curType() {  // the type shown in the header: of the open meeting, or of the next one
  if (S.meeting === "new") return recType(S.sel);
  const m = S.feed.get(S.meeting);
  return (m && m.info.type) || "";
}

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
  if (S.sel) S.expanded.add(S.sel);
  const ids = new Set([...S.projects.map((p) => p.id), "inbox"]);
  for (const id of [...S.lists.keys()]) if (!ids.has(id)) S.lists.delete(id);
  await Promise.all([...S.expanded].filter((id) => ids.has(id) && id !== S.sel).map((id) => fetchList(id).catch(() => null)));
  renderSide(); renderTop();
  if (changed) { S.meeting = null; await loadFeed(); }
}

async function fetchList(pid) {
  let list;
  try { list = (await api("GET", `/api/projects/${encodeURIComponent(pid)}/meetings`)).meetings || []; }
  catch (e) { if (!missing(e)) throw e; list = []; }
  S.lists.set(pid, list);
  return list;
}

function saveExpanded() { remember("steno-expanded", JSON.stringify([...S.expanded])); }

function toggleExpanded(id) {
  if (S.expanded.has(id)) S.expanded.delete(id); else S.expanded.add(id);
  saveExpanded();
  renderSide();
  if (S.expanded.has(id) && !S.lists.has(id)) guarded(() => fetchList(id).then(renderSide));
}

function ensureSelection() {  // returns true when the selected project had to change
  if (S.view !== "project") return false;
  // the Inbox / TBD is always there, also when empty
  const exists = (id) => id === "inbox" || S.projects.some((p) => p.id === id);
  if (exists(S.sel)) return false;
  const saved = remember("steno-project");
  const pick = [recProjectId(), saved, (S.projects[0] || {}).id, S.inbox > 0 ? "inbox" : null]
    .find((id) => id && exists(id));
  const next = pick || null;
  const changed = next !== S.sel;
  S.sel = next;
  if (changed) { S.feed.clear(); S.meetings = []; S.unknown.clear(); }
  return changed;
}

function selectProject(id, meeting) {
  const same = S.sel === id;
  S.view = "project"; S.sel = id; S.meeting = meeting || null; S.unseen = 0;
  if (meeting && meeting !== "new") S.newType.delete(id);  // the dock follows the open meeting
  if (!same) { S.feed.clear(); S.meetings = S.lists.get(id) || []; S.unknown.clear(); }
  if (!S.expanded.has(id)) { S.expanded.add(id); saveExpanded(); }
  remember("steno-project", id);
  if (S.meeting !== "new" && S.lists.has(id)) pickMeeting();  // else loadFeed picks once the list is in
  showView(); renderSide(); renderTop(); renderFeed();
  guarded(loadFeed);
}

const selectMeeting = (pid, sid) => selectProject(pid, sid);

function newMeeting(pid, type) {
  if (type !== undefined) setRecType(pid, type);
  selectProject(pid, "new");
  setTimeout(() => { if (!$("rec-title").disabled) $("rec-title").focus(); }, 60);
}

/* Which meeting to open: the one asked for, else the live one, else the latest, else "new". */
function pickMeeting() {
  const list = S.meetings, r = S.status.recorder || {};
  if (S.pendingRec && r.state === "recording" && r.session && r.session !== S.pendingFrom && list.some((i) => i.session === r.session)) {
    S.meeting = r.session; S.pendingRec = false;
  }
  if (S.meeting === "new") return;
  if (S.meeting && list.some((i) => i.session === S.meeting)) return;
  const visible = list.filter((i) => !i.archived);
  const live = list.find((i) => i.recording);
  S.meeting = live ? live.session : visible.length ? visible[visible.length - 1].session : "new";
}

function showPeople() {
  S.view = "people";
  showView(); renderSide();
  guarded(loadPeople);
}

function showView() {
  closeDrawer(); closeMenu();
  $("view-project").hidden = S.view !== "project";
  $("view-people").hidden = S.view !== "people";
  $("view-settings").hidden = S.view !== "settings";
}

function showSettings() {
  S.view = "settings";
  showView(); renderSide();
  guarded(loadSettings);
}

// ---------------------------------------------------------------- settings

const AUTO = [
  ["auto_finalize", "Sort out speakers automatically", "A few seconds after Stop, on this computer: who said what is worked out again from the whole meeting, more accurately than live. No kept audio needed."],
  ["auto_title", "Automatic title", "Claude names a meeting you left untitled from what was said, e.g. \"Budgetgenomgång Q1\". A title you type is never replaced. Per meeting: the Auto title switch next to the title field."],
  ["auto_clean", "Clean up automatically", "After a recording stops, Claude fixes misheard words and punctuation."],
  ["auto_summary", "Summary automatically", "Then writes summary.md in the meeting folder."],
  ["auto_todo", "To-do automatically", "Then writes todo.md with explicit commitments."],
];

async function loadSettings() {
  let data;
  try { data = await api("GET", "/api/settings"); } catch (e) { if (!missing(e)) throw e; data = { settings: {} }; }
  const s = data.settings || {};
  if (typeof s.auto_title === "boolean") setAutoTitleDefault(s.auto_title);
  const box = $("settings");
  const page = h("div", { class: "page" });
  box.replaceChildren(page);
  applyLook(s);
  const seg = h("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Theme" });
  const paintSeg = () => {
    for (const b of seg.children) {
      const on = b.dataset.v === LOOK.theme;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    }
  };
  for (const [v, label] of [["system", "System"], ["dark", "Dark"], ["light", "Light"]]) {
    seg.append(h("button", { type: "button", role: "radio", "data-v": v, text: label,
      onclick: async () => { LOOK.theme = v; paintSeg(); applyLook(); await saveLook({ theme: v }); paintSeg(); } }));
  }
  paintSeg();
  const pal = h("div", { class: "pal-grid", role: "radiogroup", "aria-label": "Colours" });
  const paintPal = () => {
    const light = document.documentElement.classList.contains("light");
    for (const [id, label, dark, lite] of PALETTES) {
      const on = id === LOOK.palette;
      const b = pal.querySelector(`[data-v="${id}"]`);
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
      b.querySelectorAll("i").forEach((d, i) => { d.style.background = (light ? lite : dark)[i]; });
    }
  };
  for (const [id, label] of PALETTES) {
    pal.append(h("button", { type: "button", role: "radio", class: "pal-opt", "data-v": id,
      onclick: async () => { LOOK.palette = id; applyLook(); paintPal(); await saveLook({ palette: id }); paintPal(); } },
      h("span", { class: "pal-dots" }, h("i"), h("i")), label));
  }
  paintPal();
  seg.addEventListener("click", () => setTimeout(paintPal, 0));
  const strip = h("input", { type: "checkbox", class: "switch", id: "set-mini" });
  strip.checked = s.mini_strip !== false;
  strip.addEventListener("change", async () => {
    const res = await guarded(() => api("POST", "/api/settings", { mini_strip: strip.checked }));
    if (!res) strip.checked = !strip.checked;
  });
  const glass = h("input", { type: "checkbox", class: "switch", id: "set-transparent" });
  glass.checked = LOOK.transparent;
  glass.addEventListener("change", async () => {
    applyLook({ transparent: glass.checked });
    const res = await saveLook({ transparent: glass.checked });
    if (!res) { glass.checked = !glass.checked; applyLook({ transparent: glass.checked }); }
  });
  page.append(h("h2", { class: "set-h", text: "Appearance" }),
    h("div", { class: "group" },
      h("div", { class: "set-row static" },
        h("div", { class: "pn" }, h("div", { text: "Theme" }), h("div", { class: "sub", text: "System follows Windows' light or dark app mode." })), seg),
      h("div", { class: "set-row static" },
        h("div", { class: "pn" }, h("div", { text: "Colours" }), h("div", { class: "sub", text: "Accent colour on grey surfaces. Every palette has a dark and a light version." })), pal),
      h("label", { class: "set-row", for: "set-transparent" },
        h("div", { class: "pn" }, h("div", { text: "Transparent background" }), h("div", { class: "sub", text: "Your wallpaper shows softly through the window. Off: solid background." })), glass),
      h("label", { class: "set-row", for: "set-mini" },
        h("div", { class: "pn" }, h("div", { text: "Mini strip while recording" }), h("div", { class: "sub", text: "A small bar with timer, Pause and Stop stays on top of other windows while Steno is not in front. Drag it anywhere; x hides it until the next recording. Hidden from screen sharing." })), strip)));
  const group = h("div", { class: "group" });
  page.append(h("h2", { class: "set-h", text: "Claude" }),
    h("div", { class: "group" }, h("div", { class: "set-row static", id: "set-llm" })));
  renderSettingsLlm();
  page.append(h("h2", { class: "set-h", text: "After a meeting" }),
    h("p", { class: "set-hint", text: "Runs in this order once you press Stop. Title, Clean up, Summary and To-do use Claude, about 10-30 s each. Off: use the buttons on each meeting." }),
    group);
  for (const [key, label, sub] of AUTO) {
    const box2 = h("input", { type: "checkbox", class: "switch", id: `set-${key}` });
    box2.checked = !!s[key];
    box2.addEventListener("change", async () => {
      const res = await guarded(() => api("POST", "/api/settings", { [key]: box2.checked }));
      if (!res) box2.checked = !box2.checked;
      else if (key === "auto_title") setAutoTitleDefault(box2.checked);
    });
    group.append(h("label", { class: "set-row", for: `set-${key}` },
      h("div", { class: "pn" }, h("div", { text: label }), h("div", { class: "sub", text: sub })), box2));
  }
  const area = h("textarea", { class: "text set-area", id: "set-glossary", rows: "6", maxlength: "8000",
    placeholder: "One per line, e.g.\nJira\nCanvas\nClaude Code", spellcheck: "false" });
  area.value = (s.glossary || []).join("\n");
  const save = h("button", { class: "btn small primary", type: "button", text: "Save glossary", onclick: async () => {
    const terms = area.value.split("\n").map((t) => t.trim()).filter(Boolean);
    const res = await guarded(() => api("POST", "/api/settings", { glossary: terms }));
    if (res) { area.value = res.settings.glossary.join("\n"); toast(`Glossary saved (${res.settings.glossary.length} terms)`); }
  } });
  page.append(h("h2", { class: "set-h", text: "Glossary" }),
    h("p", { class: "set-hint", text: "Words your meetings use: product names, course codes, names. Clean up uses them as the right spelling when the speech recognition misheard them (\"J här\" becomes \"Jira\")." }),
    area, h("div", { class: "set-actions" }, save));
  const cfg = S.status.config || {};
  const kv = (k, v) => h("div", { class: "kv" }, h("span", { text: k }), h("span", { text: v || "-" }));
  page.append(h("h2", { class: "set-h", text: "About" }),
    h("div", { class: "group" },
      kv("Speech recognition", cfg.asr_model), kv("Language model", cfg.llm_model),
      kv("Version", S.status.version ? "v" + S.status.version : ""),
      kv("Data", "Audio stays on this computer. Only transcript text goes to Claude, for Title, Clean up, Summary and To-do.")));
}

function sideMeetings(id) {
  const r = S.status.recorder || {};
  const here = S.view === "project" && S.sel === id;
  const every = (S.lists.get(id) || []).slice().reverse();  // newest first
  const list = every.filter((i) => !i.archived);
  const archived = every.filter((i) => i.archived);
  const types = id === "inbox" ? [] : projTypes(id);
  const draft = () => h("div", { class: "mitem active draft" }, h("span", { class: "mi-title", text: S.pendingRec ? "Starting..." : "New meeting" }));
  const row = (info) => {
    const live = info.recording || (r.session === info.session && r.state && r.state !== "idle" && r.state !== "error");
    const active = here && S.meeting === info.session;
    const more = h("button", { class: "mmore", type: "button", title: "Meeting actions", "aria-label": "Meeting actions", "aria-haspopup": "menu" }, icon("more"));
    more.addEventListener("click", (ev) => { ev.stopPropagation(); showMenu(more, () => meetingItems(info, id, null)); });
    const full = niceDate(info.started_at);  // the row shows a relative label; the tooltip the exact time
    const main = h("button", { class: "mitem" + (active ? " active" : ""), type: "button",
      title: (titleOf(info) || "Untitled meeting") + (full ? "\n" + full : ""), onclick: () => selectMeeting(id, info.session) },
    h("span", { class: "mi-title" + (titleOf(info) ? "" : " untitled"), text: titleOf(info) || "Untitled meeting" }),
    live ? h("i", { class: "live-dot", title: "Recording" }) : h("span", { class: "mi-date", text: relDate(info.started_at) }));
    armDrag(main, info, id);
    const dragged = DRAG.active && DRAG.active.info.session === info.session;
    return h("div", { class: "mrow" + (active ? " active" : "") + (info.archived ? " archived" : "") + (dragged ? " dragging" : ""),
      "data-ts": String(Date.parse(info.started_at) || 0) }, main, more);
  };
  const rows = (key, part) => {  // newest first, "Show all" past SIDE_MEETINGS
    const all = S.showAll.has(key);
    const out = (all ? part : part.slice(0, SIDE_MEETINGS)).map(row);
    if (part.length > SIDE_MEETINGS) {
      out.push(h("button", { class: "mitem more", type: "button", text: all ? "Show fewer" : `Show all ${part.length}`,
        onclick: () => { if (all) S.showAll.delete(key); else S.showAll.add(key); renderSide(); } }));
    }
    return out;
  };
  const items = [];
  const drafting = here && S.meeting === "new";
  if (!types.length) {
    if (drafting) items.push(draft());
    items.push(...rows(id, list));
    if (!items.length && S.lists.has(id)) items.push(h("div", { class: "mitem none", text: "No meetings yet" }));
  } else {
    // subfolders first (level 1, meetings inside at level 2), then the meetings without a type (level 1)
    const chosen = drafting ? recType(id) : null;
    const untyped = list.filter((i) => !i.type || !types.some((t) => t.name === i.type));
    for (const t of types) {
      const type = t.name;
      const part = list.filter((i) => i.type === type);
      const key = `${id}::${type}`;
      const open = !S.folded.has(key) || chosen === type;
      const body = [];
      if (chosen === type) body.push(draft());
      body.push(...rows(key, part));
      if (!body.length && S.lists.has(id)) body.push(h("div", { class: "mitem none", text: "No meetings yet" }));
      items.push(h("div", { class: "tnode" + (open ? " open" : ""), "data-drop": id, "data-drop-type": type },
        h("div", { class: "trow" },
          h("button", { class: "tmain", type: "button", "aria-expanded": String(open), title: `${type}: ${t.folder || ""}`,
            onclick: () => { if (open) S.folded.add(key); else S.folded.delete(key); remember("steno-folded", JSON.stringify([...S.folded])); renderSide(); } },
          icon("chevron"), h("i", { class: "ticon" }, icon("folder")), h("span", { class: "tname", text: type }), open ? null : h("span", { class: "tcount", text: String(part.length) })),
          explorerButton(`${type} (${projectName(id)})`, "type", id, type),
          subfolderMore(id, type, part.length),
          h("button", { class: "padd", type: "button", title: `New ${type} meeting`, "aria-label": `New ${type} meeting`, onclick: () => newMeeting(id, type) }, icon("plus"))),
        open ? h("div", { class: "tlist" }, body) : null));
    }
    if (chosen === "") items.push(draft());
    items.push(...rows(`${id}::`, untyped));
  }
  if (archived.length) {
    const open = S.showArchived.has(id);
    items.push(h("button", { class: "mitem more arch-toggle", type: "button",
      onclick: () => { if (open) S.showArchived.delete(id); else S.showArchived.add(id); renderSide(); } },
    icon("archive"), h("span", { text: `Archived (${archived.length})` }), icon("chevron", open ? "up" : "")));
    if (open) for (const info of archived) items.push(row(info));
  }
  return h("div", { class: "mlist" }, items);
}

function projectNode(id, name, count, canRecord, live) {
  const open = S.expanded.has(id);
  const here = S.view === "project" && S.sel === id;
  const chev = h("button", { class: "pchev", type: "button", "aria-label": open ? "Collapse" : "Expand", "aria-expanded": String(open), onclick: () => toggleExpanded(id) }, icon("chevron"));
  const main = h("button", { class: "pmain", type: "button", onclick: () => selectProject(id) },
    h("i", { class: "picon" }, icon(id === "inbox" ? "inbox" : "folder")),
    h("span", { class: "pname", text: name }),
    live ? h("i", { class: "live-dot", title: "Recording" }) : open || (id === "inbox" && !count) ? null : h("span", { class: "pcount", text: String(count) }));
  const add = canRecord ? h("button", { class: "padd", type: "button", title: `New meeting in ${name}`, "aria-label": `New meeting in ${name}`, onclick: () => newMeeting(id) }, icon("plus")) : null;
  const sub = canRecord && id !== "inbox" ? h("button", { class: "padd", type: "button", title: `New subfolder in ${name} (e.g. Board, 1-1)`, "aria-label": `New subfolder in ${name}`, onclick: () => guarded(() => newSubfolder(id)) }, icon("folderPlus")) : null;
  const show = id === "inbox" ? explorerButton(name, "inbox") : explorerButton(name, "project", id);
  return h("div", { class: "pnode" + (open ? " open" : "") + (here ? " here" : ""), "data-drop": id, "data-drop-type": "" },
    h("div", { class: "pitem prow" + (here && !open ? " active" : "") }, chev, main, show, sub, add),
    open ? sideMeetings(id) : null);
}

/* Hover button on a project / subfolder / Inbox row: show its folder in File Explorer. The page
   only sends what the row stands for (kind, project id, subfolder name); the desktop app works
   out the path. Not shown in a plain browser. */
function explorerButton(label, kind, ref, type) {
  if (!pickerAvailable()) return null;
  const tip = `Show ${label} in File Explorer`;
  return h("button", { class: "padd pexp", type: "button", title: tip, "aria-label": tip,
    onclick: (ev) => { ev.stopPropagation(); guarded(() => openInExplorer(kind, ref, type)); } }, icon("folderOpen"));
}
async function openInExplorer(kind, ref, type) {
  const bridge = window.pywebview && window.pywebview.api;
  if (!bridge || !bridge.open_folder) { toast("Opening folders needs the desktop app.", "error"); return; }
  const ok = await bridge.open_folder(kind, ref || null, type || null);
  if (!ok) toast("That folder was not found.", "error");
}

function renderSide() {
  const live = recProjectId();
  $("plist").replaceChildren(...S.projects.map((p) => projectNode(p.id, p.name, p.meetings, true, live === p.id)));
  $("inbox-slot").replaceChildren(projectNode("inbox", INBOX_NAME, S.inbox, true, live === "inbox"));
  $("nav-people").classList.toggle("active", S.view === "people");
  $("nav-settings").classList.toggle("active", S.view === "settings");
  if (DRAG.active && !DRAG.active.settled) { paintDrop(); syncGap(false); }
  $("people-count").textContent = S.people.length ? String(S.people.length) : "";
  const elsewhere = live && (S.view !== "project" || live !== S.sel);
  document.querySelectorAll(".menu-btn").forEach((b) => b.classList.toggle("badge", !!elsewhere));
  renderSideFoot();
}

function renderSideFoot() {  // only shown while the connection to the local server is lost
  const foot = $("side-foot");
  foot.hidden = !!S.connected;
  if (!S.connected && !foot.firstChild) foot.replaceChildren(h("i", { class: "state" }), h("span", { text: "Reconnecting..." }));
}

// ---------------------------------------------------------------- Claude Code status (AI features)

const aiOff = () => S.llm.state === "logged_out" || S.llm.state === "missing";
const aiOk = () => S.llm.state === "ok";
const AI_OFF_TEXT = { logged_out: "AI off: not logged in", missing: "AI off: Claude Code not installed" };
const llmDetail = () => [S.llm.method, S.llm.plan].filter(Boolean).join(" · ");  // never an account name
const llmWaiting = () => S.llmWait > Date.now();

async function fetchLlm(refresh) {
  try {
    const res = await api("GET", "/api/llm/status" + (refresh ? "?refresh=1" : ""));
    if (res && res.llm) S.llm = res.llm;
  } catch (e) { return; }  // older server or not reachable: keep what we know
  renderLlm();
}

function renderLlm() {
  renderSideLlm(); renderSettingsLlm(); renderAutoTitle(); updateButtons();
}

async function llmLogin() {
  const res = await api("POST", "/api/llm/login", {});
  S.llmWait = Date.now() + 180000;
  renderLlm();
  toast(res.started ? "Sign-in window opened. Finish signing in to Claude in your browser." : "The sign-in window is already open.");
  pollLlm();
}

async function pollLlm() {  // every few seconds for up to ~3 minutes, until the login shows up
  if (S.llmPoll) return;
  S.llmPoll = true;
  try {
    while (llmWaiting()) {
      await new Promise((r) => setTimeout(r, 3000));
      await fetchLlm(true);
      if (aiOk()) { toast("Logged in to Claude"); break; }
    }
  } finally { S.llmWait = 0; S.llmPoll = false; renderLlm(); }
}

const llmInstall = () => api("POST", "/api/llm/install", {});

function llmMenuItems() {
  const st = S.llm.state, items = [];
  if (st === "logged_out") {
    items.push({ header: "AI is off: not logged in" },
      { label: "Log in to Claude", sub: "Opens a sign-in window; finish in your browser", icon: "login", onclick: () => guarded(llmLogin) });
  } else if (st === "missing") {
    items.push({ header: "AI is off: Claude Code is not installed" },
      { label: "Install Claude Code", sub: "Opens the install page in your browser", icon: "external", onclick: () => guarded(llmInstall) });
  } else if (st === "ok") {
    items.push({ header: "Claude is signed in" + (llmDetail() ? ` (${llmDetail()})` : "") });
  } else {
    items.push({ header: S.llm.detail || "Claude Code could not be checked" });
  }
  items.push({ label: "Check again", icon: "refresh", onclick: () => guarded(() => fetchLlm(true)) });
  return items;
}

function renderSideLlm() {
  const box = $("side-llm"), st = S.llm.state, waiting = llmWaiting();
  if (st === "unknown" && !waiting) { box.hidden = true; box.dataset.key = ""; box.replaceChildren(); return; }
  let label, tip, cls;
  if (waiting) { label = "Waiting for sign-in..."; cls = "wait"; tip = "Finish signing in to Claude in the browser window that opened."; }
  else if (st === "ok") {
    label = "Claude"; cls = "ok";
    tip = `Claude is signed in${llmDetail() ? " (" + llmDetail() + ")" : ""}. Summary, To-do, Clean up and automatic titles use it. Click for options.`;
  } else if (aiOff()) {
    label = AI_OFF_TEXT[st]; cls = "warn";
    tip = st === "missing" ? "Install Claude Code to use Summary, To-do, Clean up and automatic titles." : "Log in to Claude to use Summary, To-do, Clean up and automatic titles.";
  } else { label = "AI status unknown"; cls = "warn"; tip = S.llm.detail || "Claude Code could not be checked."; }
  const key = [label, cls, tip].join("|");
  box.hidden = false;
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  const btn = h("button", { class: "llm-pill " + cls, type: "button", title: tip, "aria-haspopup": "menu" },
    cls === "wait" ? h("i", { class: "spinner" }) : h("i", { class: "llm-dot" }), h("span", { text: label }));
  btn.addEventListener("click", () => showMenu(btn, llmMenuItems));
  box.replaceChildren(btn);
}

function renderSettingsLlm() {  // the status line in Settings, with the same buttons
  const row = $("set-llm");
  if (!row) return;
  const st = S.llm.state;
  let title, sub, action, cls = "warn";
  const button = (text, fn, primary) => h("button", { class: "btn small" + (primary ? " primary" : ""), type: "button", text, onclick: () => guarded(fn) });
  if (llmWaiting()) {
    title = "Waiting for sign-in..."; cls = "wait";
    sub = "Finish signing in to Claude in the browser. This updates by itself.";
  } else if (st === "ok") {
    title = "Claude is ready"; cls = "ok";
    sub = `Signed in${llmDetail() ? " (" + llmDetail() + ")" : ""}. Only transcript text is sent; audio never leaves this computer.`;
    action = button("Check again", () => fetchLlm(true));
  } else if (st === "logged_out") {
    title = AI_OFF_TEXT.logged_out;
    sub = "Summary, To-do, Clean up and automatic titles use Claude Code with your own Claude account. The sign-in finishes in your browser.";
    action = button("Log in to Claude", llmLogin, true);
  } else if (st === "missing") {
    title = AI_OFF_TEXT.missing;
    sub = "Summary, To-do, Clean up and automatic titles use Claude Code, which signs in with your own Claude account.";
    action = button("Install Claude Code", llmInstall, true);
  } else {
    title = st === "unknown" ? "Checking Claude ..." : "Claude could not be checked";
    sub = S.llm.detail || "";
    action = button("Check again", () => fetchLlm(true));
  }
  row.replaceChildren(
    h("div", { class: "pn" }, h("div", { class: "llm-title " + cls }, h("i", { class: "llm-dot" }), h("span", { text: title })), h("div", { class: "sub", text: sub })),
    action || null);
}

// The dock's per-meeting "Auto title" switch: Claude names the meeting after Stop, unless a title was typed.
function setAutoTitleDefault(on) {
  if (S.autoTitleDefault === on) return;
  S.autoTitleDefault = on;
  S.autoTitle = on;
  renderAutoTitle();
}

function renderAutoTitle() {
  const r = S.status.recorder || {}, st = r.state || "idle";
  const active = st === "starting" || st === "recording" || st === "stopping";
  const input = $("rec-title");
  const typed = !active && !!input.value.trim();
  const off = aiOff();
  const on = (active ? !!r.auto_title : S.autoTitle) && !off && !typed;
  input.placeholder = on && aiOk() ? "Title, or let Claude name it" : "Meeting title (optional)";
  let tip;
  if (off) tip = `${AI_OFF_TEXT[S.llm.state]}. Log in under Settings to use Auto title.`;
  else if (typed) tip = "Auto title is not needed: you typed a title. Clear the field to use it.";
  else if (st === "stopping") tip = "The meeting is being saved.";
  else if (on) tip = "Auto title is ON: Claude names the meeting when it ends. Click to turn it off for this meeting.";
  else tip = "Auto title is off. Click to let Claude name this meeting when it ends. You can also press Auto title on the meeting afterwards.";
  for (const b of [$("btn-autotitle"), $("btn-autotitle-live")]) {
    b.disabled = off || typed || st === "stopping";
    b.setAttribute("aria-pressed", String(on));
    b.classList.toggle("on", on);
    b.title = tip;
  }
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
  const ok = await formModal({ title: "Forget project", text: `Remove "${p.name}" from Steno? Its folder and meetings stay on disk.`, ok: "Forget", danger: true });
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
  const ctype = proj ? curType() : "";
  $("proj-name").textContent = S.sel === "inbox" ? INBOX_NAME : proj ? proj.name + (ctype ? "  ›  " + ctype : "") : "";
  renderTypeSelect(proj, active);
  $("btn-projmenu").hidden = !proj;
  const noProject = !S.sel;
  const dock = $("dock");
  dock.dataset.state = st === "recording" ? (r.paused ? "paused" : "recording") : st;
  $("rec-title").disabled = noProject || active;
  const rec = $("btn-rec");
  rec.disabled = noProject || st === "starting" || st === "stopping";
  $("btn-rec-label").textContent = st === "starting" ? "Starting..." : "Record";
  const pause = $("btn-pause");
  if (pause.dataset.paused !== String(!!r.paused)) {
    pause.dataset.paused = String(!!r.paused);
    pause.replaceChildren(icon(r.paused ? "play" : "pause"), h("span", { class: "lbl", text: r.paused ? "Resume" : "Pause" }));
    pause.title = r.paused ? "Resume" : "Pause";
  }
  pause.disabled = st !== "recording";
  $("btn-stop").disabled = st !== "recording";
  const on = sourcesOn();
  const srcKey = `${on.mic}${on.loop}`;
  $("btn-src").disabled = active;
  const keepBtn = $("btn-keep");
  keepBtn.disabled = active;
  keepBtn.setAttribute("aria-pressed", String(!!S.opts.keep));
  keepBtn.classList.toggle("on", !!S.opts.keep);
  keepBtn.title = S.opts.keep
    ? "Keep audio: ON. The raw recording is saved on this computer (not in the project folder). Click to turn off."
    : "Keep audio: off. Turn on to save the raw recording on this computer, e.g. to compare speech models. Not needed for speakers.";
  if ($("btn-src").dataset.key !== srcKey) {
    $("btn-src").dataset.key = srcKey;
    $("btn-src").replaceChildren(icon("mic", on.mic ? "" : "off"), icon("system", on.loop ? "" : "off"));
  }
  $("btn-src").title = `Audio sources: ${[on.mic && "microphone", on.loop && "system audio"].filter(Boolean).join(" + ") || "none"}${on.keep ? ", keeping audio" : ""}`;
  $("empty").hidden = !(S.view === "project" && !S.sel && S.projects.length === 0 && S.inbox === 0);
  $("feed").parentElement.hidden = !$("empty").hidden;
  dock.hidden = !$("empty").hidden;
  renderAutoTitle();
  renderStrip();
  updateButtons();
}

// Subfolder of the next meeting: a button in the dock that opens a menu (a native <select> drops a
// white list on Windows that ignores the theme).
function renderTypeSelect(proj, active) {
  const btn = $("rec-type");
  const types = proj ? proj.types || [] : [];
  btn.hidden = !types.length;
  btn.disabled = active;
  if (!types.length) return;
  const cur = recType(proj.id);
  const key = [proj.id, proj.name, cur, ...types.map((t) => t.name)].join("|");
  if (btn.dataset.key === key) return;
  btn.dataset.key = key;
  btn.replaceChildren(icon("folder"), h("span", { class: "dt-name", text: cur || "Project root" }), icon("chevron"));
  btn.title = `Saved in: ${proj.name} › ${cur || "project root"}. Click to change.`;
}

function typeMenuItems() {
  const proj = curProject();
  if (!proj) return [];
  const cur = recType(proj.id);
  const pick = (name) => () => { setRecType(proj.id, name); renderSide(); renderTop(); if (S.meeting === "new") renderFeed(); };
  return [{ header: "Save the next meeting in" },
    ...(proj.types || []).map((t) => ({ label: t.name, sub: t.folder || null, icon: t.name === cur ? "check" : "folder", onclick: pick(t.name) })),
    "-",
    { label: "Project root", sub: "Steno/", icon: cur ? "folder" : "check", onclick: pick("") }];
}

/* Edit a project's meeting types: name + folder (inside the project) per row. */
function typesModal(p) {
  return new Promise((resolve) => {
    const dlg = $("modal");
    const list = h("div", { class: "typerows" });
    const addRow = (t) => {
      const name = h("input", { class: "text tn", type: "text", value: t.name || "", placeholder: "e.g. Board", maxlength: "80", autocomplete: "off" });
      const folder = h("input", { class: "text tf", type: "text", value: t.folder || "", placeholder: "Meetings/Board", autocomplete: "off" });
      let auto = !t.folder;  // follow the name until the folder is edited by hand
      name.addEventListener("input", () => { if (auto) folder.value = name.value.trim() ? "Meetings/" + name.value.trim() : ""; });
      folder.addEventListener("input", () => { auto = false; });
      const rm = h("button", { class: "icon-btn", type: "button", title: "Remove subfolder (only when it has no meetings)", "aria-label": "Remove subfolder" }, icon("x"));
      const row = h("div", { class: "typerow" }, name, folder, rm);
      rm.addEventListener("click", () => row.remove());
      list.append(row);
      return name;
    };
    for (const t of p.types || []) addRow(t);
    const err = h("p", { class: "ferr", role: "alert", hidden: true });
    const finish =(v) => { resolve(v); if (dlg.open) dlg.close(); };
    const form = h("form", { class: "mform", onsubmit: async (ev) => {
      ev.preventDefault();
      const types = [...list.children].map((row) => {
        const [n, f] = row.querySelectorAll("input");
        return { name: n.value.trim(), folder: f.value.trim() };
      }).filter((t) => t.name || t.folder);
      err.hidden = true;
      try {
        const res = await api("POST", `/api/projects/${encodeURIComponent(p.id)}/types`, { types });
        finish(res.types);
      } catch (e) { err.textContent = e.message; err.hidden = false; }  // toasts sit behind the dialog
    } },
    h("h3", { text: `Subfolders in ${p.name}` }),
    h("p", { text: "Each type saves its meetings in its own folder inside the project, e.g. Meetings/Board/2026-11-20 Lobby days. Meetings without a type stay in Steno/. No types = one simple list." }),
    h("div", { class: "typehead" }, h("span", { text: "Name" }), h("span", { text: "Folder in the project" })),
    list,
    h("button", { class: "btn", type: "button", onclick: () => addRow({}).focus() }, icon("plus"), h("span", { text: "Add subfolder" })),
    err,
    h("div", { class: "btns" },
      h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => finish(null) }),
      h("button", { class: "btn primary", type: "submit", text: "Save" })));
    dlg.replaceChildren(form);
    dlg.addEventListener("close", () => resolve(null), { once: true });
    dlg.showModal();
    if (!(p.types || []).length) addRow({}).focus();
  });
}

/* Subfolders (meeting types) straight from the sidebar: create, rename, remove. */
const typeList = (pid) => projTypes(pid).map((t) => ({ name: t.name, folder: t.folder }));
async function saveTypes(pid, types) {
  await api("POST", `/api/projects/${encodeURIComponent(pid)}/types`, { types });
  if (!S.expanded.has(pid)) { S.expanded.add(pid); saveExpanded(); }
  await loadProjects();
}
async function newSubfolder(pid) {
  const p = S.projects.find((x) => x.id === pid);
  if (!p) return;
  const v = await formModal({
    title: `New subfolder in ${p.name}`,
    text: "Meetings of one kind get their own folder inside the project, e.g. Meetings/Board/2026-11-20 Lobby days. Press + next to it to record one.",
    fields: [
      { key: "name", label: "Name", placeholder: "e.g. Board, Dev team, 1-1, External" },
      { key: "folder", label: "Folder in the project (optional)", placeholder: "Meetings/<name>" }],
    ok: "Create" });
  if (!v || !v.name) return;
  await saveTypes(pid, [...typeList(pid), { name: v.name, folder: v.folder }]);
  // the folder is made on disk together with the subfolder, so Explorer can show it right away
  toast(`Subfolder ${v.name} created`, null, pickerAvailable()
    ? { label: "Open in File Explorer", run: () => guarded(() => openInExplorer("type", pid, v.name)) } : null);
}
async function renameSubfolder(pid, name) {
  const v = await formModal({ title: "Rename subfolder", text: "Only the name in Steno changes; the folder on disk stays.",
    fields: [{ key: "name", label: "Name", value: name }], ok: "Rename" });
  if (!v || !v.name || v.name === name) return;
  await saveTypes(pid, typeList(pid).map((t) => (t.name === name ? { ...t, name: v.name } : t)));
}
async function removeSubfolder(pid, name, count) {
  if (count) { toast(`${name} has meetings: move or delete them first.`, "error"); return; }
  const ok = await formModal({ title: "Remove subfolder", text: `Remove ${name} from Steno? Nothing on disk is deleted.`, ok: "Remove", danger: true });
  if (!ok) return;
  await saveTypes(pid, typeList(pid).filter((t) => t.name !== name));
}
function subfolderMore(pid, name, count) {
  const more = h("button", { class: "padd tmore", type: "button", title: `${name}: rename or remove`, "aria-label": `${name} actions`, "aria-haspopup": "menu" }, icon("more"));
  more.addEventListener("click", (ev) => { ev.stopPropagation(); showMenu(more, () => [
    ...(pickerAvailable() ? [{ label: "Open in File Explorer", icon: "folderOpen", onclick: () => guarded(() => openInExplorer("type", pid, name)) }] : []),
    { label: "Rename...", icon: "pencil", onclick: () => guarded(() => renameSubfolder(pid, name)) },
    { label: "Remove", sub: count ? "Move its meetings out first" : "Files on disk stay", icon: "trash", danger: true, disabled: !!count, onclick: () => guarded(() => removeSubfolder(pid, name, count)) }]); });
  return more;
}

async function editTypes() {
  const p = curProject();
  if (!p) return;
  const types = await typesModal(p);
  if (!types) return;
  toast(types.length ? `Subfolders saved (${types.length})` : "All subfolders removed");
  await loadProjects();
}

function sourcesOn() {
  const r = S.status.recorder || {}, st = r.state || "idle";
  if (st === "starting" || st === "recording" || st === "stopping") {
    const src = r.sources || [];
    return { mic: src.includes("mic"), loop: src.includes("loopback"), keep: !!r.keep_audio };
  }
  return S.opts;
}

function sourceMenu() {
  const t = (key) => () => { S.opts[key] = !S.opts[key]; remember("steno-opts", JSON.stringify(S.opts)); renderTop(); };
  return [
    { header: "Record from" },
    { label: "Microphone", icon: "mic", checked: S.opts.mic, keep: true, onclick: t("mic") },
    { label: "System audio", sub: "What the computer plays (calls, video)", icon: "system", checked: S.opts.loop, keep: true, onclick: t("loop") },
    "-",
    { label: "Keep audio", sub: "Saves the recording on this computer (data folder), e.g. to compare speech models later", icon: "keep", checked: S.opts.keep, keep: true, onclick: t("keep") },
  ];
}

const BAR_W = [0.55, 0.85, 1, 0.75, 0.5];
function meter(key, label) {  // built once; renderStrip only moves the bars
  const m = h("span", { class: "meter", "data-src": key, title: label }, icon(key === "mic" ? "mic" : "system"),
    h("span", { class: "bars" }, BAR_W.map(() => h("i"))));
  return m;
}

function renderStrip() {
  const r = S.status.recorder || {}, st = r.state || "idle";
  const live = st === "recording" || st === "stopping";
  renderJobChip();
  if (!live) return;
  $("timer").textContent = clock(r.elapsed_s);
  const meters = $("meters");
  if (!meters.children.length) meters.append(meter("mic", "Microphone"), meter("loopback", "System audio"));
  const lv = r.levels || {};
  for (const m of meters.children) {
    const v = lv[m.dataset.src];
    m.hidden = !v;
    if (!v) continue;
    m.classList.toggle("active", !!v.active && !r.paused);
    const base = r.paused || st === "stopping" ? 0 : Math.sqrt(Math.max(0, v.level || 0));
    [...m.querySelectorAll(".bars i")].forEach((bar, i) => {
      const jitter = 0.75 + Math.random() * 0.5;
      bar.style.transform = `scaleY(${Math.max(0.12, Math.min(1, base * BAR_W[i] * jitter * 1.6)).toFixed(3)})`;
    });
  }
  const out = [];
  const word = st === "stopping" ? `Finishing${r.backlog ? ` · ${r.backlog} pending` : ""}`
    : r.paused ? "Paused" : r.backlog > 0 ? `Transcribing · ${r.backlog} queued` : "Listening";
  if (st === "stopping") out.push(h("span", { class: "spinner" }));
  if (r.speaker && !r.paused && st === "recording") {
    const s = h("span", { class: "spk", text: r.speaker });
    s.style.setProperty("--c", speakerColor(S.feed.get(r.session) || LIVE_SCOPE, r.speaker));
    out.push(s);
  } else out.push(h("span", { text: word }));
  const rp = recProjectId();
  if (rp && (S.view !== "project" || rp !== S.sel)) {
    out.push(h("span", { text: "·" }), h("button", { class: "link", type: "button", text: "in " + projectName(rp), onclick: () => selectProject(rp) }));
  }
  const statusKey = out.map((n) => n.className + ":" + n.textContent).join("|");
  if ($("live-status").dataset.key !== statusKey) {  // rebuilt every tick, the spinner restarted and stuttered
    $("live-status").dataset.key = statusKey;
    $("live-status").replaceChildren(...out);
  }
  $("live-status").title = word;
}

function renderJobChip() {
  const job = S.status.job, chip = $("jobchip");
  const run = job && job.state === "running";
  chip.hidden = !run;
  if (!run) { chip.replaceChildren(); return; }
  const text = jobText(job);
  if (chip.dataset.text === text) return;
  chip.dataset.text = text;
  chip.title = text;
  chip.replaceChildren(h("i", { class: "spinner" }), h("span", { text }));
}

async function startRecording() {
  if (!S.sel) return;
  remember("steno-opts", JSON.stringify(S.opts));
  const body = { project: S.sel, mic: S.opts.mic, loopback: S.opts.loop, keep_audio: S.opts.keep, auto_title: S.autoTitle };
  const type = S.sel !== "inbox" ? recType(S.sel) : "";
  if (type) body.type = type;
  const title = $("rec-title").value.trim();
  if (title) body.title = title;
  S.stick = true;
  S.meeting = "new"; S.pendingRec = true;
  S.pendingFrom = (S.status.recorder || {}).session || null;  // never "find" the previous recording
  renderSide(); renderFeed();
  const res = await guarded(() => api("POST", "/api/record/start", body));
  if (res) {
    if (res.recorder) S.status.recorder = res.recorder;
    $("rec-title").value = "";
    S.autoTitle = S.autoTitleDefault;  // the switch is per meeting
    renderTop(); renderSide();
    scheduleFeed();
  } else {
    S.pendingRec = false;
    renderSide(); renderFeed();
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
  const list = await fetchList(sel);
  if (my !== feedSeq) return;
  S.meetings = list;
  pickMeeting();
  const visible = list.filter((i) => i.session === S.meeting);
  S.loadingFeed = true; S.buffer = [];
  try {
    await Promise.all(visible.map((info) => ensureMeeting(info).catch((e) => { if (!missing(e)) toast(e.message, "error"); })));
  } finally { S.loadingFeed = false; }
  if (my !== feedSeq) return;
  const keep = new Set(visible.map((i) => i.session));
  for (const k of [...S.feed.keys()]) if (!keep.has(k)) S.feed.delete(k);
  for (const ev of S.buffer) { const m = S.feed.get(ev.session); if (m && m.detail) appendSegment(m, ev.segment, true); }
  S.buffer = [];
  renderSide();
  renderTop();  // the header and the dock's subfolder follow the meeting that just loaded
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
  const changed = feed.dataset.meeting !== String(S.meeting);
  feed.dataset.meeting = String(S.meeting);
  feed.replaceChildren();
  if (!S.sel) { feed.append(h("p", { class: "hint", text: "Select or create a project in the sidebar." })); return; }
  if (S.meeting === "new") {
    feed.append(newCard());
    renderJump();
    return;
  }
  const m = S.feed.get(S.meeting);
  if (!m || !m.detail) {
    feed.append(h("p", { class: "hint" }, h("span", { class: "spinner" })));
    renderJump();
    return;
  }
  const el = buildMeeting(m);
  if (changed) el.classList.add("enter");
  feed.append(el);
  if (changed) S.stick = !!m.info.recording;
  if (S.stick) scrollToEnd(); else feed.scrollTop = changed ? 0 : prevTop;
  updateLive(); renderJump();
}

function newCard() {
  const type = S.sel !== "inbox" ? recType(S.sel) : "";
  const where = projectName(S.sel) + (type ? " · " + type : "");
  return h("div", { class: "newcard" },
    h("div", { class: "newicon" }, S.pendingRec ? h("span", { class: "spinner big" }) : icon("mic")),
    h("h2", { text: S.pendingRec ? "Starting the recording" : `New meeting in ${where}` }),
    h("p", { text: S.pendingRec ? "Loading the speech models. This takes a few seconds the first time."
      : "Add a title below if you like, then press Record. Transcription runs on this computer." }));
}

function jobLabel(job) {
  return { summary: "Summarizing", todo: "Extracting to-dos", clean: "Cleaning up", finalize: "Finalizing", auto: "After meeting", title: "Writing a title" }[job.kind] || String(job.kind);
}
function jobText(job) {
  const stage = job.stage && job.stage !== "waiting for Claude" ? " · " + job.stage : "";
  return jobLabel(job) + stage + (job.total ? ` ${job.done}/${job.total}` : "");
}

// what a job is called in toasts (job.kind is the server's internal name)
const JOB_NAMES = { summary: "Summary", todo: "To-do", clean: "Clean up", finalize: "Speakers", auto: "After-meeting steps", title: "Title" };

const busyJob = () => !!(S.status.job && S.status.job.state === "running");

// ---------------------------------------------------------------- copy to clipboard

async function copyText(text, what, btn) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; }
  catch (e) {  // older WebView or no permission: the textarea route still works
    const ta = h("textarea", { class: "offscreen", "aria-hidden": "true" });
    ta.value = text;
    document.body.append(ta); ta.focus(); ta.select();
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    ta.remove();
  }
  if (!ok) { toast(`Could not copy the ${what.toLowerCase()}`, "error"); return; }
  toast(`${what} copied`);
  if (btn) {
    btn.classList.add("copied");
    btn.replaceChildren(icon("check"));
    setTimeout(() => { btn.classList.remove("copied"); btn.replaceChildren(icon("copy")); }, 1400);
  }
}

function copyButton(label, getText, what) {
  const b = h("button", { class: "icon-btn small copy", type: "button", title: label, "aria-label": label }, icon("copy"));
  b.addEventListener("click", async (ev) => {
    ev.preventDefault(); ev.stopPropagation();  // inside <summary>: copy, do not fold
    const text = await guarded(getText);
    if (text) copyText(text, what, b);
  });
  return b;
}

function transcriptText(title, startedAt, segments) {
  const out = [`# ${title || "Untitled meeting"}`, niceDate(startedAt), ""];
  let prev = null;
  for (const s of segments) {
    const turn = !prev || prev.speaker !== s.speaker || prev.speaker_id !== s.speaker_id;
    if (turn) { if (prev) out.push(""); out.push(`${s.speaker || "Speaker"} (${clock(s.t_start)})`); }
    out.push(s.text);
    prev = s;
  }
  return out.join("\n").trim() + "\n";
}

async function meetingTranscript(info) {
  const m = S.feed.get(info.session);
  const detail = m && m.detail ? m.detail : await api("GET", `/api/sessions/${encodeURIComponent(info.session)}`);
  const segs = detail.segments || [];
  if (!segs.length) throw new Error("This meeting has no transcript lines yet.");
  return transcriptText(titleOf(info), info.started_at, segs);
}

// ---------------------------------------------------------------- meeting actions (⋯ in the sidebar and on the meeting)

function findMeeting(sid) {
  for (const [pid, list] of S.lists) { const info = list.find((i) => i.session === sid); if (info) return { pid, info }; }
  const m = S.feed.get(sid);
  return m ? { pid: S.sel, info: m.info } : null;
}

function meetingItems(info, pid, titleBtn) {
  const sid = info.session;
  const live = info.recording || ((S.status.recorder || {}).session === sid && (S.status.recorder || {}).state !== "idle");
  const job = S.status.job && S.status.job.state === "running" && S.status.job.session === sid;
  const items = [];
  if (pickerAvailable()) {  // an inbox meeting opens its session folder in the data dir
    items.push({ label: "Open in File Explorer", icon: "folderOpen", onclick: () => guarded(() => openInExplorer("meeting", sid)) });
  }
  items.push(
    { label: "Copy transcript", icon: "copy", onclick: () => guarded(async () => copyText(await meetingTranscript(info), "Transcript")) },
    { label: "Rename", icon: "pencil", onclick: () => (titleBtn && titleBtn.isConnected ? renameMeeting(S.feed.get(sid), titleBtn) : guarded(() => renameByDialog(info))) },
    { label: "Move...", sub: "Other project or subfolder", icon: "move", disabled: live || !S.projects.length, title: live ? "Stop the recording first" : null, onclick: () => guarded(() => moveMeeting(info, pid)) },
    "-",
    info.archived
      ? { label: "Unarchive", sub: "Show it in the list again", icon: "unarchive", onclick: () => guarded(() => archiveMeeting(info, pid, false)) }
      : { label: "Archive", sub: "Hide it in the app; files stay", icon: "archive", disabled: live, title: live ? "Stop the recording first" : null, onclick: () => guarded(() => archiveMeeting(info, pid, true)) },
    { label: "Delete...", icon: "trash", danger: true, disabled: live || job, title: live ? "Stop the recording first" : job ? "A job is running on it" : null,
      onclick: () => guarded(() => deleteMeeting(info, pid)) });
  return items;
}

function meetingMenu(m, titleBtn) { return meetingItems(m.info, S.sel, titleBtn); }

async function renameByDialog(info) {
  const v = await formModal({ title: "Rename meeting", fields: [{ key: "title", label: "Title", value: titleOf(info), placeholder: "Meeting title" }], ok: "Rename" });
  if (!v || !v.title || v.title === info.title) return;
  await api("POST", `/api/meetings/${encodeURIComponent(info.session)}/rename`, { title: v.title });
  for (const list of S.lists.values()) for (const i of list) if (i.session === info.session) i.title = v.title;
  const m = S.feed.get(info.session);
  if (m) { m.info.title = v.title; if (m.el) m.el.replaceWith(buildMeeting(m)); }
  renderSide();
}

async function archiveMeeting(info, pid, archived) {
  await api("POST", `/api/meetings/${encodeURIComponent(info.session)}/archive`, { archived });
  info.archived = archived;
  toast(archived ? "Meeting archived. Find it under Archived in the project." : "Meeting unarchived");
  if (archived && S.sel === pid && S.meeting === info.session) S.meeting = null;
  if (archived && !S.showArchived.has(pid)) renderSide();
  await loadProjects();
  if (S.sel === pid) await loadFeed();
}

async function deleteMeeting(info, pid) {
  const name = titleOf(info) || "Untitled meeting";
  const ok = await formModal({ title: "Delete meeting",
    text: `Delete "${name}" (${niceDate(info.started_at)})? Its transcript, summary, to-do, voice data and any kept audio are removed from this computer and from the project folder. This cannot be undone.`,
    ok: "Delete", danger: true });
  if (!ok) return;
  await api("DELETE", `/api/meetings/${encodeURIComponent(info.session)}`);
  S.feed.delete(info.session);
  for (const list of S.lists.values()) { const i = list.findIndex((x) => x.session === info.session); if (i >= 0) list.splice(i, 1); }
  if (S.meeting === info.session) S.meeting = null;
  toast(`Deleted "${name}"`);
  await loadProjects();
  if (S.sel === pid || S.meeting === null) await loadFeed();
}

function buildMeeting(m) {
  const info = m.info, sid = info.session;
  m.rows = new Map();
  const titleBtn = h("button", { class: "mtitle" + (titleOf(info) ? "" : " untitled"), type: "button", title: "Click to rename", text: titleOf(info) || "Untitled meeting", onclick: () => renameMeeting(m, titleBtn) });
  m.durEl = h("span", { class: "mdur" });
  const more = h("button", { class: "icon-btn", type: "button", "aria-label": "Meeting actions", title: "Meeting actions", "aria-haspopup": "menu" }, icon("more"));
  more.addEventListener("click", () => showMenu(more, () => meetingMenu(m, titleBtn)));
  m.btns = {};
  const act = (kind, label, ic, tip) => {
    const b = h("button", { class: "act", type: "button", title: tip, "data-tip": tip }, icon(ic), h("span", { text: label }));
    b.addEventListener("click", () => startJob(m, kind));
    return (m.btns[kind] = b);
  };
  const bar = h("div", { class: "mbar" },
    act("summary", m.docs.summary ? "Redo summary" : "Summary", "spark", m.docs.summary ? "Summarize again with Claude (sends transcript text only)" : "Summarize with Claude (sends transcript text only)"),
    act("todo", m.docs.todo ? "Redo to-do" : "To-do", "todo", m.docs.todo ? "Extract to-dos again with Claude" : "Extract explicit commitments with Claude"),
    act("clean", "Clean up", "wand", "Fix misheard words and punctuation with Claude"),
    // only for an untitled meeting: Claude names it from what was said (same as the automatic title)
    titleOf(info) ? null : act("title", "Auto title", "heading", "Let Claude name this meeting from what was said (sends transcript text only)"));
  m.jobEl = h("div", { class: "mjob", hidden: true });
  m.docsEl = h("div", { class: "docs" });
  m.linesEl = h("div", { class: "lines" });
  const linesHead = h("div", { class: "lines-head" },
    h("span", { text: "Transcript" }),
    copyButton("Copy transcript", () => meetingTranscript(m.info), "Transcript"));
  m.el = h("section", { class: "meeting", "data-session": sid },
    h("header", { class: "mhead" },
      h("div", { class: "mhead-text" }, titleBtn,
        h("div", { class: "mmeta" }, h("span", { text: niceDate(info.started_at) }), m.durEl,
          info.archived ? h("button", { class: "badge-arch", type: "button", title: "Archived: hidden in the list. Click to unarchive.",
            onclick: () => guarded(() => archiveMeeting(info, S.sel, false)) }, icon("archive"), "Archived") : null)),
      more),
    bar, m.jobEl, m.docsEl, linesHead, m.linesEl);
  renderDocs(m);
  renderLines(m);
  updateJobLines(); updateButtons(); updateLive();
  return m.el;
}

function renderLines(m) {
  m.linesEl.replaceChildren(); m.rows = new Map(); m.spk = new Map();  // colours: by first appearance in this transcript
  const segs = m.detail.segments;
  const frag = document.createDocumentFragment();
  segs.forEach((s, i) => frag.append(segRow(m, s, segs[i - 1])));
  m.linesEl.append(frag);
  syncEmptyNote(m);
}

function syncEmptyNote(m) {
  const note = m.linesEl.querySelector(".empty-meeting");
  if (m.detail.segments.length) { if (note) note.remove(); return; }
  if (!note) {
    m.linesEl.append(m.info.recording
      ? h("div", { class: "empty-meeting" }, h("span", { class: "spinner" }), "Listening. Lines appear a few seconds after someone speaks.")
      : h("div", { class: "empty-meeting", text: "Nothing was transcribed. Check the audio sources (the microphone icon next to Record) and try again." }));
  }
}

function canName(m, seg) {
  const n = clusterNo(seg.speaker_id);
  if (n === null) return false;
  const sp = m.detail.speakers.find((x) => x.id === seg.speaker_id && x.name === seg.speaker);
  return sp ? !!sp.can_name : true;
}

function segRow(m, seg, prev) {
  const first = !prev || prev.speaker !== seg.speaker || prev.speaker_id !== seg.speaker_id;
  const n = clusterNo(seg.speaker_id);
  const unknown = n !== null && seg.speaker === `Speaker ${n}`;
  const nameable = first && canName(m, seg);
  const who = first && seg.speaker ? h("button", {
    class: "who" + (unknown ? " unknown" : "") + (nameable ? " nameable" : ""), type: "button",
    title: nameable ? "Name this speaker" : null }, seg.speaker,
    // a live guess that may still change (text stays the same colour: the words are not in doubt)
    seg.provisional && !unknown ? h("span", { class: "unsure", title: "Speaker not certain yet: it can change after the meeting", text: "?" }) : null,
    unknown && nameable ? h("span", { class: "q", text: "Name?" }) : null) : null;
  const p = h("p", { text: seg.text, title: first ? null : clock(seg.t_start) });
  const row = h("div", { class: "seg" + (seg.provisional ? " prov" : "") + (first ? " first" : ""), "data-seq": seg.seq });
  if (first) {
    const color = speakerColor(m, seg.speaker);
    row.style.setProperty("--c", color);
    row.append(h("span", { class: "avatar", "aria-hidden": "true", text: initials(seg.speaker) }),
      h("div", { class: "seg-head" }, who, h("time", { class: "time", text: clock(seg.t_start) })));
  }
  row.append(p);
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
  const row = segRow(m, seg, prev);
  if (!silent) row.classList.add("enter");
  m.linesEl.append(row);
  syncEmptyNote(m);
  if (silent) return;
  if (S.stick) scrollToEnd(); else { S.unseen += 1; renderJump(); }
}

function reviseSegment(m, seq, text) {
  const seg = m.detail.segments.find((x) => x.seq === seq);
  if (!seg || seg.text === text) return;
  seg.text = text;
  const p = m.rows && m.rows.get(seq);
  if (!p) return;
  p.textContent = text;
  p.classList.remove("revised");
  void p.offsetWidth;  // restart the highlight
  p.classList.add("revised");
}

async function refreshMeeting(session, openKind) {
  const m = S.feed.get(session);
  if (!m || !m.el) return;
  await fetchDetail(m);
  if (m.detail.title) m.info.title = m.detail.title;  // e.g. a title Claude just wrote
  if (openKind && m.docs[openKind]) m.open[openKind] = true;
  m.el.replaceWith(buildMeeting(m));
  if (S.stick) scrollToEnd();
}

function updateLive() {
  const r = S.status.recorder || {};
  for (const m of S.feed.values()) {
    if (!m.durEl) continue;
    const live = m.info.recording || (r.session === m.info.session && r.state !== "idle");
    if (live) {
      const paused = r.session === m.info.session && r.paused;
      if (!m.durEl.querySelector(".badge-live")) {
        m.durEl.replaceChildren(h("span", { class: "badge-live" }, h("i", { class: "live-dot" }), h("span", { class: "bl-text" })));
      }
      m.durEl.querySelector(".bl-text").textContent = paused ? "PAUSED" : "LIVE";
      m.durEl.querySelector(".live-dot").hidden = paused;
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
    if (!mine) { m.jobEl.replaceChildren(); continue; }
    const frac = job.total ? Math.max(0, Math.min(1, job.done / job.total)) : null;
    if (!m.jobEl.firstChild) {
      m.jobEl.append(h("div", { class: "mjob-row" }, h("span", { class: "spinner" }), h("b"), h("span", { class: "mjob-stage" })),
        h("div", { class: "bar" }, h("i")));
    }
    m.jobEl.querySelector("b").textContent = jobLabel(job);
    m.jobEl.querySelector(".mjob-stage").textContent = (job.stage || "") + (job.total ? ` · ${job.done}/${job.total}` : "");
    const bar = m.jobEl.querySelector(".bar");
    bar.classList.toggle("indet", frac === null);
    bar.firstChild.style.transform = frac === null ? "" : `scaleX(${frac})`;
  }
}

function updateButtons() {
  const busy = busyJob(), off = aiOff(), r = S.status.recorder || {};
  for (const m of S.feed.values()) {
    const live = m.info.recording || (r.session === m.info.session && r.state && r.state !== "idle");
    const empty = !(m.detail && m.detail.segments && m.detail.segments.length);
    for (const [kind, b] of Object.entries(m.btns || {})) {
      // why the button cannot be pressed right now, if it cannot (shown as its tooltip)
      const why = busy ? "Wait: another job is running"
        : live && kind !== "summary" && kind !== "todo" ? "Available when the meeting has ended"
        : empty ? "Available once the meeting has transcript lines" : "";
      b.disabled = !!why;
      b.classList.toggle("ai-off", off);  // stays visible; a click explains and offers the login
      b.title = why || (off ? `${AI_OFF_TEXT[S.llm.state]}. Click for options.` : b.dataset.tip);
    }
  }
}

async function startJob(m, kind) {
  if (aiOff()) { showMenu(m.btns[kind], llmMenuItems); return; }
  const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(m.info.session)}/jobs`, { kind }));
  if (res && res.job) { S.status.job = res.job; updateJobLines(); updateButtons(); renderStrip(); }
}

function renderDocs(m) {
  m.docsEl.replaceChildren();
  for (const kind of ["summary", "todo"]) {
    const md = m.docs[kind];
    if (!md) continue;
    const label = kind === "todo" ? "To-do" : "Summary";
    const det = h("details", { class: "doc" },
      h("summary", null, icon(kind === "todo" ? "todo" : "spark"), h("span", { class: "doc-title", text: label }),
        copyButton(`Copy ${label.toLowerCase()}`, async () => md, label), icon("chevron", "chev")),
      h("div", { class: "md" }, renderMarkdown(md)));
    det.open = !!m.open[kind];
    det.addEventListener("toggle", () => { m.open[kind] = det.open; });
    m.docsEl.append(det);
  }
}

function renameMeeting(m, btn) {
  if (!btn.isConnected) return;
  const input = h("input", { class: "text mtitle-in", type: "text", value: titleOf(m.info), placeholder: "Meeting title", maxlength: "80", "aria-label": "Meeting title" });
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const title = input.value.trim();
    if (save && title && title !== m.info.title) {
      const res = await guarded(() => api("POST", `/api/meetings/${encodeURIComponent(m.info.session)}/rename`, { title }));
      if (res) {
        m.info.title = title; btn.textContent = title; btn.classList.remove("untitled");
        if (m.btns.title) { m.btns.title.remove(); delete m.btns.title; }  // named: no Auto title any more
        for (const list of S.lists.values()) for (const i of list) if (i.session === m.info.session) i.title = title;
        renderSide();
      }
    }
    input.replaceWith(btn);
  };
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") finish(true); else if (ev.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
  btn.replaceWith(input);
  input.focus(); input.select();
}

async function moveMeeting(info, current) {
  const options = [];
  for (const p of S.projects) {
    const types = p.types || [];
    options.push({ value: p.id, label: types.length ? `${p.name} (no subfolder)` : p.name });
    for (const t of types) options.push({ value: `${p.id}|${t.name}`, label: `${p.name} › ${t.name}` });
  }
  options.push({ value: "inbox", label: INBOX_NAME });
  const here = current + (info.type ? "|" + info.type : "");
  const v = await formModal({ title: "Move meeting", text: "The meeting folder is moved along: into the subfolder, into Steno/ at the project root, or out of the project into the Inbox / TBD.", fields: [{ key: "to", label: "Move to", options, value: here }], ok: "Move" });
  if (!v || v.to === here) return;
  const cut = v.to.indexOf("|");
  const pid = cut < 0 ? v.to : v.to.slice(0, cut), type = cut < 0 ? null : v.to.slice(cut + 1);
  await moveMeetingTo(info, current, pid, type);
}

/* Move a meeting (dialog or drag): pid is a project id or "inbox", type a subfolder name or null.
   Keeps the meeting open if it was open, and refreshes the lists. */
async function moveMeetingTo(info, current, pid, type) {
  await api("POST", `/api/meetings/${encodeURIComponent(info.session)}/move`, { project: pid, type });
  toast(`Moved to ${projectName(pid)}${type ? " › " + type : ""}`);
  if (S.feed.has(info.session)) S.feed.get(info.session).info.type = type;
  const open = S.sel === current && S.meeting === info.session;
  if (!S.expanded.has(pid)) { S.expanded.add(pid); saveExpanded(); }
  await loadProjects();
  const list = await fetchList(pid);  // fresh, so the moved meeting is found when selecting it
  if (S.sel === pid) S.meetings = list;
  if (open) selectMeeting(pid, info.session); else if (S.sel === current) await loadFeed();
}

// ---------------------------------------------------------------- drag to move
//
// Press and hold a meeting in the sidebar (~350 ms, pointer events) to pick it up; a plain click
// still opens it, and moving more than a few pixels before the hold is over cancels it (that is
// a scroll or a click). Picked up, the row stays behind as a grey hole and a grey, translucent
// copy of it follows the pointer. The place under the pointer (a project, a subfolder, the Inbox /
// TBD: the nearest [data-drop] ancestor) is highlighted if the meeting can go there, and its
// meetings slide apart to open a gap where the meeting's date puts it (newest first). Release
// moves it (and its folder on disk): the copy settles into the gap. Escape or a release anywhere
// else cancels: the copy flies back to the hole. Same place = nothing happens.

const HOLD_MS = 350, HOLD_SLOP = 6, EDGE_PX = 40, EXPAND_MS = 700, MOVE_MS = 150;
const reducedMotion = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function armDrag(el, info, pid) {
  el.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || DRAG.press || DRAG.active) return;
    DRAG.press = { info, pid, el, id: ev.pointerId, x: ev.clientX, y: ev.clientY,
      timer: setTimeout(() => beginDrag(), HOLD_MS) };
  });
}

function cancelPress() {
  if (DRAG.press) clearTimeout(DRAG.press.timer);
  DRAG.press = null;
}

function beginDrag() {
  const p = DRAG.press;
  DRAG.press = null;
  if (!p || !p.el.isConnected) return;
  const r = S.status.recorder || {};
  if (p.info.recording || (r.session === p.info.session && r.state && r.state !== "idle" && r.state !== "error")) {
    toast("Stop the recording before moving it.", "error");
    return;
  }
  closeMenu();
  const box = (p.el.closest(".mrow") || p.el).getBoundingClientRect();
  const copy = p.el.cloneNode(true);  // the row itself, without its listeners
  copy.classList.remove("active");
  copy.removeAttribute("title");
  copy.tabIndex = -1;
  const ghost = h("div", { class: "drag-ghost", "aria-hidden": "true" }, copy);
  ghost.style.width = `${Math.round(box.width)}px`;
  document.body.append(ghost);
  document.body.classList.add("dragging");
  // capture on the (permanent) sidebar: a release outside the window still ends the drag
  try { $("side").setPointerCapture(p.id); } catch (e) { /* no capture: events still reach the document */ }
  DRAG.active = { info: p.info, from: p.pid, fromType: p.info.type || null, id: p.id, x: p.x, y: p.y,
    sx: p.x, sy: p.y, ox: p.x - box.left, oy: p.y - box.top, rowH: box.height,
    moved: false, ghost, target: null, key: "", expandTimer: null, raf: 0, gap: null, opened: [], landing: false, settled: false };
  placeGhost();
  renderSide();  // the source row turns into the hole
  DRAG.active.raf = requestAnimationFrame(dragTick);
}

function placeGhost() {
  const d = DRAG.active;
  d.ghost.style.transform = `translate(${Math.round(d.x - d.ox)}px, ${Math.round(d.y - d.oy)}px)`;
}

/* A place the meeting can be dropped on, from the pointer position: {pid, type, el} or null. */
function dropTargetAt(x, y) {
  const at = document.elementFromPoint(x, y);
  const el = at && at.closest("#side [data-drop]");
  return el ? { el, pid: el.dataset.drop, type: el.dataset.dropType || null } : null;
}

function canDrop(d, t) {
  return !!t && !(t.pid === d.from && (t.type || null) === d.fromType);
}

/* The node of a place in the sidebar (project or subfolder), found again after a re-render. */
function dropNode(t) {
  for (const el of document.querySelectorAll("#side [data-drop]")) {
    if (el.dataset.drop === t.pid && (el.dataset.dropType || null) === (t.type || null)) return el;
  }
  return null;
}

function paintDrop() {  // after every sidebar render, and when the target changes
  const d = DRAG.active;
  for (const el of document.querySelectorAll("#side .drop-target")) el.classList.remove("drop-target");
  if (!d || !d.target) return;
  const node = dropNode(d.target);
  const row = node && node.querySelector(":scope > .prow, :scope > .trow");
  if (row) row.classList.add("drop-target");
}

/* Where the meeting would land in the target's list: its date puts it among the meetings there
   (newest first). Returns {box, before} (insert before `before`, null = at the end) or null while
   the place is collapsed. */
function gapPlace(d) {
  const node = d.target && dropNode(d.target);
  const box = node && node.querySelector(d.target.type ? ":scope > .tlist" : ":scope > .mlist");
  if (!box) return null;
  const ts = Date.parse(d.info.started_at) || 0;
  let before = null, last = null;
  for (const el of box.children) {
    if (!el.classList.contains("mrow") || el.classList.contains("archived") || el.classList.contains("dragging")) continue;
    last = el;
    if (!before && (Number(el.dataset.ts) || 0) < ts) before = el;
  }
  if (!before) before = last ? last.nextElementSibling : box.querySelector(":scope > .mitem.more, :scope > .mitem.none, :scope > .arch-toggle");
  while (before && before.classList.contains("drop-gap")) before = before.nextElementSibling;
  return { box, before };
}

function closeGap(gap, animate) {
  if (!gap || !gap.el.isConnected) return;
  const { el } = gap;
  if (!animate || reducedMotion()) { el.remove(); return; }
  el.classList.remove("open");
  el.style.height = "0px";
  setTimeout(() => el.remove(), MOVE_MS + 20);
}

/* Open the gap at the landing place (and close the old one): the meetings below slide down. */
function syncGap(animate) {
  const d = DRAG.active;
  if (!d) return;
  const place = d.target ? gapPlace(d) : null;
  const cur = d.gap;
  if (cur && cur.el.isConnected && place && cur.box === place.box && cur.el.nextElementSibling === place.before) return;
  closeGap(cur, animate);
  d.gap = null;
  if (!place) return;
  const el = h("div", { class: "drop-gap", "aria-hidden": "true" });
  el.style.height = "0px";
  place.box.insertBefore(el, place.before);
  d.gap = { el, box: place.box };
  const open = () => { el.classList.add("open"); el.style.height = `${Math.round(d.rowH)}px`; };
  if (animate && !reducedMotion()) { void el.offsetHeight; open(); } else { el.style.transition = "none"; open(); }
}

const collapsedTarget = (t) => (t.type ? S.folded.has(`${t.pid}::${t.type}`) : !S.expanded.has(t.pid));

function updateDropTarget() {
  const d = DRAG.active;
  if (!d || d.landing) return;
  const t = dropTargetAt(d.x, d.y);
  const ok = canDrop(d, t);
  const key = ok ? t.pid + "\u0000" + (t.type || "") : "";
  if (key === d.key) return;
  d.key = key;
  d.target = ok ? t : null;
  clearTimeout(d.expandTimer);
  // hovering a closed project or subfolder for a moment opens it, so the gap has a list to open in
  if (ok && collapsedTarget(t)) {
    d.expandTimer = setTimeout(() => {
      if (!DRAG.active || DRAG.active.key !== key) return;
      if (t.type) { S.folded.delete(`${t.pid}::${t.type}`); d.opened.push({ type: `${t.pid}::${t.type}` }); }
      else { S.expanded.add(t.pid); d.opened.push({ pid: t.pid }); }  // not remembered: only opened for this drag
      renderSide();
      if (!S.lists.has(t.pid)) guarded(() => fetchList(t.pid).then(renderSide));
    }, EXPAND_MS);
  }
  paintDrop();
  syncGap(true);
}

function dragTick() {  // scrolls the sidebar while the pointer rests near its top or bottom edge
  const d = DRAG.active;
  if (!d || d.landing) return;
  const side = $("side"), box = side.getBoundingClientRect();
  let dy = 0;
  if (d.y < box.top + EDGE_PX) dy = -Math.ceil((box.top + EDGE_PX - d.y) / 4);
  else if (d.y > box.bottom - EDGE_PX) dy = Math.ceil((d.y - (box.bottom - EDGE_PX)) / 4);
  if (dy && d.x >= box.left && d.x <= box.right) {
    const before = side.scrollTop;
    side.scrollTop += Math.max(-18, Math.min(18, dy));
    if (side.scrollTop !== before) updateDropTarget();
  }
  d.raf = requestAnimationFrame(dragTick);
}

/* The copy settles at `rect` (the gap, the hole or the target row); `fade`: it melts away there. */
function flyTo(d, rect, fade) {
  if (reducedMotion() || !rect) return false;
  d.ghost.classList.add("landing");
  d.ghost.style.width = `${Math.round(rect.width)}px`;
  d.ghost.style.transform = `translate(${Math.round(rect.left)}px, ${Math.round(rect.top)}px)`;
  if (fade) d.ghost.style.opacity = "0";
  return true;
}

function endDrag(d) {
  d.ghost.remove();
  closeGap(d.gap, false);
  document.body.classList.remove("dragging");
  DRAG.active = null;
  renderSide();
}

async function finishDrag(drop) {
  const d = DRAG.active;
  if (!d || d.landing) return;
  d.landing = true;
  cancelAnimationFrame(d.raf);
  clearTimeout(d.expandTimer);
  try { $("side").releasePointerCapture(d.id); } catch (e) { /* not captured */ }
  DRAG.suppress = d.moved;  // the release would otherwise "click" a row; held but not moved: a click
  setTimeout(() => { DRAG.suppress = false; }, 120);
  const target = drop ? d.target : null;
  try {
    if (!target) {  // cancelled: the gap closes, the copy flies back to the hole
      d.target = null; d.key = "";
      paintDrop(); syncGap(true);
      const hole = document.querySelector("#side .mrow.dragging");
      if (flyTo(d, hole && hole.getBoundingClientRect(), !hole)) await sleep(MOVE_MS + 20);
      for (const o of d.opened) { if (o.type) S.folded.add(o.type); else S.expanded.delete(o.pid); }
      return;
    }
    const gap = d.gap && d.gap.el.isConnected ? d.gap.el : null;
    const node = dropNode(target);
    const row = node && node.querySelector(":scope > .prow, :scope > .trow");
    const dest = gap || row;
    if (flyTo(d, dest && dest.getBoundingClientRect(), !gap)) await sleep(MOVE_MS + 20);
    d.settled = true;  // from here the lists are refreshed from the server: no more gap painting
    await guarded(() => moveMeetingTo(d.info, d.from, target.pid, target.type));
  } finally {
    endDrag(d);
  }
}

function initDrag() {
  document.addEventListener("pointermove", (ev) => {
    const p = DRAG.press, d = DRAG.active;
    if (p && ev.pointerId === p.id && Math.hypot(ev.clientX - p.x, ev.clientY - p.y) > HOLD_SLOP) cancelPress();
    if (d && ev.pointerId === d.id && !d.landing) {
      d.x = ev.clientX; d.y = ev.clientY;
      if (Math.hypot(d.x - d.sx, d.y - d.sy) > HOLD_SLOP) d.moved = true;
      placeGhost();
      updateDropTarget();
      ev.preventDefault();
    }
  });
  document.addEventListener("pointerup", (ev) => {
    if (DRAG.press && ev.pointerId === DRAG.press.id) cancelPress();  // a click: it opens the meeting
    else if (DRAG.active && ev.pointerId === DRAG.active.id) finishDrag(true);
  });
  document.addEventListener("pointercancel", (ev) => {
    if (DRAG.press && ev.pointerId === DRAG.press.id) cancelPress();
    else if (DRAG.active && ev.pointerId === DRAG.active.id) finishDrag(false);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || !(DRAG.press || DRAG.active)) return;
    ev.stopImmediatePropagation(); ev.preventDefault();
    if (DRAG.active) finishDrag(false); else cancelPress();
  }, true);
  window.addEventListener("blur", () => { cancelPress(); if (DRAG.active) finishDrag(false); });
  document.addEventListener("click", (ev) => {
    if (!DRAG.suppress) return;
    DRAG.suppress = false;
    ev.preventDefault(); ev.stopPropagation();
  }, true);
  for (const type of ["contextmenu", "selectstart", "dragstart"]) {
    document.addEventListener(type, (ev) => { if (DRAG.press || DRAG.active) ev.preventDefault(); });
  }
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
    const body = { speaker: seg.speaker_id, name, save_profile: save.checked };
    if (m.detail.speaker_space === "final") body.view = "final";  // finalize runs again afterwards
    const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(m.info.session)}/name`, body));
    if (res) { toast(res.message || "Speaker named"); await guarded(() => refreshMeeting(m.info.session)); }
  } }, input,
  h("label", { title: "Store a voice profile on this computer so future meetings recognise this person" }, save, "Remember voice"),
  h("button", { class: "btn small primary", type: "submit", text: "Save" }),
  h("button", { class: "btn small ghost", type: "button", text: "Cancel", onclick: () => form.remove() }));
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
  const text = S.unseen ? `${S.unseen} new line${S.unseen === 1 ? "" : "s"}` : recording ? "Jump to live" : "Latest";
  if (j.dataset.text !== text) { j.dataset.text = text; j.replaceChildren(icon("down"), text); }
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
  const page = h("div", { class: "page" },
    h("div", { class: "people-head" },
      h("p", { class: "set-hint", text: "Voice profiles are stored encrypted on this computer. Steno uses them to recognise people in new meetings." }),
      h("button", { class: "btn primary", type: "button", onclick: () => openEnroll(null) }, icon("plus"), "Add person")));
  box.replaceChildren(page);
  if (!S.people.length) page.append(h("p", { class: "hint", text: "No voice profiles yet. Choose \"Add person\" to record a voice sample, or name a speaker in a meeting and choose \"Remember voice\"." }));
  else page.append(h("div", { class: "group" }, S.people.map(personRow)));
  renderSide();
}

function personRow(p, i) {
  const row = h("div", { class: "person" });
  row.style.setProperty("--c", `var(--spk-${i % SPEAKER_HUES + 1})`);  // by list position: neighbours differ
  const show = () => row.replaceChildren(
    h("span", { class: "avatar", "aria-hidden": "true", text: initials(p.name) }),
    h("div", { class: "pn" }, h("div", { text: p.name }), h("div", { class: "sub", text: `${p.embeddings} voice sample${p.embeddings === 1 ? "" : "s"} · updated ${niceDate(p.updated)}` })),
    h("button", { class: "btn small", type: "button", title: "Record more of this person's voice", onclick: () => openEnroll(p) }, icon("mic"), "Add voice"),
    h("button", { class: "icon-btn", type: "button", title: "Rename person", "aria-label": "Rename person", onclick: edit }, icon("pencil")),
    h("button", { class: "icon-btn", type: "button", title: "Delete person", "aria-label": "Delete person", onclick: () => removePerson(p) }, icon("trash")));
  const edit = () => {
    const input = h("input", { class: "text", type: "text", value: p.name, maxlength: "80", "aria-label": "New name" });
    row.replaceChildren(h("form", { onsubmit: async (ev) => {
      ev.preventDefault();
      const res = await guarded(() => api("POST", `/api/people/${p.id}/rename`, { name: input.value }));
      if (res) { toast(res.message); await guarded(loadPeople); }
    } }, input, h("button", { class: "btn small primary", type: "submit", text: "Save" }),
    h("button", { class: "btn small ghost", type: "button", text: "Cancel", onclick: show })));
    input.focus(); input.select();
  };
  show();
  return row;
}

async function removePerson(p) {
  const ok = await formModal({ title: "Delete person", text: `Delete ${p.name} and their voice data? Old transcripts keep the name as plain text. This cannot be undone.`, ok: "Delete", danger: true });
  if (!ok) return;
  const res = await guarded(() => api("DELETE", `/api/people/${p.id}`));
  if (res) { toast(res.message); await guarded(loadPeople); }
}

// ---- voice sample from the microphone: new person or more voice for an existing one

const ENROLL_TEXT = "Jag brukar börja dagen med en kopp kaffe och en promenad längs vattnet. " +
  "På vårens första varma dag sitter många ute och läser en bok i solen. " +
  "Bussen till centrum går varje kvart, men ibland är det snabbare att cykla. " +
  "I köket luktar det nybakat bröd, och någon har dukat fram ost och sylt. " +
  "Efter middagen brukar vi sitta en stund och prata om vad som hände under dagen.";
const ENROLL_MIN_S = 10;      // shorter recordings are dropped when finished early
const ENROLL_GOOD_S = 30;
let enrollUi = null;          // the open dialog: { dlg, name, existing, seconds, live, phase, el }

function openEnroll(person) {
  const dlg = $("modal");
  const ui = { dlg, name: person ? person.name : "", existing: !!person, seconds: ENROLL_GOOD_S, live: false, phase: "", el: null };
  const noEscape = (ev) => { if (ui.live) ev.preventDefault(); };  // Escape must not drop a running recording
  dlg.addEventListener("cancel", noEscape);
  dlg.addEventListener("close", () => { dlg.removeEventListener("cancel", noEscape); if (enrollUi === ui) enrollUi = null; }, { once: true });
  enrollUi = ui;
  enrollForm(ui);
  dlg.showModal();
}

function enrollForm(ui, error) {
  ui.live = false; ui.phase = "form"; ui.el = null;
  const name = h("input", { class: "text", type: "text", value: ui.name, maxlength: "80", autocomplete: "off", placeholder: "Name", "aria-label": "Name", readonly: ui.existing });
  const err = h("p", { class: "en-err", role: "alert", text: error || "", hidden: !error });
  const showErr = (msg) => { err.textContent = msg; err.hidden = false; };
  const lens = [ENROLL_GOOD_S, 60].map((s) => h("button", { type: "button", class: "btn small en-len", text: `${s} s`, onclick: () => pick(s) }));
  const pick = (s) => {
    ui.seconds = s;
    lens.forEach((b, i) => { const on = [ENROLL_GOOD_S, 60][i] === s; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
  };
  pick(ui.seconds);
  const start = h("button", { class: "btn primary", type: "submit" }, icon("mic"), "Start recording");
  const form = h("form", { class: "mform", onsubmit: async (ev) => {
    ev.preventDefault();
    const nm = name.value.trim();
    if (!nm) { showErr("Enter a name first."); name.focus(); return; }
    ui.name = nm; start.disabled = true; err.hidden = true;
    ui.live = true;  // events can arrive before the reply
    try {
      const res = await api("POST", "/api/people/enroll", { name: nm, seconds: ui.seconds, existing: ui.existing });
      if (ui.phase === "form") onEnroll(res.enroll);  // else events already moved the dialog on
    } catch (e) {
      enrollForm(ui, e instanceof Error ? e.message : String(e));
    }
  } },
  h("h3", { text: ui.existing ? `Add voice to ${ui.name}` : "Add person" }),
  h("p", { text: "Record a short voice sample so Steno recognises this person in later meetings. Use the microphone you use in meetings and a quiet room." }),
  h("label", null, "Name", name),
  h("div", { class: "en-field" }, h("span", { class: "en-label", text: "Read this aloud, at a normal pace" }), h("p", { class: "en-read", lang: "sv", text: ENROLL_TEXT })),
  h("div", { class: "en-field" }, h("span", { class: "en-label", text: "Length" }), h("div", { class: "en-lens", role: "group", "aria-label": "Length" }, lens),
    h("span", { class: "en-sub", text: "30 s is enough; 60 s is more reliable." })),
  h("p", { class: "en-consent", text: "Voiceprints are biometric data; only add people who agreed." }),
  err,
  h("div", { class: "btns" }, h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => ui.dlg.close() }), start));
  ui.dlg.replaceChildren(form);
  (ui.existing ? start : name).focus();
}

const enrollClock = (secs) => { const s = Math.max(0, Math.ceil(secs)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

function enrollActive(ui) {  // the recording view: built once, then only updated
  ui.phase = "active";
  const el = ui.el = {
    count: h("div", { class: "en-count" }),
    fill: h("i"), level: h("i"),
    status: h("p", { class: "en-status", "aria-live": "polite" }),
    btn: h("button", { class: "btn", type: "button" }),
  };
  el.btn.addEventListener("click", async () => {
    el.btn.disabled = true;
    try { await api("POST", "/api/people/enroll/cancel", {}); } catch (e) { el.btn.disabled = false; }
  });
  ui.dlg.replaceChildren(h("div", { class: "mform" },
    h("h3", { text: ui.existing ? `Adding voice to ${ui.name}` : `Recording ${ui.name}` }),
    el.count,
    h("div", { class: "en-bar", role: "progressbar", "aria-label": "Recording progress" }, el.fill),
    h("div", { class: "en-meter", "aria-hidden": "true" }, el.level),
    el.status,
    h("p", { class: "en-read", lang: "sv", text: ENROLL_TEXT }),
    h("div", { class: "btns" }, el.btn)));
}

function enrollResult(ui, d) {
  ui.live = false; ui.phase = d.state; ui.el = null;
  const failed = d.state === "error";
  const again = () => { ui.existing = true; ui.name = d.name || ui.name; enrollForm(ui); };
  const retry = () => enrollForm(ui);
  ui.dlg.replaceChildren(h("div", { class: "mform" },
    h("h3", { text: failed ? "Could not record the voice" : "Voice added" }),
    h("p", { class: failed ? "en-err" : "en-ok", role: failed ? "alert" : "status" }, icon(failed ? "alert" : "check"), h("span", { text: d.message })),
    h("div", { class: "btns" },
      failed ? h("button", { class: "btn", type: "button", text: "Try again", onclick: retry }) : null,
      !failed && d.speech_s < ENROLL_GOOD_S ? h("button", { class: "btn", type: "button", text: "Add more voice", onclick: again }) : null,
      h("button", { class: "btn primary", type: "button", text: failed ? "Close" : "Done", onclick: () => ui.dlg.close() }))));
  ui.dlg.querySelector(".btn.primary").focus();
}

function onEnroll(d) {  // SSE "enroll" and the start reply: {state, name, elapsed_s, seconds, level, message, created, speech_s}
  if (d.state === "done" && S.view === "people") guarded(loadPeople);
  const ui = enrollUi;
  if (!ui || !ui.live) return;
  if (d.state === "idle") {  // cancelled before enough was recorded
    ui.live = false;
    if (ui.dlg.open) ui.dlg.close();
    if (d.message) toast(d.message);
    return;
  }
  if (d.state === "done" || d.state === "error") { enrollResult(ui, d); return; }
  if (ui.phase !== "active") enrollActive(ui);
  const el = ui.el, recording = d.state === "recording";
  const shown = d.state === "processing" ? 0 : d.seconds - d.elapsed_s;
  el.count.textContent = enrollClock(shown);
  el.fill.style.width = `${Math.min(100, d.state === "processing" ? 100 : (d.elapsed_s / d.seconds) * 100)}%`;
  el.level.style.transform = `scaleX(${Math.max(0.02, Math.min(1, d.level || 0))})`;
  el.status.textContent = d.state === "preparing" ? "Loading the speaker model ..."
    : recording ? (d.elapsed_s < ENROLL_MIN_S ? "Recording. Read the text aloud." : "Recording. You can finish early now.")
    : "Learning the voice ...";
  el.btn.hidden = d.state === "processing";
  el.btn.textContent = recording && d.elapsed_s >= ENROLL_MIN_S ? "Finish early" : "Cancel";
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
  } else if (name === "enroll") {
    onEnroll(data);
  } else if (name === "llm") {
    fetchLlm(false);  // a job failed to log in, or the sign-in console closed
  } else if (name === "settings") {
    applyLook(data);
    if (typeof data.auto_title === "boolean") setAutoTitleDefault(data.auto_title);
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
  const moved = r.state !== lastState || (r.session && r.session !== lastSession);
  if (S.pendingRec && (r.state === "error" || (r.state === "idle" && lastState && lastState !== "idle"))) {
    S.pendingRec = false;  // the start failed: back to the ready screen
    if (S.view === "project") renderFeed();
  }
  lastState = r.state;
  if (r.session) lastSession = r.session;
  if (moved) renderSide();
  renderTop(); renderSideFoot(); updateLive(); updateJobLines();
}

function onJob(job) {
  const prev = S.jobStates.get(job.id);
  S.jobStates.set(job.id, job.state);
  if (job.state === "running") S.status.job = job;
  else if (S.status.job && S.status.job.id === job.id) S.status.job = null;
  updateJobLines(); updateButtons(); renderStrip();
  if (job.state !== "running" && prev !== job.state) {
    const name = JOB_NAMES[job.kind] || String(job.kind);
    if (job.state === "error") toast(/^Not logged in to Claude/.test(job.error) ? job.error : `${name} failed: ${job.error}`, "error");
    else {
      const msg = job.message || "";
      toast(job.kind === "title" && msg.startsWith("titled: ") ? `Named "${msg.slice(8)}"` : `${name} done${msg ? ": " + msg : ""}`);
      guarded(() => refreshMeeting(job.session, job.kind === "summary" || job.kind === "todo" ? job.kind : null));
      if (job.kind === "title" || job.kind === "auto") scheduleProjects();  // the new title in the sidebar
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

// ---------------------------------------------------------------- look: theme and transparency

const PALETTES = [
  // [id, label, dark swatches, light swatches]: the accent colours of each palette
  ["mono", "Mono", ["#bdbdbd", "#4a4a4a"], ["#3a3a3a", "#1c1c1c"]],
  ["teal", "Teal", ["#aec3b0", "#356a7e"], ["#124559", "#598392"]],
  ["wine", "Wine", ["#c4474d", "#a01820"], ["#800e13", "#640d14"]],
  ["vintage", "Vintage", ["#bcabae", "#716969"], ["#716969", "#2d2e2e"]],
  ["deepsea", "Deep sea", ["#1b98e0", "#006494"], ["#006494", "#247ba0"]],
];
const LOOK = { theme: "system", palette: "mono", transparent: true };
const systemLight = window.matchMedia("(prefers-color-scheme: light)");
function applyLook(s) {
  if (s && typeof s === "object") {
    if (["system", "dark", "light"].includes(s.theme)) LOOK.theme = s.theme;
    if (PALETTES.some((p) => p[0] === s.palette)) LOOK.palette = s.palette;
    if (typeof s.transparent === "boolean") LOOK.transparent = s.transparent;
  }
  const light = LOOK.theme === "light" || (LOOK.theme === "system" && systemLight.matches);
  const root = document.documentElement;
  root.classList.toggle("light", light);
  root.classList.toggle("solid", !LOOK.transparent);
  root.dataset.palette = LOOK.palette;
  root.style.colorScheme = light ? "light" : "dark";
  remember("steno-look", JSON.stringify(LOOK));  // no flash of the wrong theme on the next start
  const api = window.pywebview && window.pywebview.api;
  if (api && api.set_look) api.set_look(!light).catch(() => {});
}
systemLight.addEventListener("change", () => { if (LOOK.theme === "system") applyLook(); });
try { applyLook(JSON.parse(remember("steno-look") || "null")); } catch (e) { applyLook(); }

async function saveLook(change) {
  const res = await guarded(() => api("POST", "/api/settings", change));
  if (res) applyLook(res.settings);
  return res;
}

// Called by the desktop window after it was moved: one full repaint, so parts that were off
// screen when the window opened are drawn (otherwise they can stay white).
window.stenoRepaint = () => {
  const s = document.body.style;
  s.transform = "translateZ(0)";
  requestAnimationFrame(() => requestAnimationFrame(() => { s.transform = ""; }));
};

function markBrowserMode() { document.body.classList.toggle("browser", !window.pywebview); }

function init() {
  initToken();
  window.addEventListener("load", markBrowserMode);
  window.addEventListener("pywebviewready", () => { markBrowserMode(); applyLook(); renderSide(); });
  fillIcons();
  initResizer();
  $("btn-addmenu").addEventListener("click", () => showMenu($("btn-addmenu"), () => [
    { label: "New project", sub: "Create a new folder", icon: "folderPlus", onclick: () => guarded(newProject) },
    { label: "Add existing folder", icon: "folder", onclick: () => guarded(addProject) }]));
  $("empty-new").addEventListener("click", () => guarded(newProject));
  $("empty-add").addEventListener("click", () => guarded(addProject));
  $("btn-projmenu").addEventListener("click", () => showMenu($("btn-projmenu"), () => [
    ...(pickerAvailable() && S.sel && S.sel !== "inbox"
      ? [{ label: "Open in File Explorer", icon: "folderOpen", onclick: () => guarded(() => openInExplorer("project", S.sel)) }] : []),
    { label: "Subfolders...", sub: "Board, 1-1, ... each in its own folder", icon: "folderPlus", onclick: () => guarded(editTypes) },
    "-",
    { label: "Forget project", sub: "Files stay on disk", icon: "forget", danger: true, onclick: () => guarded(forgetProject) }]));
  initDrag();
  watchDay();
  $("nav-people").addEventListener("click", showPeople);
  $("nav-settings").addEventListener("click", showSettings);
  document.querySelectorAll(".menu-btn").forEach((b) => b.addEventListener("click", menuButton));
  $("side-close").addEventListener("click", closeDrawer);
  $("side-hide").addEventListener("click", () => applySide(true));
  initWindowButtons();
  $("scrim").addEventListener("click", closeDrawer);
  $("btn-rec").addEventListener("click", () => startRecording());
  $("btn-pause").addEventListener("click", () => recordCmd((S.status.recorder || {}).paused ? "resume" : "pause"));
  $("btn-stop").addEventListener("click", () => recordCmd("stop"));
  $("btn-src").addEventListener("click", () => showMenu($("btn-src"), sourceMenu));
  $("btn-keep").addEventListener("click", () => { S.opts.keep = !S.opts.keep; renderTop(); });  // until Steno closes
  $("btn-autotitle").addEventListener("click", () => { S.autoTitle = !S.autoTitle; renderAutoTitle(); });
  $("btn-autotitle-live").addEventListener("click", async () => {
    const res = await guarded(() => api("POST", "/api/record/auto_title", { enabled: !(S.status.recorder || {}).auto_title }));
    if (res && res.recorder) { S.status.recorder = res.recorder; renderAutoTitle(); }
  });
  $("rec-title").addEventListener("input", renderAutoTitle);
  window.addEventListener("focus", () => fetchLlm(false));  // cheap: the server keeps the answer for a minute
  $("rec-type").addEventListener("click", () => { if (S.sel && S.sel !== "inbox") showMenu($("rec-type"), typeMenuItems); });
  $("rec-title").addEventListener("keydown", (ev) => { if (ev.key === "Enter") startRecording(); });
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
    try {
      const set = (await api("GET", "/api/settings")).settings;
      applyLook(set);
      if (typeof set.auto_title === "boolean") setAutoTitleDefault(set.auto_title);
    } catch (e) { /* defaults */ }
    fetchLlm(false);
  });
  connect();
}

init();
