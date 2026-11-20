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
  inbox: "M3 13h5l1.5 3h5l1.5-3h5M5.5 5h13L21 13v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5z",
  chevron: "M6 9l6 6 6-6",
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

// Speaker colours: muted, readable on dark glass; stable per name across meetings.
// muted: low saturation, so names read as tints, not as signal colours
const HUES = ["#9eaccb", "#a5b9a2", "#b9a8c6", "#c9b893", "#97b4b6", "#c3a19f", "#b3ab9e", "#a9aed0"];
function speakerColor(name) {
  let x = 0;
  for (const ch of String(name || "")) x = (x * 31 + ch.codePointAt(0)) >>> 0;
  return HUES[x % HUES.length];
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
  const el = h("div", { class: "toast" + (kind ? " " + kind : "") }, icon(kind === "error" ? "alert" : "check"), h("span", { text: String(text) }));
  const dismiss = () => { if (el.classList.contains("out")) return; el.classList.add("out"); setTimeout(() => el.remove(), 230); };
  el.addEventListener("click", dismiss);
  $("toasts").append(el);
  while ($("toasts").children.length > 4) $("toasts").firstChild.remove();
  setTimeout(dismiss, kind === "error" ? 9000 : 4500);
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
  if (S.sel) S.expanded.add(S.sel);
  const ids = new Set([...S.projects.map((p) => p.id), ...(S.inbox > 0 ? ["inbox"] : [])]);
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

function selectProject(id, meeting) {
  const same = S.sel === id;
  S.view = "project"; S.sel = id; S.meeting = meeting || null; S.unseen = 0;
  if (!same) { S.feed.clear(); S.meetings = S.lists.get(id) || []; S.unknown.clear(); }
  if (!S.expanded.has(id)) { S.expanded.add(id); saveExpanded(); }
  remember("steno-project", id);
  if (S.meeting !== "new" && S.lists.has(id)) pickMeeting();  // else loadFeed picks once the list is in
  showView(); renderSide(); renderTop(); renderFeed();
  guarded(loadFeed);
}

const selectMeeting = (pid, sid) => selectProject(pid, sid);

function newMeeting(pid) {
  selectProject(pid, "new");
  setTimeout(() => { if (!$("rec-title").disabled) $("rec-title").focus(); }, 60);
}

/* Which meeting to open: the one asked for, else the live one, else the latest, else "new". */
function pickMeeting() {
  const list = S.meetings, r = S.status.recorder || {};
  if (S.pendingRec && r.session && list.some((i) => i.session === r.session)) {
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
  ["auto_clean", "Clean up automatically", "After a recording stops, Claude fixes misheard words and punctuation."],
  ["auto_summary", "Summary automatically", "Then writes summary.md in the meeting folder."],
  ["auto_todo", "To-do automatically", "Then writes todo.md with explicit commitments."],
];

async function loadSettings() {
  let data;
  try { data = await api("GET", "/api/settings"); } catch (e) { if (!missing(e)) throw e; data = { settings: {} }; }
  const s = data.settings || {};
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
      h("label", { class: "set-row", for: "set-transparent" },
        h("div", { class: "pn" }, h("div", { text: "Transparent background" }), h("div", { class: "sub", text: "Your wallpaper shows softly through the window. Off: solid background." })), glass)));
  const group = h("div", { class: "group" });
  page.append(h("h2", { class: "set-h", text: "After a meeting" }),
    h("p", { class: "set-hint", text: "Runs in this order once you press Stop. Each step uses Claude (Haiku) and takes about 10-30 s. Off: use the buttons on each meeting." }),
    group);
  for (const [key, label, sub] of AUTO) {
    const box2 = h("input", { type: "checkbox", class: "switch", id: `set-${key}` });
    box2.checked = !!s[key];
    box2.addEventListener("change", async () => {
      const res = await guarded(() => api("POST", "/api/settings", { [key]: box2.checked }));
      if (!res) box2.checked = !box2.checked;
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
      kv("Data", "Stays on this computer")));
}

function sideMeetings(id) {
  const r = S.status.recorder || {};
  const here = S.view === "project" && S.sel === id;
  const every = (S.lists.get(id) || []).slice().reverse();  // newest first
  const list = every.filter((i) => !i.archived);
  const archived = every.filter((i) => i.archived);
  const all = S.showAll.has(id);
  const shown = all ? list : list.slice(0, SIDE_MEETINGS);
  const items = [];
  if (here && S.meeting === "new") {
    items.push(h("div", { class: "mitem active draft" }, h("span", { class: "mi-title", text: S.pendingRec ? "Starting..." : "New meeting" })));
  }
  const row = (info) => {
    const live = info.recording || (r.session === info.session && r.state && r.state !== "idle" && r.state !== "error");
    const active = here && S.meeting === info.session;
    const more = h("button", { class: "mmore", type: "button", title: "Meeting actions", "aria-label": "Meeting actions", "aria-haspopup": "menu" }, icon("more"));
    more.addEventListener("click", (ev) => { ev.stopPropagation(); showMenu(more, () => meetingItems(info, id, null)); });
    return h("div", { class: "mrow" + (active ? " active" : "") + (info.archived ? " archived" : "") },
      h("button", { class: "mitem" + (active ? " active" : ""), type: "button",
        title: info.title || "Untitled meeting", onclick: () => selectMeeting(id, info.session) },
      h("span", { class: "mi-title" + (info.title ? "" : " untitled"), text: info.title || "Untitled meeting" }),
      live ? h("i", { class: "live-dot", title: "Recording" }) : h("span", { class: "mi-date", text: niceDate(info.started_at).replace(" · ", " ") })),
      more);
  };
  for (const info of shown) items.push(row(info));
  if (list.length > SIDE_MEETINGS) {
    items.push(h("button", { class: "mitem more", type: "button", text: all ? "Show fewer" : `Show all ${list.length}`,
      onclick: () => { if (all) S.showAll.delete(id); else S.showAll.add(id); renderSide(); } }));
  }
  if (!items.length && S.lists.has(id)) items.push(h("div", { class: "mitem none", text: "No meetings yet" }));
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
    live ? h("i", { class: "live-dot", title: "Recording" }) : open ? null : h("span", { class: "pcount", text: String(count) }));
  const add = canRecord ? h("button", { class: "padd", type: "button", title: `New meeting in ${name}`, "aria-label": `New meeting in ${name}`, onclick: () => newMeeting(id) }, icon("plus")) : null;
  return h("div", { class: "pnode" + (open ? " open" : "") + (here ? " here" : "") },
    h("div", { class: "pitem prow" + (here && !open ? " active" : "") }, chev, main, add),
    open ? sideMeetings(id) : null);
}

function renderSide() {
  const live = recProjectId();
  $("plist").replaceChildren(...S.projects.map((p) => projectNode(p.id, p.name, p.meetings, true, live === p.id)));
  $("inbox-slot").replaceChildren(...(S.inbox > 0 ? [projectNode("inbox", "Inbox", S.inbox, false, live === "inbox")] : []));
  $("nav-people").classList.toggle("active", S.view === "people");
  $("nav-settings").classList.toggle("active", S.view === "settings");
  $("people-count").textContent = S.people.length ? String(S.people.length) : "";
  const elsewhere = live && (S.view !== "project" || live !== S.sel);
  document.querySelectorAll(".menu-btn").forEach((b) => b.classList.toggle("badge", !!elsewhere));
  renderSideFoot();
}

function renderSideFoot() {
  const foot = $("side-foot");
  foot.classList.toggle("off", !S.connected);
  foot.replaceChildren(h("i", { class: "state" }),
    h("span", { text: S.connected ? "On this computer" : "Reconnecting..." }));
  foot.title = S.connected ? "Everything runs locally. Only Clean up, Summary and To-do send transcript text to Claude." : "";
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
  $("proj-name").textContent = S.sel === "inbox" ? "Inbox" : proj ? proj.name : "";
  $("btn-projmenu").hidden = !proj;
  const noProject = !S.sel;
  const dock = $("dock");
  dock.dataset.state = st === "recording" ? (r.paused ? "paused" : "recording") : st;
  $("rec-title").disabled = noProject || active;
  const rec = $("btn-rec");
  rec.disabled = noProject || st === "starting" || st === "stopping";
  $("btn-rec-label").textContent = st === "starting" ? "Loading models" : "Record";
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
  if ($("btn-src").dataset.key !== srcKey) {
    $("btn-src").dataset.key = srcKey;
    $("btn-src").replaceChildren(icon("mic", on.mic ? "" : "off"), icon("system", on.loop ? "" : "off"));
  }
  $("btn-src").title = `Audio sources: ${[on.mic && "microphone", on.loop && "system audio"].filter(Boolean).join(" + ") || "none"}${on.keep ? ", keeping audio" : ""}`;
  $("empty").hidden = !(S.view === "project" && !S.sel && S.projects.length === 0 && S.inbox === 0);
  $("feed").parentElement.hidden = !$("empty").hidden;
  dock.hidden = !$("empty").hidden;
  renderStrip();
  updateButtons();
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
    { label: "Keep audio", sub: "Raw audio in the meeting folder, needed for a final transcript", icon: "keep", checked: S.opts.keep, keep: true, onclick: t("keep") },
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
    s.style.setProperty("--c", speakerColor(r.speaker));
    out.push(s);
  } else out.push(h("span", { text: word }));
  const rp = recProjectId();
  if (rp && (S.view !== "project" || rp !== S.sel)) {
    out.push(h("span", { text: "·" }), h("button", { class: "link", type: "button", text: "in " + projectName(rp), onclick: () => selectProject(rp) }));
  }
  $("live-status").replaceChildren(...out);
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
  const body = { project: S.sel, mic: S.opts.mic, loopback: S.opts.loop, keep_audio: S.opts.keep };
  const title = $("rec-title").value.trim();
  if (title) body.title = title;
  S.stick = true;
  S.meeting = "new"; S.pendingRec = true;
  renderSide(); renderFeed();
  const res = await guarded(() => api("POST", "/api/record/start", body));
  if (res) {
    if (res.recorder) S.status.recorder = res.recorder;
    $("rec-title").value = "";
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
  if (!S.sel) { feed.append(h("p", { class: "hint", text: "No project selected." })); return; }
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
  const where = projectName(S.sel);
  return h("div", { class: "newcard" },
    h("div", { class: "newicon" }, S.pendingRec ? h("span", { class: "spinner big" }) : icon("mic")),
    h("h2", { text: S.pendingRec ? "Starting the recording" : `New meeting in ${where}` }),
    h("p", { text: S.pendingRec ? "Loading the speech models. This takes a few seconds the first time."
      : "Add a title below if you like, then press Record. Transcription runs on this computer." }));
}

function jobLabel(job) {
  return { summary: "Summarizing", todo: "Extracting to-dos", clean: "Cleaning up", finalize: "Finalizing", auto: "After meeting" }[job.kind] || String(job.kind);
}
function jobText(job) {
  const stage = job.stage && job.stage !== "waiting for Claude" ? " · " + job.stage : "";
  return jobLabel(job) + stage + (job.total ? ` ${job.done}/${job.total}` : "");
}

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
  return transcriptText(info.title, info.started_at, segs);
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
  if (pickerAvailable() && pid !== "inbox") {
    items.push({ label: "Show in Explorer", icon: "folder", onclick: () => guarded(async () => {
      const ok = await window.pywebview.api.open_meeting_folder(sid);
      if (!ok) toast("The meeting folder was not found.", "error");
    }) });
  }
  items.push(
    { label: "Copy transcript", icon: "copy", onclick: () => guarded(async () => copyText(await meetingTranscript(info), "Transcript")) },
    { label: "Rename", icon: "pencil", onclick: () => (titleBtn && titleBtn.isConnected ? renameMeeting(S.feed.get(sid), titleBtn) : guarded(() => renameByDialog(info))) },
    { label: "Move to project...", icon: "move", disabled: live || !S.projects.length, onclick: () => guarded(() => moveMeeting(info, pid)) },
    "-",
    info.archived
      ? { label: "Unarchive", sub: "Show it in the list again", icon: "unarchive", onclick: () => guarded(() => archiveMeeting(info, pid, false)) }
      : { label: "Archive", sub: "Hide it in the app; files stay", icon: "archive", disabled: live, onclick: () => guarded(() => archiveMeeting(info, pid, true)) },
    { label: "Delete...", icon: "trash", danger: true, disabled: live || job, title: live ? "Stop the recording first" : job ? "A job is running on it" : null,
      onclick: () => guarded(() => deleteMeeting(info, pid)) });
  return items;
}

function meetingMenu(m, titleBtn) { return meetingItems(m.info, S.sel, titleBtn); }

async function renameByDialog(info) {
  const v = await formModal({ title: "Rename meeting", fields: [{ key: "title", label: "Title", value: info.title || "", placeholder: "Meeting title" }], ok: "Rename" });
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
  const name = info.title || "Untitled meeting";
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
  const titleBtn = h("button", { class: "mtitle" + (info.title ? "" : " untitled"), type: "button", title: "Click to rename", text: info.title || "Untitled meeting", onclick: () => renameMeeting(m, titleBtn) });
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
    act("clean", "Clean up", "wand", "Fix misheard words and punctuation with Claude"));
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
  if (!note) {
    m.linesEl.append(m.info.recording
      ? h("div", { class: "empty-meeting" }, h("span", { class: "spinner" }), "Listening. Lines appear a few seconds after someone speaks.")
      : h("div", { class: "empty-meeting", text: "No transcript lines." }));
  }
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
    title: nameable ? "Name this speaker" : null }, seg.speaker, unknown && nameable ? h("span", { class: "q", text: "Name?" }) : null) : null;
  const p = h("p", { text: seg.text, title: first ? null : clock(seg.t_start) });
  const row = h("div", { class: "seg" + (seg.provisional ? " prov" : "") + (first ? " first" : ""), "data-seq": seg.seq });
  if (first) {
    const color = speakerColor(seg.speaker);
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
  const busy = busyJob();
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
  const input = h("input", { class: "text mtitle-in", type: "text", value: m.info.title || "", placeholder: "Meeting title", maxlength: "80", "aria-label": "Meeting title" });
  let done = false;
  const finish = async (save) => {
    if (done) return; done = true;
    const title = input.value.trim();
    if (save && title && title !== m.info.title) {
      const res = await guarded(() => api("POST", `/api/meetings/${encodeURIComponent(m.info.session)}/rename`, { title }));
      if (res) {
        m.info.title = title; btn.textContent = title; btn.classList.remove("untitled");
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
  const options = S.projects.map((p) => ({ value: p.id, label: p.name }));
  const v = await formModal({ title: "Move to project", text: "The meeting folder is moved to the project's Steno folder.", fields: [{ key: "project", label: "Project", options, value: current }], ok: "Move" });
  if (!v || v.project === current) return;
  await api("POST", `/api/meetings/${encodeURIComponent(info.session)}/move`, { project: v.project });
  toast(`Moved to ${projectName(v.project)}`);
  const open = S.sel === current && S.meeting === info.session;
  if (!S.expanded.has(v.project)) { S.expanded.add(v.project); saveExpanded(); }
  await loadProjects();
  if (open) selectMeeting(v.project, info.session); else if (S.sel === current) await loadFeed();
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
    h("p", { class: "set-hint", text: "Voice profiles are stored encrypted on this computer. Steno uses them to recognise people in new meetings." }));
  box.replaceChildren(page);
  if (!S.people.length) page.append(h("p", { class: "hint", text: "No voice profiles yet. Name a speaker in a meeting and choose \"Remember voice\"." }));
  else page.append(h("div", { class: "group" }, S.people.map(personRow)));
  renderSide();
}

function personRow(p) {
  const row = h("div", { class: "person" });
  row.style.setProperty("--c", speakerColor(p.name));
  const show = () => row.replaceChildren(
    h("span", { class: "avatar", "aria-hidden": "true", text: initials(p.name) }),
    h("div", { class: "pn" }, h("div", { text: p.name }), h("div", { class: "sub", text: `${p.embeddings} voice sample${p.embeddings === 1 ? "" : "s"} · updated ${niceDate(p.updated)}` })),
    h("button", { class: "icon-btn", type: "button", title: "Rename", "aria-label": "Rename", onclick: edit }, icon("pencil")),
    h("button", { class: "icon-btn", type: "button", title: "Delete", "aria-label": "Delete", onclick: () => removePerson(p) }, icon("trash")));
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
  } else if (name === "settings") {
    applyLook(data);
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

// ---------------------------------------------------------------- look: theme and transparency

const LOOK = { theme: "system", transparent: true };
const systemLight = window.matchMedia("(prefers-color-scheme: light)");
function applyLook(s) {
  if (s && typeof s === "object") {
    if (["system", "dark", "light"].includes(s.theme)) LOOK.theme = s.theme;
    if (typeof s.transparent === "boolean") LOOK.transparent = s.transparent;
  }
  const light = LOOK.theme === "light" || (LOOK.theme === "system" && systemLight.matches);
  const root = document.documentElement;
  root.classList.toggle("light", light);
  root.classList.toggle("solid", !LOOK.transparent);
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
  window.addEventListener("pywebviewready", () => { markBrowserMode(); applyLook(); });
  fillIcons();
  initResizer();
  $("btn-addmenu").addEventListener("click", () => showMenu($("btn-addmenu"), () => [
    { label: "New project", sub: "Create a new folder", icon: "folderPlus", onclick: () => guarded(newProject) },
    { label: "Add existing folder", icon: "folder", onclick: () => guarded(addProject) }]));
  $("empty-new").addEventListener("click", () => guarded(newProject));
  $("empty-add").addEventListener("click", () => guarded(addProject));
  $("btn-projmenu").addEventListener("click", () => showMenu($("btn-projmenu"), () => [
    { label: "Forget project", sub: "Files stay on disk", icon: "forget", danger: true, onclick: () => guarded(forgetProject) }]));
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
    try { applyLook((await api("GET", "/api/settings")).settings); } catch (e) { /* defaults */ }
  });
  connect();
}

init();
