# Desktop app: how it is built

A map for working on the app (`steno app`). User-facing docs: [README](../README.md).

## Pieces

| Part | File | What it does |
|---|---|---|
| Window | `src/steno/ui/window.py` | pywebview (WebView2) window around the local UI server; Windows 11 Acrylic backdrop (`apply_glass`), dark title bar, size fitted to the scaled screen, single instance (named mutex; a second start focuses the window), confirm on close while recording, log file `<data dir>/logs/app.log`. `Bridge` is the JS API (`window.pywebview.api.pick_folder()`); keep its attributes private (`_name`), pywebview exposes public ones to the page. |
| Server | `src/steno/ui/server.py` | stdlib `ThreadingHTTPServer` on 127.0.0.1, per-launch token (`X-Steno-Token`), Host/Origin checks, CSP, routes `/api/...` to `UiApp`, SSE at `/api/events`. |
| API | `src/steno/ui/api.py` | `UiApp`: plain methods returning JSON dicts or raising `ApiError`. |
| Recorder | `src/steno/ui/recorder.py` | Drives one `Engine` (same wiring as `steno record`, including refine models). Start into a project, pause/resume (samples replaced by silence, so the clock and kept audio stay aligned), mirrors `transcript.md` into the meeting folder every 2 s while recording. |
| Jobs | `src/steno/ui/jobs.py` | One background job at a time (finalize, clean, summary, todo, and `auto`: the after-meeting chain from Settings). |
| Projects | `src/steno/projects.py` | Registry (`<data dir>/projects.json`) and meeting folders (`<project>/Steno/<YYYY-MM-DD HH.MM title>/`). |
| Settings | `src/steno/settings.py` | `<data dir>/settings.json`: `auto_clean`, `auto_summary`, `auto_todo` (all off by default), `glossary`. |
| Shortcuts | `src/steno/shortcuts.py` | `steno install-shortcuts`: Start menu and desktop `.lnk` running `pythonw -m steno.cli app`; the `.ico` is drawn in code. |
| Page | `src/steno/ui/static/` | `index.html`, `style.css`, `app.js`. Plain JS, no build step, no CDN, no web fonts, no inline scripts or styles (CSP). Only these three files are served (`STATIC_TYPES`). |

## API

All JSON, all need the token header. Errors: `{"error": "..."}` with a 4xx/5xx status.

| Method | Path | |
|---|---|---|
| GET | `/api/status` | recorder (`state`, `paused`, `project`, levels, elapsed, speaker, backlog), running job, config summary |
| GET/POST/DELETE | `/api/projects`, `/api/projects/<id>` | list (with `inbox` count) / add `{path, name?, create?}` / forget (files stay) |
| GET | `/api/projects/<id>/meetings` | oldest first; id `inbox` = sessions without a meeting folder |
| POST | `/api/record/start` `{project?, title?, mic, loopback, keep_audio}`, `/api/record/pause`, `/resume`, `/stop` | |
| GET | `/api/sessions/<id>` | segments (clean text when fresh, else final, else best live), speakers, title, project, summary/todo text |
| GET | `/api/sessions/<id>/doc/summary`, `/doc/todo` | `{markdown}` |
| POST | `/api/meetings/<id>/rename` `{title}`, `/api/meetings/<id>/move` `{project}` | renames/moves the meeting folder |
| POST | `/api/sessions/<id>/jobs` `{kind}` | `finalize`, `clean`, `summary`, `todo` |
| POST | `/api/sessions/<id>/name` `{speaker, name, save_profile?}` | name a speaker (optionally into a voice profile) |
| GET/POST | `/api/settings` | read / update (partial) |
| GET/POST/DELETE | `/api/people`, `/api/people/<id>/rename`, `/api/people/<id>` | voice profiles |

SSE events: `hello`, `status`, `segment` `{session, segment}`, `revision` `{session, seq, text}`
(refined text of a line: swap in place), `job`, `names`, `sessions`, `projects`, `settings`.

## Design (first version, waiting for feedback)

- Always dark glass: transparent page over the Acrylic backdrop; panels are translucent dark
  fills with 1px light hairlines. In a normal browser `body.browser` adds a dark gradient.
- Accent `#ff5a1f` only on tiny things (recording dot, active marker, focus ring, primary
  button edge). Mostly white/gray/black.
- Fonts: system UI stack (`-apple-system`, `Segoe UI Variable`, ...) for text; mono
  (`Cascadia Mono`, `SF Mono`, ...) only for small uppercase status labels.
- Layout: sidebar (projects, Inbox, People, Settings, model/status footer); main area with
  the recording controls and status strip on top and the project's meetings as one feed
  (latest 3, "Show older meetings"), each meeting with a divider row and its actions.
- The window keeps the native title bar (dark) for now: snapping and resizing for free.

## Not done yet (owner's list)

- Compact mini strip that stays on top during a meeting (pause/stop).
- Design pass from the owner's feedback.
- Light mode; Swedish UI language; Mac/Linux; installer and first-run setup; global hotkey.
- Moving a meeting back to the Inbox; noticing a project folder moved outside the app.

## Testing

`tests/test_ui.py` (server, API, recorder with a fake engine), `tests/test_projects.py`,
`tests/test_settings.py`, `tests/test_shortcuts.py`. For a manual run against an empty data
dir: set `STENO_DATA_DIR` to a temp folder (link its `models/` to the real models folder) and
start `steno ui --no-browser` or `steno app`.
