# Desktop app: how it is built

A map for working on the app (`steno app`). User-facing docs: [README](../README.md).

## Pieces

| Part | File | What it does |
|---|---|---|
| Window | `src/steno/ui/window.py`, `src/steno/ui/frame.py` | pywebview (WebView2) window around the local UI server; Windows 11 Acrylic backdrop (`apply_glass`); own title bar (`frame.py`: the caption strip is removed via `WM_NCCALCSIZE` and WS_SYSMENU, the window stays sizable so snapping/resizing/Win+arrows work; CSS `app-region: drag` on the header and brand row moves/snaps the window through WebView2's non-client region support; the page's minimize/maximize/close buttons call `Bridge.window_control`, close still asks while recording; any failure keeps the native title bar; WinForms would re-add the removed caption height on every restore from minimized/maximized, so rame.py keeps the last normal size and re-applies it right after a restore). The window opens centred above the taskbar and is moved inside the work area if needed (it_on_screen): a part that starts off screen is never painted and showed as a white rectangle; after a move the page is repainted once (stenoRepaint). A blank page loads first because that WebView2 setting applies from the next navigation, size fitted to the scaled screen, single instance (named mutex; a second start focuses the window), confirm on close while recording, log file `<data dir>/logs/app.log`. `Bridge` is the JS API (`window.pywebview.api.pick_folder()`, `open_meeting_folder(session_id)`: Explorer on that meeting's folder only); keep its attributes private (`_name`), pywebview exposes public ones to the page. |
| Server | `src/steno/ui/server.py` | stdlib `ThreadingHTTPServer` on 127.0.0.1, per-launch token (`X-Steno-Token`), Host/Origin checks, CSP, routes `/api/...` to `UiApp`, SSE at `/api/events`. |
| API | `src/steno/ui/api.py` | `UiApp`: plain methods returning JSON dicts or raising `ApiError`. |
| Recorder | `src/steno/ui/recorder.py` | Drives one `Engine` (same wiring as `steno record`, including refine models). Start into a project, pause/resume (samples replaced by silence, so the clock and kept audio stay aligned), mirrors `transcript.md` into the meeting folder every 2 s while recording. |
| Jobs | `src/steno/ui/jobs.py` | One background job at a time (finalize, clean, summary, todo, and `auto`: the after-meeting chain from Settings). |
| Projects | `src/steno/projects.py` | Registry (`<data dir>/projects.json`) and meeting folders (`<project>/Steno/<YYYY-MM-DD title>/`; with meeting types `<project>/<type folder>/<YYYY-MM-DD title>/`, e.g. `Meetings/Board/2026-11-20 Lobby days/`). See [formats](formats.md). |
| Mini strip | `src/steno/ui/window.py` (`MiniBridge`, `watch_mini`, `prepare_mini`, `save/restore_mini_place`), `src/steno/ui/static/mini.*` | Second pywebview window (196x34, frameless, always on top, no focus steal) made hidden at start. `watch_mini` polls the recorder: shown while `starting`/`recording`, except when the main window is the foreground window, after x (`MiniBridge.dismiss`, until the recording ends) or with `settings.mini_strip` off; hidden from `stopping` on. Page `/mini?token=` (own SSE stream): grip, dot (opens Steno), timer, level bars, Pause/Resume, Stop (also brings the main window forward), x. Dragging anywhere but a button moves it through `MiniBridge.drag_start/drag_to/drag_end` (Win32 `SetWindowPos`; pywebview's own drag region calls `SetWindowPos` with `None` and fails); the place is kept in `<data dir>/mini-strip.json` and used again if still on a screen. Excluded from screen capture (`WDA_EXCLUDEFROMCAPTURE`), rounded corners. |
| Settings | `src/steno/settings.py` | `<data dir>/settings.json`: `auto_finalize` (on: finalize after every recording; the default `[post]` modes need no kept audio), `auto_clean`, `auto_summary`, `auto_todo` (off by default), `glossary`, `theme`, `transparent`, `mini_strip` (on). |
| Shortcuts | `src/steno/shortcuts.py` | `steno install-shortcuts`: Start menu and desktop `.lnk` running `pythonw -m steno.cli app`. The icon (`steno.ico`, 16-256 px: dark rounded tile, orange four-bar wave) is drawn in code with numpy, bars snapped to whole pixels at small sizes. The app rewrites it on each start and sets it as window/taskbar icon with its own AppUserModelID (`Steno.Desktop`), so it does not show as Python. |
| Page | `src/steno/ui/static/` | `index.html`, `style.css`, `app.js`. Plain JS, no build step, no CDN, no web fonts, no inline scripts or styles (CSP). Only these three files are served (`STATIC_TYPES`). |

## API

All JSON, all need the token header. Errors: `{"error": "..."}` with a 4xx/5xx status.

| Method | Path | |
|---|---|---|
| GET | `/api/status` | recorder (`state`, `paused`, `project`, levels, elapsed, speaker, backlog), running job, config summary |
| GET/POST/DELETE | `/api/projects`, `/api/projects/<id>` | list (with `inbox` count and `types`) / add `{path, name?, create?}` / forget (files stay) |
| POST | `/api/projects/<id>/types` `{types: [{name, folder?}]}` | replace the meeting types (folder defaults to `Meetings/<name>`) |
| GET | `/api/projects/<id>/meetings` | oldest first; id `inbox` = sessions without a meeting folder |
| POST | `/api/record/start` `{project?, type?, title?, mic, loopback, keep_audio}`, `/api/record/pause`, `/resume`, `/stop` | |
| GET | `/api/sessions/<id>` | segments (clean text when fresh, else final, else best live), speakers, title, project, summary/todo text |
| GET | `/api/sessions/<id>/doc/summary`, `/doc/todo` | `{markdown}` |
| POST | `/api/meetings/<id>/rename` `{title}`, `/api/meetings/<id>/move` `{project, type?}` | renames/moves the meeting folder (`type: null` = no type) |
| POST | `/api/meetings/<id>/archive` `{archived}` | hide/show it in the app (`archived.json` in the session dir; files stay) |
| DELETE | `/api/meetings/<id>` | deletes the session dir and its meeting folder for good (not while recording it or while a job runs on it) |
| POST | `/api/sessions/<id>/jobs` `{kind}` | `finalize`, `clean`, `summary`, `todo` |
| POST | `/api/sessions/<id>/name` `{speaker, name, save_profile?, view?}` | name a speaker (optionally into a voice profile); `view: "final"` names a final speaker (`ops.name_final_speaker`: the name goes to the live cluster overlapping its turns, the voice is learned from the window embeddings inside its turns) and queues finalize again. `GET /api/sessions/<id>` says which labels the lines carry in `speaker_space` (`final` / `live`) |
| GET/POST | `/api/settings` | read / update (partial) |
| GET/POST/DELETE | `/api/people`, `/api/people/<id>/rename`, `/api/people/<id>` | voice profiles |
| POST | `/api/people/enroll` `{name, seconds?, existing?}` | record the microphone (10-120 s, default 30) into a new person or add voice to an existing one; 409 while a meeting is recorded or another enrolment runs |
| GET | `/api/people/enroll` | state of the enrolment: `{enroll: {state, name, elapsed_s, seconds, level, message, created, speech_s}}`, state `idle`, `preparing`, `recording`, `processing`, `done` or `error` |
| POST | `/api/people/enroll/cancel` | finish early: processes what was recorded if >= 10 s, else cancels |

SSE events: `hello`, `status`, `segment` `{session, segment}`, `revision` `{session, seq, text}`
(refined text of a line: swap in place), `job`, `names`, `sessions`, `projects`, `settings`, `enroll` (the enrolment state above, about 4 times a second while recording).

## Speakers and kept audio

- Lines keep one text colour. A live speaker guess that may still change (`provisional`) gets a
  small "?" after the name; "Speaker N" is an unnamed one.
- Finalize runs after every recording by default (`auto_finalize`) and needs no kept audio
  (`diar.bin` / `windows.bin` from the live run); only `[post] diarization = "thorough"` or
  `asr_mode` longform/selective need it, and the API only asks for audio then.
- The dock has a visible Keep audio toggle (on until Steno closes, never remembered): the raw
  recording stays in the session dir in the data dir, e.g. to compare speech models later.
- Pressing Record clears the recorder's session id until the new one exists, and the page only
  opens a session that is recording and differs from the previous one.

## Design (second pass)

- Always dark glass: transparent page over the Acrylic backdrop; panels are translucent dark
  fills with 1px light hairlines. In a normal browser `body.browser` adds a dark gradient.
- Accent `#ff5a1f` for recording only (Record button, live dot, active marker, focus ring,
  progress bars). Everything else white/gray.
- Fonts: system UI stack; `--display` (Segoe UI Variable Display / SF Pro Display) for titles,
  `--sans` (the Text cut) for the rest; mono only for model names. Design tokens live at the
  top of `style.css`: four text levels (`--text`, `--body`, `--dim`, `--faint`, plus `--ghost` for
  times/counts), surfaces with a 1px top `--highlight`, radii 6/8/10/14, a 4px spacing grid.
  The header is a quiet breadcrumb (project name); the meeting title (22px) is the page's hero. Icons are stroked 24x24 paths in `ICONS` (app.js), built as SVG nodes.
- Subfolders = meeting types. Created from the sidebar: hover a project for the folder-plus button
  (next to `+`), or "New subfolder" under a project that has some; each subfolder row has `+` and
  a hover ⋯ (Rename, Remove when empty). Project ⋯ → Subfolders... edits them all at once.
- Meeting types: a project with types shows one small group
  per type in the sidebar (chevron, name, `+` for a new meeting of that type; folded state in
  `steno-folded`), then "No type" when such meetings exist. The dock gets a type menu
  (chosen with `+` or the menu, else the open meeting's type, else the last used,
  `steno-type-<project>`); the header reads "Project › Type". Move... lists every project and
  type. Without types everything looks as before.
- Layout: sidebar tree: each project (and the Inbox) folds open to its meetings, newest
  first (6, then "Show all"); `+` on a project opens a "New meeting" screen in it. The main
  area shows one meeting at a time (header with the project name, running-job chip, ⋯ menu;
  then the meeting: title, Summary/To-do/Clean up buttons, summary/to-do cards, transcript)
  and the recording dock at the bottom (title, audio-source menu, Record; while recording:
  timer, level bars, speaker/status, Pause, Stop). Pressing Record opens the new meeting as
  soon as it is listed. Only Rename and Move sit in a meeting's ⋯ menu (owner's wish: main
  actions stay in sight, not in submenus). Speakers get a stable colour from their name.
  Open/closed projects are remembered in `localStorage` (`steno-expanded`). Hovering a
  meeting in the sidebar shows ⋯ (Show in Explorer, Copy transcript, Rename, Move, Archive,
  Delete); archived meetings sit under "Archived (N)" at the end of the project.
- Sidebar width: drag its right edge (190-460 px); dragging past ~120 px hides it, the
  header's menu button, a drag from the left edge, double-click on the edge or Ctrl+B bring
  it back (`steno-side-w`, `steno-side-hidden` in `localStorage`).
- Copy buttons (clipboard, text only): on the Summary and To-do cards and the Transcript
  heading; the transcript is copied as "Speaker (mm:ss)" turns.
- Claude calls run without a console window (`llm.no_window()`: `CREATE_NO_WINDOW`).
- Narrow windows (split screen): at <= 760 px the sidebar becomes a slide-in drawer (menu
  button in the header); at <= 520 px the dock drops labels and avatars hide. Window
  `min_size` is 340x480.
- Motion: short CSS transitions/keyframes (new lines fade up, revised lines flash, dock
  swaps state, menus/toasts/dialogs pop, job progress bar). `prefers-reduced-motion` turns
  them off. No animation libraries: the CSP and offline rule allow only the three own files.
- Looks (Settings > Appearance, `settings.json` `theme` = system | dark | light, `transparent`):
  `html.light` swaps the colour tokens; `html.solid` makes panels opaque. The window backdrop is
  Mica (the wallpaper, smoothly blurred; Acrylic showed the windows behind as blocks), light or
  dark via `Bridge.set_look`; "system" follows Windows' app mode (`prefers-color-scheme`).
- Glass: panels are ~60-75 % opaque so a light wallpaper does not wash them out; in-page
  `backdrop-filter` is off in the desktop window (`html.glass`), where it does nothing and WebView2
  could paint white patches.
- Own title bar in the desktop window (`html.custom-frame`): window buttons top right, the header
  and brand row are drag regions; in a normal browser none of this shows.

## Not done yet (owner's list)

- Design pass from the owner's feedback.
- Swedish UI language; Mac/Linux; installer and first-run setup; global hotkey.
- Moving a meeting back to the Inbox; noticing a project folder moved outside the app.

## Testing

`tests/test_ui.py` (server, API, recorder with a fake engine), `tests/test_projects.py`,
`tests/test_settings.py`, `tests/test_shortcuts.py`. For a manual run against an empty data
dir: set `STENO_DATA_DIR` to a temp folder (link its `models/` to the real models folder) and
start `steno ui --no-browser` or `steno app`.
