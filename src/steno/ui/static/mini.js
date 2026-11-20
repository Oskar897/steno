"use strict";
/* Mini strip: timer, level, pause/resume, stop and hide for the running recording. The desktop
   app shows this window (always on top) while recording, except when Steno itself is in front;
   x hides it until the next recording. Dragging anywhere on it moves the window. */

const $ = (id) => document.getElementById(id);
const url = new URL(location.href);
const token = url.searchParams.get("token") || "";
history.replaceState(null, "", url.pathname);  // keep the token out of the address bar
let rec = {};

const pad2 = (n) => String(n).padStart(2, "0");
function clock(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return (hh ? hh + ":" + pad2(mm) : pad2(mm)) + ":" + pad2(s % 60);
}

async function call(path, method = "GET") {
  const opts = { method, headers: { "X-Steno-Token": token } };
  if (method === "POST") { opts.headers["Content-Type"] = "application/json"; opts.body = "{}"; }
  const res = await fetch(path, opts);
  return res.ok ? res.json() : null;
}

const W = [0.6, 1, 0.8, 0.55];
function render() {
  const st = rec.state || "idle";
  const strip = $("strip");
  strip.dataset.state = st === "recording" && rec.paused ? "paused" : st;
  $("time").textContent = clock(rec.elapsed_s);
  strip.title = st === "starting" ? "Starting the recording" : rec.paused ? "Paused · drag to move" : "Recording · drag to move";
  $("pause").disabled = st !== "recording";
  $("stop").disabled = st !== "recording";
  $("pause").title = rec.paused ? "Resume" : "Pause";
  $("pause").setAttribute("aria-label", $("pause").title);
  const levels = Object.values(rec.levels || {});
  const level = levels.reduce((m, v) => Math.max(m, v.level || 0), 0);
  const base = rec.paused || st !== "recording" ? 0 : Math.sqrt(level);
  [...$("bars").children].forEach((bar, i) => {
    const jitter = 0.75 + Math.random() * 0.5;
    bar.style.transform = `scaleY(${Math.max(0.12, Math.min(1, base * W[i] * jitter * 1.6)).toFixed(3)})`;
  });
}

function applyLook(s) {
  if (!s) return;
  const light = s.theme === "light" || (s.theme === "system" && matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.classList.toggle("light", light);
}

function handle(name, data) {
  if (name === "hello" || name === "status") { if (data.recorder) { rec = data.recorder; render(); } }
  else if (name === "settings") applyLook(data);
}

async function connect() {
  for (;;) {
    try {
      const res = await fetch("/api/events", { headers: { "X-Steno-Token": token } });
      if (res.status === 401) return;
      if (!res.ok || !res.body) throw new Error("events " + res.status);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          let name = "message", data = "";
          for (const line of buf.slice(0, i).split("\n")) {
            if (line.startsWith("event: ")) name = line.slice(7);
            else if (line.startsWith("data: ")) data += line.slice(6);
          }
          buf = buf.slice(i + 2);
          if (data) { try { handle(name, JSON.parse(data)); } catch (e) { /* skip a bad event */ } }
        }
      }
    } catch (e) { /* reconnect below */ }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

const bridge = () => (window.pywebview && window.pywebview.api) || null;

$("pause").addEventListener("click", async () => {
  const res = await call("/api/record/" + (rec.paused ? "resume" : "pause"), "POST");
  if (res && res.recorder) { rec = res.recorder; render(); }
});
$("stop").addEventListener("click", async () => {
  $("stop").disabled = true;
  await call("/api/record/stop", "POST");
  const b = bridge();
  if (b && b.stopped) b.stopped();  // hide the strip and bring the meeting forward
});
$("open").addEventListener("click", () => { const b = bridge(); if (b && b.show_main) b.show_main(); });
$("hide").addEventListener("click", () => { const b = bridge(); if (b && b.dismiss) b.dismiss(); });

/* Drag to move: our own, pywebview's drag region fails on Windows. Deltas are CSS pixels from
   the press; one call in flight at a time, always with the latest position. */
let drag = null, sending = false;
async function sendDrag() {
  const b = bridge();
  if (sending || !drag || !b || !drag.pending) return;
  sending = true;
  const [dx, dy] = drag.pending;
  drag.pending = null;
  try { await b.drag_to(dx, dy); } catch (e) { /* ignore */ }
  sending = false;
  if (drag && drag.pending) sendDrag();
}
$("strip").addEventListener("pointerdown", (ev) => {
  const b = bridge();
  if (ev.button !== 0 || ev.target.closest("button") || !b || !b.drag_start) return;
  drag = { x: ev.screenX, y: ev.screenY, pending: null };
  try { $("strip").setPointerCapture(ev.pointerId); } catch (e) { /* moves still arrive while over the strip */ }
  b.drag_start();
});
$("strip").addEventListener("pointermove", (ev) => {
  if (!drag) return;
  drag.pending = [ev.screenX - drag.x, ev.screenY - drag.y];
  sendDrag();
});
const endDrag = () => {
  if (!drag) return;
  drag = null;
  const b = bridge();
  if (b && b.drag_end) setTimeout(() => b.drag_end(), 120);
};
$("strip").addEventListener("pointerup", endDrag);
$("strip").addEventListener("pointercancel", endDrag);

call("/api/settings").then((d) => applyLook(d && d.settings)).catch(() => {});
render();
connect();
