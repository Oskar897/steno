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
function closeMenu(refocus) {
  if (!openMenu) return;
  const { el, anchor, from } = openMenu;
  openMenu = null;
  if (anchor) anchor.setAttribute("aria-expanded", "false");
  el.classList.add("closing");
  setTimeout(() => el.remove(), 130);
  const back = anchor || from;
  if (refocus && back && back.isConnected) back.focus({ preventScroll: true });
}
/* items: [{label, sub, icon, onclick, disabled, danger, checked, keep}] | "-" | {header}
   anchor: the button the menu hangs under; with `at` ({x, y} in window px, a right-click) the menu
   opens at that point instead (anchor null) and `from` is the element to give the focus back to. */
function showMenu(anchor, build, at, from) {
  if (anchor && openMenu && openMenu.anchor === anchor) { closeMenu(); return; }
  closeMenu();
  const el = h("div", { class: "menu", role: "menu" });
  const fill = () => {
    el.replaceChildren(...build().map((it) => {
      if (it === "-") return h("hr");
      if (it.node) return it.node;  // a ready-made element (e.g. a stepper)
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
  const z = zoom(), mw = el.offsetWidth, mh = el.offsetHeight;  // menu styles are in zoomed px
  const W = window.innerWidth / z, H = window.innerHeight / z;
  if (at) {
    const x = at.x / z, y = at.y / z;
    const up = y + mh + 8 > H && y - mh > 8;
    el.classList.toggle("up", up);
    el.style.left = Math.max(8, Math.min(W - mw - 8, x)) + "px";
    el.style.top = Math.max(8, up ? y - mh : y) + "px";
  } else {
    const r = anchor.getBoundingClientRect(), rb = r.bottom / z, rt = r.top / z, rr = r.right / z;
    const up = rb + mh + 8 > H && rt - mh - 8 > 0;
    el.classList.toggle("up", up);
    el.style.left = Math.max(8, Math.min(W - mw - 8, rr - mw)) + "px";
    el.style.top = (up ? rt - mh - 6 : rb + 6) + "px";
    anchor.setAttribute("aria-expanded", "true");
  }
  openMenu = { el, anchor: anchor || null, from: from || null };
  const first = el.querySelector(".mi:not(:disabled)");
  if (first) first.focus({ preventScroll: true });
}
document.addEventListener("pointerdown", (ev) => {
  if (openMenu && !openMenu.el.contains(ev.target) && !(openMenu.anchor && openMenu.anchor.contains(ev.target))) closeMenu();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if (openMenu) { closeMenu(true); ev.preventDefault(); return; }
    if (!$("scrim").hidden) { closeDrawer(); return; }
    if (S.view === "settings" && !$("modal").open && !ev.defaultPrevented) {
      const t = ev.target;
      // in a field the first Esc only leaves the field (the glossary has unsaved text), the next one closes
      if (t && t.matches && t.matches("input, textarea, select")) t.blur(); else leaveSettings();
    }
    return;
  }
  if (!openMenu || !["ArrowDown", "ArrowUp", "Home", "End"].includes(ev.key)) return;
  const items = [...openMenu.el.querySelectorAll(".mi:not(:disabled)")];
  if (!items.length) return;
  const at = items.indexOf(document.activeElement);
  const next = ev.key === "Home" ? 0 : ev.key === "End" ? items.length - 1
    : ev.key === "ArrowDown" ? (at + 1) % items.length : (at <= 0 ? items.length - 1 : at - 1);
  items[next].focus({ preventScroll: true });
  ev.preventDefault();
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
    const w = ev.clientX / zoom() - 8;
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
  back: null,             // where Settings was opened from: {view, sel, meeting, scroll} (the X and Esc return there)
  sel: null,              // project id or "inbox"
  meeting: null,          // open meeting (session id), "new" (ready to record) or null (pick one)
  pendingRec: false,      // Record pressed: open the new meeting as soon as it is listed
  meetings: [],           // list for the selected project, oldest first
  lists: new Map(),       // project id -> meetings (oldest first), for the sidebar
  expanded: loadExpanded(), showAll: new Set(), showArchived: new Set(), showArchivedProjects: false,
  folded: loadFolded(),   // "pid::type" groups closed in the sidebar
  newType: new Map(),     // project id -> meeting type for the next recording ("" = none)
  recFolder: null,        // {pid, folder, name, type, title}: record the next meeting into it
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
  const el = h("div", { class: "toast" + (kind ? " " + kind : "") }, icon(kind === "error" || kind === "warn" ? "alert" : "check"), h("span", { text: String(text) }));
  const dismiss = () => { if (el.classList.contains("out")) return; el.classList.add("out"); setTimeout(() => el.remove(), 230); };
  el.addEventListener("click", dismiss);
  if (action) {
    el.append(h("button", { class: "toast-act", type: "button", text: action.label,
      onclick: (ev) => { ev.stopPropagation(); dismiss(); action.run(); } }));
  }
  $("toasts").append(el);
  while ($("toasts").children.length > 4) $("toasts").firstChild.remove();
  setTimeout(dismiss, kind === "error" || kind === "warn" ? 9000 : action ? 8000 : 4500);
}
async function guarded(fn) {
  try { return await fn(); } catch (e) { toast(e instanceof Error ? e.message : e, "error"); return null; }
}

// ---------------------------------------------------------------- modal dialogs

function pickerAvailable() {
  return !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);
}
// path | null (cancelled) | undefined (no native picker). start/ref/type: where it opens
// ("project" + id, or "type" + id + subfolder name), resolved by the app from ids, not paths.
async function pickFolder(start = null, ref = null, type = null) {
  if (!pickerAvailable()) return undefined;
  try { return (await window.pywebview.api.pick_folder(start, ref, type)) || null; } catch (e) { return null; }
}

const baseName = (path) => String(path || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
const joinPath = (dir, name) => (dir ? dir.replace(/[\\/]+$/, "") + (dir.includes("\\") ? "\\" : "/") + name : name);

/* The dialog shell. `content`: nodes, or (ctx) => nodes; ctx = {error(msg), setOk({label, danger, disabled})}.
   `collect()` gives the values; `submit(values)` (async) may refuse by throwing: the message then shows
   inside the dialog, which stays open with everything typed so far. Resolves with submit's result
   (default: the values), or null when cancelled. */
function modal({ title, text, content, ok = "OK", danger = false, collect = () => true, submit }) {
  return new Promise((resolve) => {
    const dlg = $("modal");
    let busy = false, done = false;
    const err = h("p", { class: "ferr", role: "alert", hidden: true });
    const okBtn = h("button", { class: "btn primary", type: "submit" });
    const cancel = h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => finish(null) });
    const ctx = {
      error: (msg) => { err.textContent = msg || ""; err.hidden = !msg; },
      setOk: (o) => {
        if (o.label !== undefined) okBtn.textContent = o.label;
        if (o.danger !== undefined) okBtn.className = "btn " + (o.danger ? "danger" : "primary");
        if (o.disabled !== undefined) okBtn.disabled = !!o.disabled;
      },
    };
    ctx.setOk({ label: ok, danger });
    let form = null;
    // Escape closes the dialog by itself. A "close" event that arrives after the next dialog was opened
    // belongs to the old one: it must not close the new one.
    const onClose = () => { if (dlg.open && dlg.firstChild === form) return; finish(null); };
    const onCancel = (ev) => { if (busy) ev.preventDefault(); };
    function finish(v) {
      if (done) return;
      done = true;
      dlg.removeEventListener("close", onClose);
      dlg.removeEventListener("cancel", onCancel);
      resolve(v);
      if (dlg.open && dlg.firstChild === form) dlg.close();
    }
    const nodes = typeof content === "function" ? content(ctx) : content || [];
    form = h("form", { class: "mform", onsubmit: async (ev) => {
      ev.preventDefault();
      if (busy) return;
      const values = collect();
      if (!submit) { finish(values); return; }
      busy = true; okBtn.disabled = true; cancel.disabled = true; ctx.error("");
      try { finish(await submit(values)); }
      catch (e) {  // toasts sit behind the dialog: show it here
        busy = false; okBtn.disabled = false; cancel.disabled = false;
        ctx.error(e instanceof Error ? e.message : String(e));
      }
    } },
    h("h3", { text: title }), text ? h("p", { text }) : null, nodes, err,
    h("div", { class: "btns" }, cancel, okBtn));
    dlg.replaceChildren(form);
    dlg.addEventListener("close", onClose);
    dlg.addEventListener("cancel", onCancel);
    dlg.showModal();
    const first = form.querySelector("input:not([type=checkbox]), select");
    if (first) first.focus();
  });
}

/* A folder in a dialog: "Choose folder..." (native picker) + the chosen path + clear. Without the
   native picker (plain browser) it is a text field for the path. Returns {el, get, set}. */
function folderField({ label, value = "", button = "Choose folder...", placeholder = "Folder path", clearable = true, start = null, ref = null, type = null, onChange }) {
  let path = value || "", text = null;
  const set = (p, quiet) => {
    path = p || "";
    paint();
    if (!quiet && onChange) onChange(path);
  };
  const shown = h("span", { class: "ffield-path" });
  const clear = h("button", { class: "icon-btn small", type: "button", title: "Clear", "aria-label": "Clear folder", onclick: () => set("") }, icon("x"));
  function paint() {
    if (text) { if (text.value.trim() !== path) text.value = path; return; }
    shown.textContent = path || "No folder chosen";
    shown.title = path;
    shown.classList.toggle("none", !path);
    clear.hidden = !path || !clearable;
  }
  let row;
  if (pickerAvailable()) {
    row = h("div", { class: "ffield" },
      h("button", { class: "btn", type: "button", text: button, onclick: async () => { const p = await pickFolder(start, ref, type); if (p) set(p); } }),
      shown, clear);
  } else {
    text = h("input", { class: "text", type: "text", placeholder, autocomplete: "off", "aria-label": label,
      oninput: () => { path = text.value.trim(); if (onChange) onChange(path); } });
    row = text;
  }
  paint();
  return { el: h("div", { class: "mfield" }, h("span", { class: "flabel", text: label }), row), get: () => path, set };
}

/* fields: [{key, label, value, placeholder, browse, options:[{value,label,disabled}], checkbox}] -> values | null
   (a checkbox field gives true/false). `submit(values)`: see modal(). */
function formModal({ title, text, fields = [], ok = "OK", danger = false, submit }) {
  const inputs = {};
  const rows = fields.map((f) => {
    let input;
    if (f.checkbox) {
      input = h("input", { type: "checkbox" });
      input.checked = !!f.value;
      inputs[f.key] = input;
      return h("label", { class: "mcheck" }, input, h("span", { text: f.label }));
    }
    if (f.options) {
      input = h("select", null, f.options.map((o) => h("option", { value: o.value, text: o.label, disabled: !!o.disabled })));
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
  return modal({ title, text, content: rows, ok, danger, submit,
    collect: () => Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.type === "checkbox" ? i.checked : i.value.trim()])) });
}

/* A folder path from the user: the native picker, or (plain browser) a small dialog. null = cancelled. */
async function askFolder(title, start, ref, type) {
  if (pickerAvailable()) return pickFolder(start, ref, type);
  const v = await formModal({ title, fields: [{ key: "path", label: "Folder path", placeholder: "Path of an existing folder" }], ok: "OK" });
  return v && v.path ? v.path : null;
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
  const usable = (id) => id && exists(id) && !(projectById(id) || {}).archived;  // a pick is never an archived project
  const pick = [recProjectId(), saved, (S.projects.find((p) => !p.archived) || {}).id,
    S.inbox > 0 || S.projects.length ? "inbox" : null].find(usable);
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
  if (S.view !== "settings") S.back = { view: S.view, sel: S.sel, meeting: S.meeting, scroll: $("feed").scrollTop };
  S.view = "settings";
  showView(); renderSide();
  guarded(loadSettings);
}

/* The X / Esc of Settings: back to the people list or to the project and meeting that was open. */
function leaveSettings() {
  if (S.view !== "settings") return;
  const b = S.back || { view: "project", sel: S.sel, meeting: S.meeting, scroll: 0 };
  S.back = null;
  if (b.view === "people") { showPeople(); return; }
  if (b.sel) {
    selectProject(b.sel, b.meeting);
    if (!S.stick && b.scroll) $("feed").scrollTop = b.scroll;  // the hidden list lost its place
  } else {
    S.view = "project";
    showView(); renderSide(); renderTop(); renderFeed();
  }
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
  const str = h("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Colour strength" });
  const paintStr = () => {
    for (const b of str.children) {
      const on = b.dataset.v === LOOK.intensity;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    }
  };
  for (const [v, label] of [["subtle", "Subtle"], ["medium", "Medium"], ["strong", "Strong"]]) {
    str.append(h("button", { type: "button", role: "radio", "data-v": v, text: label,
      onclick: async () => { LOOK.intensity = v; paintStr(); applyLook(); await saveLook({ intensity: v }); paintStr(); } }));
  }
  paintStr();
  seg.addEventListener("click", () => setTimeout(paintPal, 0));
  const sizeSeg = h("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Interface size" });
  const paintSize = () => {
    for (const b of sizeSeg.children) {
      const on = Number(b.dataset.v) === LOOK.scale;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    }
  };
  for (const v of [90, 100, 110, 125, 140]) {
    sizeSeg.append(h("button", { type: "button", role: "radio", "data-v": String(v), text: v + "%",
      onclick: async () => { LOOK.scale = v; paintSize(); applyLook(); await saveLook({ ui_scale: v }); paintSize(); } }));
  }
  paintSize();
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
        h("div", { class: "pn" }, h("div", { text: "Interface size" }), h("div", { class: "sub", text: "Text, icons and controls, in the sidebar and the transcript alike. 100% is the default." })), sizeSeg),
      h("div", { class: "set-row static" },
        h("div", { class: "pn" }, h("div", { text: "Colours" }), h("div", { class: "sub", text: "Accent colour on grey surfaces. Every palette has a dark and a light version." })), pal),
      h("div", { class: "set-row static" },
        h("div", { class: "pn" }, h("div", { text: "Colour strength" }), h("div", { class: "sub", text: "Subtle: colour on buttons and focus only. Medium: selected items take the colour too. Strong: the background also leans toward it, muted." })), str),
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
  const proj = S.projects.find((p) => p.id === id);
  const closed = !!(proj && proj.archived);  // an archived project: no recording into it
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
      "data-ts": String(Date.parse(info.started_at) || 0), "data-ctx": "meeting", "data-pid": id, "data-sid": info.session }, main, more);
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
    // subfolders first (level 1, meetings inside at level 2), then the meetings in the project itself (level 1)
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
      const tip = t.missing ? `${t.folder}\nThis folder is missing; use Change folder... in the menu.` : t.folder || type;
      items.push(h("div", { class: "tnode" + (open ? " open" : ""), "data-drop": id, "data-drop-type": type },
        h("div", { class: "trow", "data-ctx": "type", "data-pid": id, "data-type": type },
          h("button", { class: "tmain", type: "button", "aria-expanded": String(open), title: tip,
            onclick: () => { if (open) S.folded.add(key); else S.folded.delete(key); remember("steno-folded", JSON.stringify([...S.folded])); renderSide(); } },
          icon("chevron"), h("i", { class: "ticon" }, icon("folder")), h("span", { class: "tname", text: type }),
          t.missing ? h("i", { class: "warn-ic", title: tip }, icon("alert")) : null,
          open ? null : h("span", { class: "tcount", text: String(part.length) })),
          closed ? null : h("button", { class: "padd", type: "button", title: `New ${type} meeting`, "aria-label": `New ${type} meeting`, onclick: () => newMeeting(id, type) }, icon("plus")),
          moreButton(`${type} actions`, () => subfolderItems(id, t))),
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

/* The ⋯ button on a sidebar row: same items as the right-click menu. */
function moreButton(label, build) {
  const more = h("button", { class: "padd pmore", type: "button", title: label, "aria-label": label, "aria-haspopup": "menu" }, icon("more"));
  more.addEventListener("click", (ev) => { ev.stopPropagation(); showMenu(more, build); });
  return more;
}

/* One project row with its meetings. `p` is a project, or null for the Inbox / TBD. */
function projectNode(p, live) {
  const id = p ? p.id : "inbox", name = p ? p.name : INBOX_NAME, count = p ? p.meetings : S.inbox;
  const open = S.expanded.has(id);
  const here = S.view === "project" && S.sel === id;
  const closed = !!(p && p.archived);
  const chev = h("button", { class: "pchev", type: "button", "aria-label": open ? "Collapse" : "Expand", "aria-expanded": String(open), onclick: () => toggleExpanded(id) }, icon("chevron"));
  let tip = p ? (p.path ? p.path : "No folder linked") : "Meetings that are not in a project yet";
  if (p && p.path && p.path_missing) tip += "\nThis folder is missing; use Change linked folder... in the menu.";
  const main = h("button", { class: "pmain", type: "button", title: tip, onclick: () => selectProject(id) },
    h("i", { class: "picon" }, icon(id === "inbox" ? "inbox" : "folder")),
    h("span", { class: "pname", text: name }),
    p && p.path_missing ? h("i", { class: "warn-ic", title: tip }, icon("alert")) : null,
    live ? h("i", { class: "live-dot", title: "Recording" }) : open || (id === "inbox" && !count) ? null : h("span", { class: "pcount", text: String(count) }));
  const add = closed ? null : h("button", { class: "padd", type: "button", title: `New meeting in ${name}`, "aria-label": `New meeting in ${name}`, onclick: () => newMeeting(id) }, icon("plus"));
  const sub = p && !closed ? h("button", { class: "padd", type: "button", title: `New subfolder in ${name}`, "aria-label": `New subfolder in ${name}`, onclick: () => addSubfolder(id) }, icon("folderPlus")) : null;
  const more = moreButton(`${name} actions`, () => (p ? projectItems(p) : inboxItems()));
  return h("div", { class: "pnode" + (open ? " open" : "") + (here ? " here" : "") + (closed ? " archived" : ""), "data-drop": id, "data-drop-type": "" },
    h("div", { class: "pitem prow" + (here && !open ? " active" : ""), "data-ctx": p ? "project" : "inbox", "data-pid": id }, chev, main, sub, add, more),
    open ? sideMeetings(id) : null);
}

async function openInExplorer(kind, ref, type) {
  const bridge = window.pywebview && window.pywebview.api;
  if (!bridge || !bridge.open_folder) { toast("Opening folders needs the desktop app.", "error"); return; }
  const ok = await bridge.open_folder(kind, ref || null, type || null);
  if (!ok) toast("That folder was not found.", "error");
}

function renderSide() {
  const live = recProjectId();
  const open = S.projects.filter((p) => !p.archived), archived = S.projects.filter((p) => p.archived);
  $("plist").replaceChildren(...open.map((p) => projectNode(p, live === p.id)));
  $("inbox-slot").replaceChildren(projectNode(null, live === "inbox"));
  $("arch-slot").replaceChildren(...archivedProjects(archived, live));
  $("nav-people").classList.toggle("active", S.view === "people");
  $("nav-settings").classList.toggle("active", S.view === "settings");
  if (DRAG.active && !DRAG.active.settled) { paintDrop(); syncGap(false); }
  $("people-count").textContent = S.people.length ? String(S.people.length) : "";
  const elsewhere = live && (S.view !== "project" || live !== S.sel);
  document.querySelectorAll(".menu-btn").forEach((b) => b.classList.toggle("badge", !!elsewhere));
  renderSideFoot();
}

/* "Archived (n)" at the bottom of the project list: closed until clicked (open while one of them is shown). */
function archivedProjects(archived, live) {
  if (!archived.length) return [];
  const open = S.showArchivedProjects || archived.some((p) => p.id === S.sel && S.view === "project");
  const head = h("button", { class: "pitem arch-head", type: "button", "aria-expanded": String(open),
    onclick: () => { S.showArchivedProjects = !open; renderSide(); } },
  h("i", { class: "picon" }, icon("archive")), h("span", { class: "pname", text: `Archived (${archived.length})` }), icon("chevron", open ? "up" : ""));
  return [head, ...(open ? archived.map((p) => projectNode(p, live === p.id)) : [])];
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
  const folder = !active && S.sel ? recFolderOf(S.sel) : null;
  const typed = !active && (!!input.value.trim() || !!folder);  // a folder brings its own title
  const off = aiOff();
  const on = (active ? !!r.auto_title : S.autoTitle) && !off && !typed;
  input.placeholder = folder ? `${folder.title} (from the folder)`
    : on && aiOk() ? "Title, or let Claude name it" : "Meeting title (optional)";
  let tip;
  if (off) tip = `${AI_OFF_TEXT[S.llm.state]}. Log in under Settings to use Auto title.`;
  else if (typed && folder) tip = "Auto title is not needed: the meeting is named after its folder.";
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

// ---------------------------------------------------------------- projects: create, rename, link, archive, delete
//
// A project is a label for meetings. It can be linked to a folder (a shortcut, nothing more); meetings
// are saved in its subfolders, each of which points at a folder anywhere on disk.

const projectById = (id) => S.projects.find((p) => p.id === id) || null;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

async function newProject() {
  let name, folder;
  const created = await modal({
    title: "New project",
    text: "A project groups your meetings. Add subfolders to it next: each one is a folder where its meetings are saved.",
    ok: "Create",
    content: () => {
      name = h("input", { class: "text", type: "text", placeholder: "Project name", maxlength: "80", autocomplete: "off", "aria-label": "Project name" });
      folder = folderField({ label: "Link a folder (optional)", button: "Choose folder..." });
      return [h("label", null, "Name", name), folder.el];
    },
    collect: () => ({ name: name.value.trim(), folder: folder.get() }),
    submit: async (v) => {
      if (!v.name) { name.focus(); throw new Error("Enter a name for the project."); }
      const body = { name: v.name };
      if (v.folder) body.folder = v.folder;
      return (await api("POST", "/api/projects", body)).project;
    },
  });
  if (created) await afterProjectAdded(created);
}

async function afterProjectAdded(project) {
  await guarded(loadProjects);
  if (project && project.id) selectProject(project.id);  // no meetings yet: the ready screen offers "Add subfolder"
}

async function renameProject(p) {
  const v = await formModal({ title: "Rename project", fields: [{ key: "name", label: "Name", value: p.name }], ok: "Rename",
    submit: async (vals) => {
      if (!vals.name) throw new Error("Enter a name.");
      if (vals.name !== p.name) await api("POST", `/api/projects/${encodeURIComponent(p.id)}/update`, { name: vals.name });
      return vals;
    } });
  if (v) await loadProjects();
}

async function linkProjectFolder(p) {
  const path = await askFolder(p.path ? "Change linked folder" : "Link a folder", "project", p.id);
  if (!path) return;
  await api("POST", `/api/projects/${encodeURIComponent(p.id)}/update`, { folder: path });
  await loadProjects();
  toast(`${p.name} is linked to ${baseName(path) || path}`);
}

async function unlinkProjectFolder(p) {
  await api("POST", `/api/projects/${encodeURIComponent(p.id)}/update`, { folder: null });
  await loadProjects();
  toast(`${p.name} is no longer linked to a folder; the folder and its files stay`);
}

async function archiveProject(p, archived) {
  await api("POST", `/api/projects/${encodeURIComponent(p.id)}/update`, { archived });
  if (archived && S.sel === p.id) { S.sel = null; S.meeting = null; S.feed.clear(); S.meetings = []; }
  else if (!archived) S.showArchivedProjects = false;
  await loadProjects();
  renderTop(); renderSide(); renderFeed();
  toast(archived ? `${p.name} archived. Find it under Archived at the bottom of the sidebar.` : `${p.name} restored`);
}

/* Delete... on a project: by default only Steno forgets it (files stay, its meetings go to the Inbox);
   the checkbox also deletes its meetings, and the dialog then turns red. */
async function deleteProject(p) {
  const n = p.all_meetings || 0;
  const linked = p.path ? " Its folder and your files stay." : "";
  const calm = `"${p.name}" is removed from Steno.${linked}${n ? (n === 1 ? ` Its meeting moves to the ${INBOX_NAME}.` : ` Its ${n} meetings move to the ${INBOX_NAME}.`) : ""}`;
  const loud = `This deletes all ${plural(n, "meeting")} of "${p.name}" for good: recordings, transcripts, summaries and to-dos (Steno's files only; your own files stay). This cannot be undone.`;
  let box;
  const res = await modal({
    title: "Delete project",
    ok: "Remove from Steno",
    content: (ctx) => {
      const lead = h("p", { text: calm });
      if (!n) return [lead];
      box = h("input", { type: "checkbox" });
      box.addEventListener("change", () => {
        lead.textContent = box.checked ? loud : calm;
        lead.classList.toggle("mwarn", box.checked);
        ctx.setOk(box.checked ? { label: `Delete project and ${plural(n, "meeting")}`, danger: true } : { label: "Remove from Steno", danger: false });
      });
      return [lead, h("label", { class: "mcheck" }, box, h("span", { text: n === 1 ? "Also delete its meeting" : `Also delete all ${n} meetings` }))];
    },
    collect: () => ({ meetings: !!(box && box.checked) }),
    submit: (v) => api("POST", `/api/projects/${encodeURIComponent(p.id)}/delete`, { meetings: v.meetings }),
  });
  if (!res) return;
  if (S.sel === p.id) { S.sel = null; S.meeting = null; S.feed.clear(); S.meetings = []; }
  await guarded(loadProjects);
  renderTop(); renderSide(); renderFeed();
  toast(res.deleted ? `${p.name} removed, ${plural(res.deleted, "meeting")} deleted`
    : n ? `${p.name} removed from Steno; its meetings are in the ${INBOX_NAME}` : `${p.name} removed from Steno`);
}

async function deleteInbox() {
  const n = S.inbox;
  if (!n) { toast("Inbox / TBD is empty."); return; }
  const ok = await formModal({
    title: "Delete Inbox / TBD meetings",
    text: `Delete all ${n} meeting${n === 1 ? "" : "s"} in ${INBOX_NAME} for good? Recordings, transcripts, summaries and to-dos are deleted. This cannot be undone.`,
    ok: `Delete ${n}`, danger: true });
  if (!ok) return;
  const res = await guarded(() => api("POST", "/api/inbox/delete", {}));
  if (!res) return;
  if (S.sel === "inbox") S.feed.clear();
  await guarded(loadProjects);
  renderTop(); renderSide(); renderFeed();
  toast(`${res.deleted} meeting${res.deleted === 1 ? "" : "s"} deleted`);
}

// ---------------------------------------------------------------- subfolders: add, rename, change folder, remove

/* Add subfolder: pick an existing folder anywhere (default), or create a new one inside a folder you pick. */
async function addSubfolder(pid) {
  const p = projectById(pid);
  if (!p) return;
  let mode = "existing", touched = false;
  let name, preview, existing, where, seg, nameLabel;
  const added = await modal({
    title: `Add subfolder to ${p.name}`,
    text: "Meetings saved in a subfolder are written into its folder, e.g. Board or 1-1.",
    ok: "Add subfolder",
    content: () => {
      name = h("input", { class: "text", type: "text", maxlength: "80", autocomplete: "off", "aria-label": "Name",
        oninput: () => { touched = true; paint(); } });
      nameLabel = h("span", { text: "Name" });
      preview = h("p", { class: "fpreview" });
      existing = folderField({ label: "Folder", button: "Choose folder...", start: "project", ref: pid,
        onChange: (path) => { if (path && !touched) name.value = baseName(path); paint(); } });
      where = folderField({ label: "Create it in", button: "Choose location...", start: "project", ref: pid, onChange: () => paint() });
      seg = h("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "How" });
      for (const [v, label] of [["existing", "Choose existing folder"], ["create", "Create new folder"]]) {
        seg.append(h("button", { type: "button", role: "radio", "data-v": v, text: label, onclick: () => { mode = v; paint(); } }));
      }
      const paint = () => {
        for (const b of seg.children) { const on = b.dataset.v === mode; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); }
        existing.el.hidden = mode !== "existing";
        where.el.hidden = mode !== "create";
        nameLabel.textContent = mode === "create" ? "New folder name" : "Name in Steno";
        name.placeholder = mode === "create" ? "e.g. Board" : "Defaults to the folder's name";
        const nm = name.value.trim();
        const path = mode === "existing" ? existing.get() : where.get() && nm ? joinPath(where.get(), nm) : "";
        preview.hidden = !path;
        preview.textContent = path ? (mode === "create" ? `Will create ${path}` : path) : "";
      };
      paint();
      return [seg, existing.el, where.el, h("label", null, nameLabel, name), preview];
    },
    collect: () => ({ mode, name: name.value.trim(), folder: mode === "existing" ? existing.get() : where.get() }),
    submit: async (v) => {
      if (!v.folder) throw new Error(v.mode === "create" ? "Choose where to create the folder." : "Choose a folder.");
      if (v.mode === "create" && !v.name) { name.focus(); throw new Error("Give the new folder a name."); }
      const body = { folder: v.folder };
      if (v.name) body.name = v.name;
      if (v.mode === "create") body.create = true;
      await api("POST", `/api/projects/${encodeURIComponent(pid)}/subfolders`, body);
      return { name: v.name || baseName(v.folder) };
    },
  });
  if (!added) return;
  if (!S.expanded.has(pid)) { S.expanded.add(pid); saveExpanded(); }
  await guarded(loadProjects);
  toast(`Subfolder ${added.name} added`, null, pickerAvailable()
    ? { label: "Open in File Explorer", run: () => guarded(() => openInExplorer("type", pid, added.name)) } : null);
  if (S.meeting === "new" && S.sel === pid) renderFeed();
}

async function renameSubfolder(pid, name) {
  const v = await formModal({ title: "Rename subfolder", text: "Only the name in Steno changes; the folder on disk stays.",
    fields: [{ key: "name", label: "Name", value: name }], ok: "Rename",
    submit: async (vals) => {
      if (!vals.name) throw new Error("Enter a name.");
      if (vals.name !== name) await api("POST", `/api/projects/${encodeURIComponent(pid)}/subfolders/rename`, { name, new_name: vals.name });
      return vals;
    } });
  if (!v || v.name === name) return;
  if (S.newType.get(pid) === name) setRecType(pid, v.name);
  if (S.recFolder && S.recFolder.pid === pid && S.recFolder.type === name) S.recFolder.type = v.name;
  await loadProjects();
}

async function changeSubfolderFolder(pid, t) {
  const path = await askFolder(`Change folder of ${t.name}`, "type", pid, t.name);
  if (!path) return;
  await api("POST", `/api/projects/${encodeURIComponent(pid)}/subfolders/folder`, { name: t.name, folder: path });
  await loadProjects();
  toast(`${t.name} now points at ${baseName(path) || path}`);
}

/* Remove... on a subfolder: Steno forgets it (its folder and files stay, its meetings stay in the project);
   the checkbox also deletes its meetings, and the dialog then turns red. */
async function removeSubfolder(pid, t) {
  const name = t.name, n = t.meetings || 0;  // the server's count includes archived meetings
  const calm = `${name} is removed from Steno. Its folder and your files stay.${n ? ` Its ${plural(n, "meeting")} stay in the project.` : ""}`;
  const loud = `This deletes the ${plural(n, "meeting")} of ${name} for good (archived ones too): recordings, transcripts, summaries and to-dos. Your own files in the meeting folders stay. This cannot be undone.`;
  let box;
  const res = await modal({
    title: "Remove subfolder",
    ok: "Remove",
    content: (ctx) => {
      const lead = h("p", { text: calm });
      if (!n) return [lead];
      box = h("input", { type: "checkbox" });
      box.addEventListener("change", () => {
        lead.textContent = box.checked ? loud : calm;
        lead.classList.toggle("mwarn", box.checked);
        ctx.setOk(box.checked ? { label: `Delete ${plural(n, "meeting")}`, danger: true } : { label: "Remove", danger: false });
      });
      return [lead, h("label", { class: "mcheck" }, box, h("span", { text: n === 1 ? "Also delete its meeting" : `Also delete its ${n} meetings` }))];
    },
    collect: () => ({ meetings: !!(box && box.checked) }),
    submit: (v) => api("POST", `/api/projects/${encodeURIComponent(pid)}/types/delete`, { name, meetings: v.meetings }),
  });
  if (!res) return;
  if (S.newType.get(pid) === name) setRecType(pid, "");
  if (S.recFolder && S.recFolder.pid === pid && S.recFolder.type === name) S.recFolder = null;
  S.feed.clear();
  await guarded(loadProjects);
  renderTop(); renderSide(); renderFeed();
  toast(res.deleted ? `${name} removed, ${plural(res.deleted, "meeting")} deleted` : `${name} removed from Steno`);
}

// ---------------------------------------------------------------- menus on rows and in the header
//
// One list of items per kind of row. The same items come from right-click on the row, the ⋯ button
// on the row (hover; always on the selected row) and the ⋯ in the header (for the open project).

function projectItems(p) {
  const act = (fn) => () => guarded(() => fn(p));
  const folderOpen = pickerAvailable() && p.path && !p.path_missing
    ? [{ label: "Open in File Explorer", icon: "folderOpen", onclick: () => guarded(() => openInExplorer("project", p.id)) }] : [];
  if (p.archived) {
    return [
      { label: "Restore", sub: "Show it in the project list again", icon: "unarchive", onclick: act((x) => archiveProject(x, false)) },
      ...folderOpen,
      "-",
      { label: "Delete...", icon: "trash", danger: true, onclick: act(deleteProject) }];
  }
  return [
    { label: "New subfolder...", icon: "folderPlus", onclick: () => guarded(() => addSubfolder(p.id)) },
    "-",
    { label: "Rename...", icon: "pencil", onclick: act(renameProject) },
    { label: p.path ? "Change linked folder..." : "Link folder...", icon: "folder", onclick: act(linkProjectFolder) },
    ...(p.path ? [{ label: "Unlink folder", sub: "The folder and its files stay", icon: "forget", onclick: act(unlinkProjectFolder) }] : []),
    ...folderOpen,
    "-",
    { label: "Archive", sub: "Hide it from the list; meetings and files stay", icon: "archive", onclick: act((x) => archiveProject(x, true)) },
    { label: "Delete...", icon: "trash", danger: true, onclick: act(deleteProject) }];
}

function subfolderItems(pid, t) {
  const p = projectById(pid);
  const closed = !!(p && p.archived);
  return [
    { label: "Record here", icon: "mic", disabled: closed, title: closed ? "Restore the project to record into it" : null, onclick: () => newMeeting(pid, t.name) },
    ...(pickerAvailable() && !t.missing ? [{ label: "Open in File Explorer", icon: "folderOpen", onclick: () => guarded(() => openInExplorer("type", pid, t.name)) }] : []),
    "-",
    { label: "Rename...", icon: "pencil", onclick: () => guarded(() => renameSubfolder(pid, t.name)) },
    { label: "Change folder...", sub: t.missing ? "The folder is missing" : null, icon: "folder", onclick: () => guarded(() => changeSubfolderFolder(pid, t)) },
    "-",
    { label: "Remove...", sub: "The folder and your files stay", icon: "trash", danger: true, onclick: () => guarded(() => removeSubfolder(pid, t)) }];
}

function inboxItems() {
  return [
    ...(pickerAvailable() ? [{ label: "Open in File Explorer", icon: "folderOpen", onclick: () => guarded(() => openInExplorer("inbox")) }, "-"] : []),
    { label: "Delete all meetings...", sub: `The ${S.inbox} in ${INBOX_NAME}`, icon: "trash", danger: true, disabled: !S.inbox, onclick: () => guarded(deleteInbox) }];
}

/* The header's ⋯: the open project (or the Inbox). */
function headerItems() {
  if (S.sel === "inbox") return inboxItems();
  const p = curProject();
  return p ? projectItems(p) : [];
}

/* Right-click on a sidebar row (data-ctx on the row) opens the same menu as its ⋯. */
function ctxBuilder(el) {
  const { ctx, pid, sid, type } = el.dataset;
  if (ctx === "project") { const p = projectById(pid); return p ? () => projectItems(projectById(pid) || p) : null; }
  if (ctx === "inbox") return inboxItems;
  if (ctx === "type") { const t = projTypes(pid).find((x) => x.name === type); return t ? () => subfolderItems(pid, projTypes(pid).find((x) => x.name === type) || t) : null; }
  if (ctx === "meeting") { const found = findMeeting(sid); return found ? () => meetingItems(found.info, pid, null) : null; }
  return null;
}
function initContextMenus() {
  document.addEventListener("contextmenu", (ev) => {
    if (DRAG.press || DRAG.active) return;  // initDrag() swallows it
    const target = ev.target instanceof Element ? ev.target : null;
    if (!target) return;
    if (target.closest(".menu")) { ev.preventDefault(); return; }
    const el = target.closest("[data-ctx]");
    const build = el && ctxBuilder(el);
    if (!build) return;  // anywhere else the normal menu (copy, paste in fields) stays
    ev.preventDefault();
    let x = ev.clientX, y = ev.clientY;
    if (!x && !y) { const r = el.getBoundingClientRect(); x = r.left + 28; y = r.bottom - 4; }  // from the keyboard (menu key)
    showMenu(null, build, { x, y }, el.querySelector("button"));
  });
}

// ---------------------------------------------------------------- top bar: recording controls and status strip

function renderTop() {
  const r = S.status.recorder || {}, st = r.state || "idle";
  const active = st === "starting" || st === "recording" || st === "stopping";
  const proj = curProject();
  const ctype = proj ? curType() : "";
  $("proj-name").textContent = S.sel === "inbox" ? INBOX_NAME : proj ? proj.name + (ctype ? "  ›  " + ctype : "") : "";
  renderTypeSelect(proj, active);
  renderFolderButton(proj, active);
  $("btn-projmenu").hidden = !proj && S.sel !== "inbox";
  const archivedSel = !!(proj && proj.archived);
  const noProject = !S.sel || archivedSel;
  const dock = $("dock");
  dock.dataset.state = st === "recording" ? (r.paused ? "paused" : "recording") : st;
  $("rec-title").disabled = noProject || active;
  const rec = $("btn-rec");
  rec.disabled = noProject || st === "starting" || st === "stopping";
  $("btn-rec-label").textContent = st === "starting" ? "Starting..." : "Record";
  rec.title = archivedSel ? "This project is archived. Restore it to record into it." : "";
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
  renderPeopleBtn();
  renderStrip();
  updateButtons();
}

// "Existing folder": record the next meeting into a folder that is already there (agenda and
// notes made before the meeting). Steno's files go next to the user's own.
const recFolderOf = (pid) => (S.recFolder && S.recFolder.pid === pid ? S.recFolder : null);
function renderFolderButton(proj, active) {
  const btn = $("rec-folder");
  btn.hidden = !proj || proj.archived || !pickerAvailable();
  btn.disabled = active;
  if (btn.hidden) return;
  const f = recFolderOf(proj.id);
  const key = f ? f.folder : "-";
  if (btn.dataset.key !== key) {
    btn.dataset.key = key;
    btn.classList.toggle("on", !!f);
    btn.replaceChildren(icon("folderOpen"), h("span", { class: "dt-name", text: f ? f.name : "Existing folder" }));
  }
  btn.title = f
    ? `Recording into ${f.name}${f.type ? ` (${f.type})` : ""}: Steno's transcript goes next to your files. Click to change.`
    : "Record into a meeting folder you already made (agenda, notes). Steno's files go next to yours.";
  btn.setAttribute("aria-label", f ? `Recording into ${f.name}` : "Record into an existing folder");
}
async function chooseRecFolder() {
  const proj = curProject();
  if (!proj) return;
  const type = recType(proj.id);
  const path = await pickFolder(type ? "type" : "project", proj.id, type || null);
  if (!path) return;
  const res = await api("POST", `/api/projects/${encodeURIComponent(proj.id)}/folder`, { folder: path });
  S.recFolder = { pid: proj.id, ...res };
  setRecType(proj.id, res.type || "");
  renderSide(); renderTop();
  if (S.meeting === "new") renderFeed();
}
function recFolderClick() {
  const proj = curProject();
  if (!proj) return;
  if (!recFolderOf(proj.id)) { guarded(chooseRecFolder); return; }
  showMenu($("rec-folder"), () => [
    { header: `Recording into ${S.recFolder.name}` },
    { label: "Choose another folder...", icon: "folderOpen", onclick: () => guarded(chooseRecFolder) },
    { label: "Use a new folder instead", sub: "YYYY-MM-DD title, made by Steno", icon: "folderPlus", onclick: () => { S.recFolder = null; renderTop(); } }]);
}

// Subfolder of the next meeting: a button in the dock that opens a menu (a native <select> drops a
// white list on Windows that ignores the theme).
function renderTypeSelect(proj, active) {
  const btn = $("rec-type");
  const types = proj && !proj.archived ? proj.types || [] : [];
  btn.hidden = !types.length;
  btn.disabled = active;
  if (!types.length) return;
  const cur = recType(proj.id);
  const key = [proj.id, proj.name, cur, ...types.map((t) => t.name + (t.missing ? "!" : ""))].join("|");
  if (btn.dataset.key === key) return;
  btn.dataset.key = key;
  const here = types.find((t) => t.name === cur);
  btn.replaceChildren(icon(here && here.missing ? "alert" : "folder"), h("span", { class: "dt-name", text: cur || "No subfolder" }), icon("chevron"));
  btn.title = cur ? `Saved in: ${proj.name} › ${cur}${here && here.missing ? " (its folder is missing: kept in Steno)" : ""}. Click to change.`
    : `Saved in ${proj.name} without a subfolder (kept in Steno, no folder on disk). Click to change.`;
}

function typeMenuItems() {
  const proj = curProject();
  if (!proj) return [];
  const cur = recType(proj.id);
  const pick = (name) => () => {
    setRecType(proj.id, name);
    if (S.recFolder && S.recFolder.type !== name) S.recFolder = null;  // the folder decides the subfolder
    renderSide(); renderTop(); if (S.meeting === "new") renderFeed();
  };
  return [{ header: "Save the next meeting in" },
    ...(proj.types || []).map((t) => ({ label: t.name, sub: t.missing ? "Folder missing: kept in Steno" : t.folder || null,
      icon: t.name === cur ? "check" : t.missing ? "alert" : "folder", onclick: pick(t.name) })),
    "-",
    { label: "No subfolder", sub: "Kept in Steno, no folder on disk", icon: cur ? "folder" : "check", onclick: pick("") }];
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
  if (!S.sel || (curProject() || {}).archived) return;
  remember("steno-opts", JSON.stringify(S.opts));
  await ensurePeople(0);  // the chosen people must still exist
  const body = { project: S.sel, mic: S.opts.mic, loopback: S.opts.loop, keep_audio: S.opts.keep, auto_title: S.autoTitle };
  const chosen = recPeople();  // who is in the meeting (per subfolder, see the People button)
  if (chosen.ids.length) body.participants = chosen.ids;
  if (chosen.n) body.speakers = chosen.n;
  const type = S.sel !== "inbox" ? recType(S.sel) : "";
  if (type) body.type = type;
  const title = $("rec-title").value.trim();
  if (title) body.title = title;
  const folder = S.sel !== "inbox" ? recFolderOf(S.sel) : null;
  if (folder) { body.folder = folder.folder; delete body.type; }
  S.stick = true;
  S.meeting = "new"; S.pendingRec = true;
  S.pendingFrom = (S.status.recorder || {}).session || null;  // never "find" the previous recording
  renderSide(); renderFeed();
  const res = await guarded(() => api("POST", "/api/record/start", body));
  if (res) {
    if (res.recorder) S.status.recorder = res.recorder;
    $("rec-title").value = "";
    S.recFolder = null;  // per meeting, like the title
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
  m.detail.participants = m.detail.participants || { profiles: [], count: null };
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
  const proj = S.sel !== "inbox" ? curProject() : null;
  const type = proj ? recType(S.sel) : "";
  const where = projectName(S.sel) + (type ? " · " + type : "");
  if (proj && proj.archived && !S.pendingRec) {
    return h("div", { class: "newcard" },
      h("div", { class: "newicon" }, icon("archive")),
      h("h2", { text: `${proj.name} is archived` }),
      h("p", { text: "Restore the project to record new meetings into it." }),
      h("div", { class: "newcard-add" }, h("button", { class: "btn primary big", type: "button", text: "Restore", onclick: () => guarded(() => archiveProject(proj, false)) })));
  }
  // a project without subfolders: the big first step is adding one (a folder where its meetings are saved)
  const first = !S.pendingRec && proj && !(proj.types || []).length;
  return h("div", { class: "newcard" },
    h("div", { class: "newicon" }, S.pendingRec ? h("span", { class: "spinner big" }) : icon(first ? "folderPlus" : "mic")),
    h("h2", { text: S.pendingRec ? "Starting the recording" : `New meeting in ${where}` }),
    h("p", { text: S.pendingRec ? "Loading the speech models. This takes a few seconds the first time."
      : first ? "Add a subfolder to save its meetings in a folder on your computer, e.g. Board or 1-1. Without one, a meeting stays in Steno."
        : "Add a title below if you like, then press Record. Transcription runs on this computer." }),
    first ? h("div", { class: "newcard-add" },
      h("button", { class: "btn primary big", type: "button", onclick: () => guarded(() => addSubfolder(proj.id)) }, icon("folderPlus"), h("span", { text: "Add subfolder" })),
      h("p", { class: "nc-or", text: "or press Record below to keep this meeting in Steno" })) : null);
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

function saveVisible(info) {
  const r = S.status.recorder || {};
  const live = info.recording || (r.session === info.session && r.state && r.state !== "idle" && r.state !== "error");
  return info.folder === "" && !live;
}
function saveButton(info) {
  const tip = "Save this meeting into a folder on your computer: a subfolder of a project, or a folder you choose";
  const b = h("button", { class: "act", type: "button", title: tip }, icon("move"), h("span", { text: "Save to..." }));
  b.addEventListener("click", () => guarded(() => saveMeetingTo(info)));
  return b;
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
    titleOf(info) ? null : act("title", "Auto title", "heading", "Let Claude name this meeting from what was said (sends transcript text only)"),
    // no folder on disk (kept in Steno, or in the Inbox): put it in a subfolder or an existing folder
    saveVisible(info) ? saveButton(info) : null);
  m.jobEl = h("div", { class: "mjob", hidden: true });
  m.docsEl = h("div", { class: "docs" });
  m.linesEl = h("div", { class: "lines" });
  const linesHead = h("div", { class: "lines-head" },
    h("span", { text: "Transcript" }),
    copyButton("Copy transcript", () => meetingTranscript(m.info), "Transcript"));
  // head + actions + participants stay pinned while the transcript scrolls (CSS: .mstick is sticky)
  m.peopleEl = h("div", { class: "mpeople", hidden: true });
  m.stickEl = h("div", { class: "mstick" },
    h("header", { class: "mhead" },
      h("div", { class: "mhead-text" }, titleBtn,
        h("div", { class: "mmeta" }, h("span", { text: niceDate(info.started_at) }), m.durEl,
          info.archived ? h("button", { class: "badge-arch", type: "button", title: "Archived: hidden in the list. Click to unarchive.",
            onclick: () => guarded(() => archiveMeeting(info, S.sel, false)) }, icon("archive"), "Archived") : null)),
      more),
    bar, m.peopleEl);
  m.el = h("section", { class: "meeting", "data-session": sid }, m.stickEl, m.jobEl, m.docsEl, linesHead, m.linesEl);
  renderDocs(m);
  renderLines(m);
  initPeople(m);
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

// ---- participants: one chip per speaker in the pinned block (dot, name, number of lines), then the people
// chosen for the meeting who have not spoken yet (dimmed, 0 lines), "3 of 4" and "+ Person" at the end

/* Chips of the shown transcript: speakers in order of first appearance (so colours match the lines), then
   expected people who have not spoken. [{kind: "speaker" | "waiting", key, name, count, seg, unknown, pid}].
   `seg` is a line to name from (the first one that can be named), else null. `unknown`: not named yet
   ("Speaker 3"). `pid`: the voice profile id when known. Lines are grouped by the name they show, like the
   colours. Lines without a name are left out. */
function peopleOf(m) {
  const by = new Map();
  for (const seg of m.detail.segments) {
    const name = String(seg.speaker || "").trim();
    if (!name) continue;
    let p = by.get(name);
    if (!p) {
      const n = clusterNo(seg.speaker_id);
      p = { kind: "speaker", key: name, name, count: 0, seg: null, unknown: n !== null && name === `Speaker ${n}`, pid: null };
      by.set(name, p);
      speakerColor(m, name);
    }
    p.count += 1;
    if (!p.seg && canName(m, seg)) p.seg = seg;
    if (!p.pid && /^P:[0-9a-f]{8}$/.test(seg.speaker_id || "")) p.pid = seg.speaker_id.slice(2);
  }
  const list = [...by.values()];
  const part = m.detail.participants || { profiles: [] };
  for (const id of part.profiles) {
    const person = personById(id);
    if (!person) continue;  // deleted since, or the People list is not loaded yet
    if (list.some((p) => p.pid === id || (p.kind === "speaker" && !p.unknown && sameName(p.name, person.name)))) continue;
    list.push({ kind: "waiting", key: "w:" + id, name: person.name, count: 0, seg: null, unknown: false, pid: id });
  }
  return list;
}

function personChip(m, p) {
  const count = h("span", { class: "pc-n", text: String(p.count) });
  const kids = [h("i", { class: "pc-dot" }), h("span", { class: "pc-name", text: p.name })];
  if (p.unknown) kids.push(h("span", { class: "pc-q", "aria-hidden": "true", text: "?" }));
  kids.push(h("span", { class: "pc-sep", "aria-hidden": "true", text: "·" }), count);
  const lines = `${p.count} line${p.count === 1 ? "" : "s"}`;
  let chip;
  if (p.kind === "waiting") {
    chip = h("span", { class: "pchip waiting", title: `${p.name} is in this meeting but has not been heard yet` }, ...kids,
      h("button", { class: "pc-x", type: "button", title: `Take ${p.name} out of this meeting`, "aria-label": `Remove ${p.name}`,
        onclick: () => guarded(() => changePeople(m, { remove: p.pid })) }, icon("x")));
  } else if (p.seg) {
    chip = h("button", { class: "pchip nameable" + (p.unknown ? " unknown" : ""), type: "button",
      title: p.unknown ? `${p.name} is not named yet (${lines}). Click to say who this is` : `${p.name}: ${lines}. Click to rename` }, ...kids);
    chip.addEventListener("click", () => openNamer(m, m.peopleEl, p.seg));
  } else {
    chip = h("span", { class: "pchip" + (p.unknown ? " unknown" : ""), title: `${p.name}: ${lines}` }, ...kids);
  }
  chip.style.setProperty("--c", speakerColor(m, p.name));
  chip._count = count; chip._p = p;
  return chip;
}

function initPeople(m) {
  if (m.peopleRO) m.peopleRO.disconnect();
  m.peopleKeys = ""; m.peopleOpen = !!m.peopleOpen;
  updatePeople(m);
  const box = m.peopleEl;  // names of the chosen people come from the People list: refresh it once, then redraw
  ensurePeople().then(() => { if (m.peopleEl === box && box.isConnected) updatePeople(m); });
  // chips that do not fit are folded into "+N": measured again whenever the row's width changes
  if (typeof ResizeObserver === "function") {
    let w = -1;
    m.peopleRO = new ResizeObserver(() => {
      const nw = m.peopleEl.offsetWidth;  // layout width: a running entry animation must not change it
      if (nw === w) return;
      w = nw; fitPeople(m);
    });
    m.peopleRO.observe(m.peopleEl);
  }
}

function updatePeople(m) {
  const box = m.peopleEl;
  if (!box) return;
  const part = m.detail.participants || { profiles: [], count: null };
  const list = peopleOf(m);
  const spoken = list.filter((p) => p.kind === "speaker").length;
  box.hidden = !(list.length || part.profiles.length || part.count || m.info.recording);
  const keys = JSON.stringify([list.map((p) => [p.kind, p.key, !!p.seg, p.unknown]), part.count, spoken]);
  if (keys === m.peopleKeys) {  // same chips: only the counts moved
    const chips = [...box.querySelectorAll(".mp-chips .pchip:not(.more)")];
    list.forEach((p, i) => { if (chips[i]) { chips[i]._count.textContent = String(p.count); chips[i]._p = p; } });
  } else {
    m.peopleKeys = keys;
    const more = h("button", { class: "pchip more", type: "button", hidden: true, onclick: () => { m.peopleOpen = !m.peopleOpen; fitPeople(m); } });
    const add = h("button", { class: "pchip add", type: "button", "aria-haspopup": "menu", title: "Add someone from People to this meeting" },
      icon("plus"), h("span", { text: "Person" }));
    add.addEventListener("click", () => ensurePeople().then(() => showMenu(add, () => addPersonMenu(m))));
    const cnt = part.count ? h("button", { class: "pchip cnt", type: "button", "aria-haspopup": "menu",
      title: `${spoken} of ${part.count} expected speakers heard. Click to change the expected number`, text: `${spoken} of ${part.count}` }) : null;
    if (cnt) cnt.addEventListener("click", () => ensurePeople().then(() => showMenu(cnt, () => addPersonMenu(m))));
    box.replaceChildren(h("div", { class: "mp-chips" }, ...list.map((p) => personChip(m, p)), more), h("div", { class: "mp-fixed" }, cnt, add));
  }
  fitPeople(m);
}

function fitPeople(m) {
  const box = m.peopleEl;
  if (!box || box.hidden || !box.isConnected) return;
  const wrap = box.querySelector(".mp-chips");
  if (!wrap) return;
  const chips = [...wrap.querySelectorAll(".pchip:not(.more)")], more = wrap.querySelector(".more");
  if (!more || !chips.length) return;
  const rows = box.offsetWidth < 520 ? 1 : 2;  // narrow: one line, wider: two
  const rowOf = (el) => Math.round(el.offsetTop / 8);
  const fits = () => { const base = rowOf(chips[0]); return rowOf(more.hidden ? chips[chips.length - 1] : more) - base < rows; };
  for (const c of chips) c.hidden = false;
  more.hidden = true;
  let hidden = 0;
  if (!fits()) {
    more.hidden = false;
    while (hidden < chips.length - 1) {  // at least one chip stays
      hidden += 1;
      chips[chips.length - hidden].hidden = true;
      more.textContent = `+${hidden}`;
      if (fits()) break;
    }
    // collapsed it shows N more; expanded every chip is shown and the button folds them again
    if (m.peopleOpen) { for (const c of chips) c.hidden = false; more.textContent = "Less"; more.title = "Show fewer"; }
    else { more.title = `${hidden} more speaker${hidden === 1 ? "" : "s"}: click to show all`; }
    more.setAttribute("aria-expanded", String(!!m.peopleOpen));
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
  updatePeople(m);
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
    if (p.archived && p.id !== current) continue;  // nothing is moved into an archived project
    const types = p.types || [];
    options.push({ value: p.id, label: types.length ? `${p.name} (no subfolder)` : p.name });
    for (const t of types) options.push({ value: `${p.id}|${t.name}`, label: `${p.name} › ${t.name}${t.missing ? " (folder missing)" : ""}`, disabled: !!t.missing });
  }
  options.push({ value: "inbox", label: INBOX_NAME });
  const here = current + (info.type ? "|" + info.type : "");
  const split = (to) => { const cut = to.indexOf("|"); return cut < 0 ? { pid: to, type: null } : { pid: to.slice(0, cut), type: to.slice(cut + 1) }; };
  const v = await formModal({ title: "Move meeting",
    text: "Into a subfolder, the meeting folder moves into that folder. Into a project without a subfolder, it is kept in Steno (no folder on disk). Into the Inbox / TBD, it leaves the project.",
    fields: [{ key: "to", label: "Move to", options, value: here }], ok: "Move",
    submit: async (vals) => {
      if (vals.to === here) return null;
      const { pid, type } = split(vals.to);
      await api("POST", `/api/meetings/${encodeURIComponent(info.session)}/move`, { project: pid, type: type || "" });
      return { pid, type };
    } });
  if (!v) return;
  await afterMeetingMoved(info, current, v.pid, v.type, `Moved to ${projectName(v.pid)}${v.type ? " › " + v.type : ""}`);
}

/* Move a meeting by drag: pid is a project id or "inbox", type a subfolder name or null (null = in the
   project itself, without a subfolder). */
async function moveMeetingTo(info, current, pid, type) {
  await api("POST", `/api/meetings/${encodeURIComponent(info.session)}/move`, { project: pid, type: type || "" });
  await afterMeetingMoved(info, current, pid, type, `Moved to ${projectName(pid)}${type ? " › " + type : ""}`);
}

/* After a move or a save: keeps the meeting open if it was open, and refreshes the lists. */
async function afterMeetingMoved(info, current, pid, type, message) {
  toast(message);
  if (S.feed.has(info.session)) S.feed.get(info.session).info.type = type || null;
  const open = S.sel === current && S.meeting === info.session;
  if (!S.expanded.has(pid)) { S.expanded.add(pid); saveExpanded(); }
  await loadProjects();
  const list = await fetchList(pid);  // fresh, so the moved meeting is found when selecting it
  if (S.sel === pid) S.meetings = list;
  if (open) selectMeeting(pid, info.session); else if (S.sel === current) await loadFeed();
}

/* Save to...: for a meeting that has no folder on disk. Pick a subfolder of any project (its folder
   is used), or an existing meeting folder inside one of them. */
async function saveMeetingTo(info) {
  const current = S.sel;
  const groups = S.projects.filter((p) => !p.archived && (p.types || []).length);
  let pick = null, folder = null;  // pick: {pid, name, label} (a subfolder) | {path} (an existing folder)
  const rows = [];
  const res = await modal({
    title: "Save meeting to",
    text: "Steno writes the transcript, summary and to-do into a meeting folder there.",
    ok: "Save here",
    content: (ctx) => {
      ctx.setOk({ disabled: true });
      const mark = () => {
        for (const r of rows) { const on = !!pick && !pick.path && r.pid === pick.pid && r.name === pick.name; r.el.classList.toggle("on", on); r.el.setAttribute("aria-checked", String(on)); }
        ctx.setOk({ disabled: !pick });
      };
      const list = h("div", { class: "pick-list", role: "radiogroup", "aria-label": "Save to" });
      for (const p of groups) {
        list.append(h("div", { class: "pick-h", text: p.name }));
        for (const t of p.types) {
          const el = h("button", { class: "pick-row", type: "button", role: "radio", "aria-checked": "false", disabled: !!t.missing,
            title: t.missing ? t.folder + "\nThis folder is missing." : t.folder },
          icon(t.missing ? "alert" : "folder"), h("span", { class: "pk-name", text: t.name }),
          h("span", { class: "pk-sub", text: t.missing ? "folder missing" : baseName(t.folder) }), icon("check", "pk-check"));
          el.addEventListener("click", () => { pick = { pid: p.id, name: t.name, label: `${p.name} › ${t.name}` }; if (folder) folder.set("", true); mark(); });
          rows.push({ el, pid: p.id, name: t.name });
          list.append(el);
        }
      }
      if (!groups.length) list.append(h("p", { class: "pick-none", text: "No project has a subfolder yet. Add one first, or choose an existing folder below." }));
      folder = folderField({ label: "Or an existing meeting folder", button: "Choose existing folder...", start: "project", ref: current && current !== "inbox" ? current : null,
        onChange: (path) => { pick = path ? { path } : null; mark(); } });
      return [list, folder.el];
    },
    submit: async () => {
      if (!pick) throw new Error("Choose where to save it.");
      const sid = encodeURIComponent(info.session);
      if (pick.path) {
        const r = await api("POST", `/api/meetings/${sid}/save`, { folder: pick.path });
        return { pid: r.project, type: r.type || null, message: `Saved to ${baseName(r.folder) || baseName(pick.path)}` };
      }
      await api("POST", `/api/meetings/${sid}/move`, { project: pick.pid, type: pick.name });
      return { pid: pick.pid, type: pick.name, message: `Saved to ${pick.label}` };
    },
  });
  if (res) await afterMeetingMoved(info, current, res.pid, res.type, res.message);
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
  ghost.style.width = `${Math.round(box.width / zoom())}px`;
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
  d.ghost.style.transform = `translate(${Math.round((d.x - d.ox) / zoom())}px, ${Math.round((d.y - d.oy) / zoom())}px)`;
}

/* A place the meeting can be dropped on, from the pointer position: {pid, type, el} or null. */
function dropTargetAt(x, y) {
  const at = document.elementFromPoint(x, y);
  const el = at && at.closest("#side [data-drop]");
  return el ? { el, pid: el.dataset.drop, type: el.dataset.dropType || null } : null;
}

function canDrop(d, t) {
  if (t && (projectById(t.pid) || {}).archived) return false;  // nothing is moved into an archived project
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
  const open = () => { el.classList.add("open"); el.style.height = `${Math.round(d.rowH / zoom())}px`; };
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
  d.ghost.style.width = `${Math.round(rect.width / zoom())}px`;
  d.ghost.style.transform = `translate(${Math.round(rect.left / zoom())}px, ${Math.round(rect.top / zoom())}px)`;
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

// ---------------------------------------------------------------- who is in the meeting

/* Chosen before recording (the People button in the dock) or changed in the meeting head ("+ Person").
   The server keeps it per meeting in participants.json: live recognition and Finalize then only match
   these people; an empty list means everybody. See docs/app.md. */

const MAX_SPEAKERS = 20;
let peopleStamp = 0, peopleFetch = null;
function ensurePeople(maxAge = 5000) {  // the People list (names for the chips), fetched when older than maxAge ms
  if (peopleStamp && Date.now() - peopleStamp < maxAge) return Promise.resolve(S.people);
  if (!peopleFetch) {
    peopleFetch = api("GET", "/api/people")
      .then((d) => { S.people = d.people; peopleStamp = Date.now(); return S.people; })
      .catch(() => S.people)
      .finally(() => { peopleFetch = null; });
  }
  return peopleFetch;
}
const personById = (id) => S.people.find((p) => p.id === id) || null;
const sameName = (a, b) => String(a).trim().toLowerCase() === String(b).trim().toLowerCase();

/* A small "Speakers - 4 +" control for a menu. `set(n)` gets a number or null (not set); the shown
   value follows the clicks at once, the caller saves it. */
function stepper(start, set) {
  let n = start;
  const val = h("span", { class: "st-val" });
  const minus = h("button", { class: "st-b", type: "button", "aria-label": "Fewer speakers", text: "−" });
  const plus = h("button", { class: "st-b", type: "button", "aria-label": "More speakers", text: "+" });
  const show = () => { val.textContent = n === null ? "any" : String(n); minus.disabled = n === null; plus.disabled = n === MAX_SPEAKERS; };
  minus.addEventListener("click", () => { n = n === null || n <= 1 ? null : n - 1; show(); set(n); });
  plus.addEventListener("click", () => { n = n === null ? 2 : Math.min(MAX_SPEAKERS, n + 1); show(); set(n); });
  show();
  return h("div", { class: "stepper", title: "How many people will speak, guests included. Steno will not invent more voices than this." },
    h("span", { class: "st-l", text: "Speakers" }), minus, val, plus);
}
const menuHint = (text) => ({ node: h("div", { class: "mhint", text }) });

// before recording: chosen people and expected speakers, remembered per subfolder (meeting type)
const recPeopleKey = () => `steno-ppl-${S.sel}-${S.sel === "inbox" ? "" : recType(S.sel)}`;
function recPeople() {
  let v = null;
  try { v = JSON.parse(remember(recPeopleKey()) || "null"); } catch (e) { /* bad prefs */ }
  const ids = v && Array.isArray(v.p) ? v.p.filter((x) => typeof x === "string") : [];
  const n = v && Number.isInteger(v.n) && v.n >= 1 && v.n <= MAX_SPEAKERS ? v.n : null;
  return { ids: peopleStamp ? ids.filter((id) => personById(id)) : ids, n };
}
function setRecPeople(ids, n) {
  remember(recPeopleKey(), ids.length || n ? JSON.stringify({ p: ids, n }) : null);
  renderPeopleBtn();
}

function renderPeopleBtn() {
  const btn = $("btn-people");
  if (!btn || !S.sel) return;
  const { ids, n } = recPeople();
  const names = ids.map((id) => (personById(id) || {}).name).filter(Boolean);
  const r = S.status.recorder || {}, st = r.state || "idle";
  btn.disabled = st === "starting" || st === "recording" || st === "stopping" || (curProject() || {}).archived === true;
  btn.classList.toggle("on", !!(ids.length || n));
  const badge = $("people-n");
  const text = n ? `${ids.length}/${n}` : ids.length ? String(ids.length) : "";
  badge.hidden = !text;
  if (badge.textContent !== text) badge.textContent = text;
  btn.title = names.length || n
    ? `In this meeting: ${names.join(", ") || "nobody chosen"}${n ? `; ${n} speakers expected` : ""}. Click to change.`
    : "Who is in this meeting? Choose people so Steno only has to tell them apart (optional).";
}

function recPeopleMenu() {
  const cur = recPeople();
  const toggle = (id) => () => {
    const now = recPeople();
    setRecPeople(now.ids.includes(id) ? now.ids.filter((x) => x !== id) : [...now.ids, id], now.n);
  };
  return [
    { header: "People in this meeting" },
    ...(S.people.length
      ? S.people.map((p) => ({ label: p.name, checked: cur.ids.includes(p.id), keep: true, onclick: toggle(p.id) }))
      : [{ label: "No voice profiles yet", sub: "Add people in the People list first", disabled: true }]),
    "-",
    { node: stepper(cur.n, (n) => setRecPeople(recPeople().ids, n)) },
    menuHint("Only the chosen people are recognised. Choose nobody to match everyone."),
  ];
}

// in the meeting head
async function changePeople(m, body) {
  const res = await api("POST", `/api/sessions/${encodeURIComponent(m.info.session)}/participants`, body);
  m.detail.participants = res.participants;
  updatePeople(m);
}

async function addPerson(m, id) {
  const part = m.detail.participants || { profiles: [] };
  if (part.profiles.length) return changePeople(m, { add: id });
  // The list was "everybody". Starting one narrows recognition to it, so the people already
  // recognised in this meeting go in too (or they would stop being matched).
  const known = peopleOf(m).filter((p) => p.kind === "speaker" && !p.unknown)
    .map((p) => personById(p.pid) || S.people.find((x) => sameName(x.name, p.name))).filter(Boolean).map((x) => x.id);
  return changePeople(m, { profiles: [...new Set([...known, id])] });
}

function addPersonMenu(m) {
  const part = m.detail.participants || { profiles: [], count: null };
  const inList = new Set(part.profiles);
  const free = S.people.filter((p) => !inList.has(p.id));
  const items = [{ header: "Add a person to this meeting" }];
  if (!S.people.length) items.push({ label: "No voice profiles yet", sub: "Add people in the People list first", disabled: true });
  else if (!free.length) items.push({ label: "Everyone is already here", disabled: true });
  else items.push(...free.map((p) => ({ label: p.name, icon: "plus", onclick: () => guarded(() => addPerson(m, p.id)) })));
  items.push("-", { node: stepper(part.count || null, (n) => guarded(() => changePeople(m, { count: n }))) });
  if (!part.profiles.length) items.push(menuHint("Until someone is added, every saved voice can be recognised. Adding people limits it to them."));
  return items;
}

// ---------------------------------------------------------------- naming speakers

async function openNamer(m, row, seg) {
  document.querySelectorAll(".namer").forEach((n) => n.remove());
  const n = clusterNo(seg.speaker_id);
  const input = h("input", { class: "text", type: "text", list: "people-names", maxlength: "80", placeholder: `Name for Speaker ${n}`, "aria-label": "Speaker name", autocomplete: "off" });
  const save = h("input", { type: "checkbox" });
  const saveText = h("span", { text: "Remember voice" });
  let touched = false;  // the user decided about the box: stop choosing for them
  // An existing person: the voice is added to their profile (Remember voice), the name stays spelled as theirs.
  const sync = () => {
    const match = S.people.find((p) => sameName(p.name, input.value));
    saveText.textContent = match ? `Add voice to ${match.name}` : "Remember voice";
    if (match && !touched) save.checked = true;
  };
  input.addEventListener("input", sync);
  save.addEventListener("change", () => { touched = true; });
  // people chosen for this meeting who have not been heard yet: one click puts their name in
  const waiting = peopleOf(m).filter((p) => p.kind === "waiting");
  const picks = waiting.length ? h("div", { class: "namer-pick" }, h("span", { class: "np-l", text: "Is it" }),
    ...waiting.map((p) => h("button", { class: "pchip pick", type: "button", title: `Name this speaker ${p.name} and add the voice to their profile`,
      onclick: () => { input.value = p.name; touched = false; sync(); input.focus(); } }, h("span", { class: "pc-name", text: p.name })))) : null;
  const form = h("form", { class: "namer", onsubmit: async (ev) => {
    ev.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const body = { speaker: seg.speaker_id, name, save_profile: save.checked };
    if (m.detail.speaker_space === "final") body.view = "final";  // finalize runs again afterwards
    const res = await guarded(() => api("POST", `/api/sessions/${encodeURIComponent(m.info.session)}/name`, body));
    if (res) { toast(res.message || "Speaker named"); await guarded(() => refreshMeeting(m.info.session)); }
  } }, input, picks,
  h("label", { title: "Store a voice profile on this computer so future meetings recognise this person. An existing person gets this voice added." }, save, saveText),
  h("button", { class: "btn small primary", type: "submit", text: "Save" }),
  h("button", { class: "btn small ghost", type: "button", text: "Cancel", onclick: () => form.remove() }));
  row.after(form);
  input.focus();
  input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") form.remove(); });
  try {
    const d = await api("GET", "/api/people");
    S.people = d.people; peopleStamp = Date.now();
    $("people-names").replaceChildren(...d.people.map((p) => h("option", { value: p.name })));
    sync();
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
  // the pinned head gets a hairline underneath only while it covers the transcript
  const m = S.feed.get(S.meeting);
  if (m && m.stickEl && m.el && m.el.isConnected) {
    m.stickEl.classList.toggle("stuck", m.stickEl.getBoundingClientRect().top > m.el.getBoundingClientRect().top + 0.5);
  }
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
  peopleStamp = Date.now();
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
  } else if (name === "participants") {
    const m = S.feed.get(data.session);
    if (m && m.detail) {
      api("GET", `/api/sessions/${encodeURIComponent(data.session)}/participants`)
        .then((d) => { m.detail.participants = d.participants; updatePeople(m); }).catch(() => {});
    }
  } else if (name === "names") {
    peopleStamp = 0;  // a voice profile may have been made or renamed
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

let lastState = null, lastSession = null, lastError = null, lastNotice = null;
function onStatus() {
  const r = S.status.recorder || {};
  if (r.state === "error" && r.error && r.error !== lastError) toast(r.error, "error");
  lastError = r.state === "error" ? r.error : null;
  // e.g. the subfolder's folder was missing: shown once per recording (and not again when Steno is opened later)
  const noticeKey = r.notice && r.state && r.state !== "idle" && r.state !== "error" ? `${r.session || ""}|${r.notice}` : null;
  if (noticeKey && noticeKey !== lastNotice) toast(r.notice, "warn");
  lastNotice = noticeKey;
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
  ["default", "Default", ["#bdbdbd", "#5fcf86"], ["#3a3a3a", "#1f8a4c"]],
  ["mono", "Mono", ["#bdbdbd", "#4a4a4a"], ["#3a3a3a", "#1c1c1c"]],
  ["teal", "Teal", ["#aec3b0", "#356a7e"], ["#124559", "#598392"]],
  ["wine", "Wine", ["#c4474d", "#a01820"], ["#800e13", "#640d14"]],
  ["vintage", "Vintage", ["#bcabae", "#716969"], ["#716969", "#2d2e2e"]],
  ["deepsea", "Deep sea", ["#1b98e0", "#006494"], ["#006494", "#247ba0"]],
];
const zoom = () => LOOK.scale / 100;  // CSS zoom of the app: pointer and rect coordinates are in screen px
const LOOK = { theme: "system", palette: "default", intensity: "subtle", scale: 100, transparent: true };
const systemLight = window.matchMedia("(prefers-color-scheme: light)");
function applyLook(s) {
  if (s && typeof s === "object") {
    if (["system", "dark", "light"].includes(s.theme)) LOOK.theme = s.theme;
    if (PALETTES.some((p) => p[0] === s.palette)) LOOK.palette = s.palette;
    if (["subtle", "medium", "strong"].includes(s.intensity)) LOOK.intensity = s.intensity;
    if ([90, 100, 110, 125, 140].includes(s.ui_scale)) LOOK.scale = s.ui_scale;
    if (typeof s.transparent === "boolean") LOOK.transparent = s.transparent;
  }
  const light = LOOK.theme === "light" || (LOOK.theme === "system" && systemLight.matches);
  const root = document.documentElement;
  root.classList.toggle("light", light);
  root.classList.toggle("solid", !LOOK.transparent);
  root.dataset.palette = LOOK.palette;
  root.dataset.intensity = LOOK.intensity;
  root.style.setProperty("--ui-zoom", String(zoom()));
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
  $("btn-addmenu").addEventListener("click", () => guarded(newProject));
  $("empty-new").addEventListener("click", () => guarded(newProject));
  $("btn-projmenu").addEventListener("click", () => showMenu($("btn-projmenu"), headerItems));
  initContextMenus();
  initDrag();
  watchDay();
  $("nav-people").addEventListener("click", showPeople);
  $("nav-settings").addEventListener("click", showSettings);
  $("settings-close").addEventListener("click", leaveSettings);
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
  $("btn-people").addEventListener("click", () => ensurePeople().then(() => showMenu($("btn-people"), recPeopleMenu)));
  $("rec-folder").addEventListener("click", recFolderClick);
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
    ensurePeople().then(() => renderPeopleBtn());
  });
  connect();
}

init();
