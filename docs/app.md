# Desktop app: how it is built

A map for working on the app (`steno app`). User-facing docs: [README](../README.md).

## Pieces

| Part | File | What it does |
|---|---|---|
| Window | `src/steno/ui/window.py`, `src/steno/ui/frame.py` | pywebview (WebView2) window around the local UI server; Windows 11 Acrylic backdrop (`apply_glass`); own title bar (`frame.py`: the caption strip is removed via `WM_NCCALCSIZE` and WS_SYSMENU, the window stays sizable so snapping/resizing/Win+arrows work; CSS `app-region: drag` on the header and brand row moves/snaps the window through WebView2's non-client region support; the page's minimize/maximize/close buttons call `Bridge.window_control`, close still asks while recording; any failure keeps the native title bar; WinForms would re-add the removed caption height on every restore from minimized/maximized, so rame.py keeps the last normal size and re-applies it right after a restore). The window opens centred above the taskbar and is moved inside the work area if needed (it_on_screen): a part that starts off screen is never painted and showed as a white rectangle; after a move the page is repainted once (stenoRepaint). A blank page loads first because that WebView2 setting applies from the next navigation, size fitted to the scaled screen, single instance (named mutex; a second start focuses the window), confirm on close while recording, log file `<data dir>/logs/app.log`. `Bridge` is the JS API (`window.pywebview.api.pick_folder()`, `open_folder(kind, ref, mtype)`: Explorer on a meeting (ref = session id), project (ref = project id), subfolder (ref = project id, mtype = its name; the nearest existing parent inside the project if the folder is not there yet) or the inbox (the data dir's `sessions/`); the path is worked out by `projects.folder_to_open` from ids and names, never taken from the page; `open_meeting_folder(session_id)` is the meeting shorthand); keep its attributes private (`_name`), pywebview exposes public ones to the page. |
| Server | `src/steno/ui/server.py` | stdlib `ThreadingHTTPServer` on 127.0.0.1, per-launch token (`X-Steno-Token`), Host/Origin checks, CSP, routes `/api/...` to `UiApp`, SSE at `/api/events`. |
| API | `src/steno/ui/api.py` | `UiApp`: plain methods returning JSON dicts or raising `ApiError`. |
| Recorder | `src/steno/ui/recorder.py` | Drives one `Engine` (same wiring as `steno record`, including refine models). Start into a project, pause/resume (samples replaced by silence, so the clock and kept audio stay aligned), mirrors `transcript.md` into the meeting folder every 2 s while recording. |
| Jobs | `src/steno/ui/jobs.py` | One background job at a time (finalize, clean, summary, todo, and `auto`: the after-meeting chain from Settings). |
| Projects | `src/steno/projects.py` | Registry (`<data dir>/projects.json`): projects are labels (optional linked folder, archived flag); subfolders are any folder on disk, meetings get `<subfolder folder>/<YYYY-MM-DD title>/`; meetings without a subfolder stay in Steno only. See [formats](formats.md). |
| Mini strip | `src/steno/ui/window.py` (`MiniBridge`, `watch_mini`, `prepare_mini`, `save/restore_mini_place`), `src/steno/ui/static/mini.*` | Second pywebview window (196x34, frameless, always on top, no focus steal) made hidden at start. `watch_mini` polls the recorder: shown while `starting`/`recording`, except when the main window is the foreground window, after x (`MiniBridge.dismiss`, until the recording ends) or with `settings.mini_strip` off; hidden from `stopping` on. Page `/mini?token=` (own SSE stream): grip, dot (opens Steno), timer, level bars, Pause/Resume, Stop (also brings the main window forward), x. Dragging anywhere but a button moves it through `MiniBridge.drag_start/drag_to/drag_end` (Win32 `SetWindowPos`; pywebview's own drag region calls `SetWindowPos` with `None` and fails); the place is kept in `<data dir>/mini-strip.json` and used again if still on a screen. Excluded from screen capture (`WDA_EXCLUDEFROMCAPTURE`), rounded corners. |
| Settings | `src/steno/settings.py` | `<data dir>/settings.json`: `auto_finalize` (on: finalize after every recording; the default `[post]` modes need no kept audio), `auto_title` (on: name untitled meetings, see "Automatic title"), `auto_clean`, `auto_summary`, `auto_todo` (off by default), `glossary`, `theme`, `transparent`, `mini_strip` (on). |
| Shortcuts | `src/steno/shortcuts.py` | `steno install-shortcuts`: Start menu and desktop `.lnk` running `pythonw -m steno.cli app`. The icon (`steno.ico`, 16-256 px: dark rounded tile, orange four-bar wave) is drawn in code with numpy, bars snapped to whole pixels at small sizes. The app rewrites it on each start and sets it as window/taskbar icon with its own AppUserModelID (`Steno.Desktop`), so it does not show as Python. |
| Page | `src/steno/ui/static/` | `index.html`, `style.css`, `app.js`. Plain JS, no build step, no CDN, no web fonts, no inline scripts or styles (CSP). Only these three files are served (`STATIC_TYPES`). |

## API

All JSON, all need the token header. Errors: `{"error": "..."}` with a 4xx/5xx status.

| Method | Path | |
|---|---|---|
| GET | `/api/status` | recorder (`state`, `paused`, `project`, levels, elapsed, speaker, backlog), running job, config summary |
| GET/POST/DELETE | `/api/projects`, `/api/projects/<id>` | list (with `inbox` count, `types` with `folder`/`missing`, `archived`, `all_meetings`) / new project `{name, folder?}` (older form `{path, name?, create?}` still works) / forget (files stay) |
| POST | `/api/projects/<id>/update` `{name?, folder?, archived?}` | rename, link (`folder` path) or unlink (`folder: null`) a folder, archive or restore |
| POST | `/api/projects/<id>/subfolders` `{folder, name?, create?}` | add a subfolder: an existing folder anywhere, or `create` a new folder `name` inside `folder`; one folder per subfolder |
| POST | `/api/projects/<id>/subfolders/rename` `{name, new_name}`, `/subfolders/folder` `{name, folder}` | rename (folder stays) / Change folder… (refused while the old folder holds meetings; meetings already moved along are relinked) |
| POST | `/api/projects/<id>/types` `{types: [{name, folder?}]}` | older form: replace all subfolders (relative folders inside a linked project folder) |
| POST | `/api/projects/<id>/types/delete` `{name, meetings?}` | remove a subfolder from Steno; `meetings: true` (default) also deletes its meetings (Steno's files only), else they stay in the project, kept in Steno; folders always stay |
| POST | `/api/projects/<id>/delete` `{meetings}` | remove the project from Steno; its meetings go to the inbox, or with `meetings: true` are deleted; an older project's `Steno/` loses Steno's own files once empty of meetings; folders and own files stay |
| POST | `/api/projects/<id>/folder` `{folder}` | can the next recording go into this existing folder? `{folder, name, type, title}` or 400 with the reason (`projects.check_existing_folder`) |
| POST | `/api/inbox/delete` | delete every inbox meeting the app shows (not the archived ones) |
| GET | `/api/projects/<id>/meetings` | oldest first (`folder` empty = kept in Steno); id `inbox` = sessions that belong to no project |
| POST | `/api/record/start` `{project?, type?, title?, folder?, mic, loopback, keep_audio, auto_title?}`, `/api/record/pause`, `/resume`, `/stop` | `auto_title` defaults to the setting; the recorder snapshot carries it. `folder`: record into that existing meeting folder (its subfolder decides the type; the title defaults to the folder name without its date, so it is never auto-titled) |
| POST | `/api/record/auto_title` `{enabled}` | switch the automatic title of the meeting being recorded (409 when not recording) |
| GET | `/api/llm/status` (`?refresh=1`) | `{llm: {state, method, plan, detail, login_running}}`, see "AI status" |
| POST | `/api/llm/login`, `/api/llm/install` | open `claude auth login` in a visible console / open the Claude Code install page in the default browser (a fixed address) |
| GET | `/api/sessions/<id>` | segments (clean text when fresh, else final, else best live), speakers, title, project, summary/todo text |
| GET | `/api/sessions/<id>/doc/summary`, `/doc/todo` | `{markdown}` |
| POST | `/api/meetings/<id>/rename` `{title}`, `/api/meetings/<id>/move` `{project, type?}` | renames/moves the meeting folder (no `type` = in the project without a folder, Steno's files leave the old folder; `project: "inbox"` = back to the inbox, see below) |
| POST | `/api/meetings/<id>/save` `{folder}` | Save to… an existing meeting folder that lies in a subfolder of any project (an untitled meeting takes the folder's name) |
| POST | `/api/meetings/<id>/archive` `{archived}` | hide/show it in the app (`archived.json` in the session dir; files stay) |
| DELETE | `/api/meetings/<id>` | deletes the session dir and Steno's files in its meeting folder for good; the folder goes only when nothing else is left in it (not while recording it or while a job runs on it) |
| POST | `/api/sessions/<id>/jobs` `{kind}` | `finalize`, `clean`, `summary`, `todo` |
| POST | `/api/sessions/<id>/name` `{speaker, name, save_profile?, view?}` | name a speaker (optionally into a voice profile); `view: "final"` names a final speaker (`ops.name_final_speaker`: the name goes to the live cluster overlapping its turns, the voice is learned from the window embeddings inside its turns) and queues finalize again. `GET /api/sessions/<id>` says which labels the lines carry in `speaker_space` (`final` / `live`) |
| GET/POST | `/api/settings` | read / update (partial) |
| GET/POST/DELETE | `/api/people`, `/api/people/<id>/rename`, `/api/people/<id>` | voice profiles |
| POST | `/api/people/enroll` `{name, seconds?, existing?}` | record the microphone (10-120 s, default 30) into a new person or add voice to an existing one; 409 while a meeting is recorded or another enrolment runs |
| GET | `/api/people/enroll` | state of the enrolment: `{enroll: {state, name, elapsed_s, seconds, level, message, created, speech_s}}`, state `idle`, `preparing`, `recording`, `processing`, `done` or `error` |
| POST | `/api/people/enroll/cancel` | finish early: processes what was recorded if >= 10 s, else cancels |

SSE events: `hello`, `status`, `segment` `{session, segment}`, `revision` `{session, seq, text}`
(refined text of a line: swap in place), `job`, `names`, `sessions`, `projects`, `settings`, `llm` (Claude status may have changed: ask `/api/llm/status` again), `enroll` (the enrolment state above, about 4 times a second while recording).

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

## AI status (Claude Code)

Every AI feature goes through the Claude Code CLI (`llm.run_claude`), so the app asks it whether it can work.

- `llm.probe_status` runs `claude auth status` (no tokens, no console window, 20 s timeout). The CLI is
  found with `llm.find_cli`: `PATH`, then `~/.local/bin`, `%APPDATA%\npm` and a few more, because the
  app runs under `pythonw` with its own PATH. The CLI exits 1 when logged out but still prints its
  JSON, so the output is read whatever the exit code. States: `ok` (`method`, `plan` from `authMethod`
  / `subscriptionType`; the account name is never read), `logged_out`, `missing` (no CLI), `error`
  (unknown; the buttons stay usable). `llm.StatusCache` keeps the answer 60 s; `?refresh=1` asks again.
- A job that fails with `LlmAuthError` (`llm.classify_failure`: "Failed to authenticate", OAuth
  expired, not logged in, invalid API key) invalidates the cache and sends the `llm` event, so the
  indicator turns at once. Failures read the JSON `result` of the CLI (exit code or `is_error`), not
  the raw output: auth -> "Not logged in to Claude: log in to use AI features.", credit balance /
  usage limit -> `LlmLimitError` with a short message. `clean` stops at the first auth error instead
  of trying every chunk.
- Login: `POST /api/llm/login` starts `claude auth login` with `CREATE_NEW_CONSOLE` (a visible window,
  not the hidden flags of normal calls); the user finishes the OAuth flow in the browser. One at a
  time. When that console exits the cache is invalidated and `llm` is sent. The page also polls
  `?refresh=1` every 3 s for up to 3 minutes. `POST /api/llm/install` opens the install page with
  `webbrowser` (the address is a constant in `llm.INSTALL_URL`, never taken from the page).
- UI: the sidebar footer has a small line above "On this computer" (`#side-llm`): dot + "Claude"
  (tooltip: method and plan) when ok, "AI off: not logged in" / "AI off: Claude Code not installed" in
  the danger colour otherwise; a click opens a menu (Log in to Claude / Install Claude Code, Check
  again). Settings has a "Claude" section with the same status and button. Summary, To-do and Clean up
  stay visible while AI is off, dimmed with a tooltip; a click shows the same menu instead of
  starting a job. A job error for a missing login shows the friendly message as it is.

## Automatic title

A meeting the user did not name gets a title from its transcript after Stop (setting `auto_title`,
on by default; the dock has a per-meeting "Auto title" switch that starts from it).

- Dock: next to the title field, before and during recording (`#btn-autotitle`, `#btn-autotitle-live`).
  `record/start` takes `auto_title`; the recorder keeps it (`Recorder.auto_title`, also in the snapshot)
  and `POST /api/record/auto_title` changes it while recording. A typed title always wins (the
  recorder turns it off; the switch is shown off with a tooltip). The switch is disabled with the
  tooltip "AI off: ..." while Claude is not available, but its choice is kept: logging in during the
  recording is enough. Placeholder of the title field: "Title is written after the meeting" when the
  switch is on and Claude status is ok, else "Untitled meeting".
- Chain: `_after_recording` adds the `title` step right after `finalize` (before clean-up, summary and
  to-do) when the recording's `auto_title` is on, in the same `auto` job. `title` is also a normal job
  kind (`run_title` in `default_runners`).
- `run_title`: skips unless the meeting's title is still `projects.DEFAULT_TITLE` ("Meeting"; a title
  the user typed or set with Rename differs), asks `llm.suggest_title` (haiku from `[llm]`,
  prompt `prompts/title.md`: 2-6 words, main language of the text, no date/quotes/punctuation), checks
  the title again (the user may have renamed meanwhile) and applies it with `projects.rename_meeting`,
  so the meeting folder is renamed too (`YYYY-MM-DD <title>`) and the sidebar updates.
- Text sent: the best transcript (clean > final > live), `llm.sample_for_title`: all of it up to
  6000 characters, else the first 3/4 plus a piece from the middle after a `[...]` line; less than
  40 characters is never sent. The reply is cleaned (`llm.clean_title_reply`: first line, markdown,
  quotes, "Title:" label, trailing punctuation and path-illegal characters removed, at most 60
  characters cut at a word; "Meeting" / "Untitled" or more than 10 words is rejected).
- Failures are quiet (`QUIET_STEPS` in `api.py`): the meeting stays untitled, the log has a line,
  the job message ends with "skipped: title (...)" and no error toast appears; the other steps go on.

## Design (second pass)

- Calm, flat surfaces in neutral warm greys (dark and light themes, optional transparency):
  regions are separated by 1px low-contrast hairlines, not boxes or shadows. The app runs edge to
  edge (no padding around the panels, no DWM window border). Settings, People, Summary and To-do
  are lists between hairlines; inputs and secondary buttons are 1px outlined, radius 6px; menus,
  dialogs and toasts 8px with a soft shadow. The dock is a flush bar with a top hairline.
- Accent is used sparingly: selected-row marker, focus ring, primary and Record buttons (flat
  `#c8470f`, white text), the live dot and the dock's top line while recording.
- Speaker colours: eight clearly different hues (`--spk-1..8`, tuned per theme), handed out per
  meeting in order of first appearance, so two speakers in one meeting never share a colour;
  unnamed/"?" speakers are neutral grey (`--spk-0`). The People list colours by list index.
- Fonts: system UI stack; `--display` (Segoe UI Variable Display / SF Pro Display) for titles,
  `--sans` (the Text cut) for the rest; mono only for model names. Design tokens live at the
  top of `style.css`: four text levels (`--text`, `--body`, `--dim`, `--faint`, plus `--ghost` for
  times/counts), radii 6/8, a 4px spacing grid.
  The header is a quiet breadcrumb (project name); the meeting title (22px) is the page's hero. Icons are stroked 24x24 paths in `ICONS` (app.js), built as SVG nodes.
- Projects are labels, subfolders are folders. A project groups meetings and may be linked to a folder
  (a shortcut, optional); a subfolder points at any folder on disk, and meetings saved in it are written
  there. A meeting in a project without a subfolder is kept in Steno (no folder on disk) until it is
  saved somewhere.
  - New project (`+` in the Projects header, or the empty screen): one dialog, Name plus an optional
    "Link a folder". `modal()` (app.js) is the dialog shell: `submit(values)` may throw and the message
    shows inline while the dialog stays open and keeps what was typed (`formModal` takes the same
    `submit`); `folderField()` is the picker field (a text field in a plain browser). A new project is
    selected and its ready screen offers a big "Add subfolder".
  - Add subfolder (folder-plus on a project row, the menu, the ready screen): "Choose existing folder"
    (default; the name is prefilled from the folder, editable) or "Create new folder" (name plus a
    location); the chosen path is shown. A subfolder whose folder is gone shows a warning icon (tooltip:
    the path and "use Change folder..."); recording into it falls back to Steno's storage and the
    recorder `notice` is shown once as a toast.
  - Menus (`projectItems`, `subfolderItems`, `inboxItems`, `meetingItems`): right-click on a row, the
    hover `...` button on the row (always shown on the open project), and the `...` in the header (the
    open project) all show the same items. Project: New subfolder, Rename, Link folder / Change linked
    folder / Unlink folder, Open in File Explorer (when linked), Archive, Delete. Subfolder: Record
    here, Open in File Explorer, Rename, Change folder, Remove. `+` (new meeting) and the folder-plus
    (new subfolder) stay as visible row buttons. Menus close on Escape or an outside click, stay inside
    the window, and move with the arrow keys.
  - Delete (project) and Remove (subfolder): by default only Steno forgets it (files stay; a project's
    meetings go to the Inbox, a subfolder's meetings stay in the project); the checkbox "Also delete all
    N meetings" turns the dialog red and deletes them for good.
  - Archive: an archived project leaves the list and sits under a collapsed "Archived (n)" row at the
    bottom of the sidebar (Restore, Delete). Nothing can be recorded or dropped into it.
  - Save to... (button next to Summary/To-do on a meeting without a folder, not while recording): a
    list of the subfolders of all non-archived projects (missing ones disabled), or an existing meeting
    folder (`POST /api/meetings/<id>/save`).
- Existing folder (dock button next to the subfolder, desktop app only): pick a meeting folder made
  by hand before the meeting (agenda, notes). The picker opens in the chosen subfolder. The folder
  must lie directly in `Steno/` or a subfolder's folder, must not be a meeting already and must
  not have its own `transcript.md`/`summary.md`/`todo.md`. Steno's files go next to the user's;
  the button then shows the folder name (click: choose another or use a new folder), the title
  field shows the folder's title, and the choice is per meeting. Without the button, a folder that
  already has the name `YYYY-MM-DD title` (and is no meeting yet) is used instead of a `(2)` copy.
- Meeting dates in the sidebar are relative (`relDate` in app.js): Today, Yesterday, weekday
  (2-6 days), Last week, 2/3 weeks ago, Last month, month name, "March 2025" for older years. The
  row's tooltip has the exact date and time. The labels are repainted when the day changes (timer
  at midnight, window focus, `visibilitychange`: `watchDay`). Headers and dialogs keep the full date.
- Sidebar tree: under a project its subfolders come first (folder icon, chevron, name, `+`, `...`;
  tooltip = its folder; folded state in `steno-folded`), their meetings one level deeper (16 px per
  level); meetings in the project itself sit on the subfolders' level, after them, with no icon. There
  is no "No type" group. A draft "New meeting" row appears where the meeting will go. The dock gets a
  subfolder menu ("No subfolder" = kept in Steno; chosen with `+` or the menu, else the open meeting's
  subfolder, else the last used, `steno-type-<project>`); the header reads "Project > Subfolder".
  Move... lists every non-archived project, subfolder and the Inbox / TBD.
- Inbox / TBD: always the last node of the sidebar, also when empty (label `INBOX_NAME`), with `+`
  to start a meeting there (`record/start` without a project). It holds the sessions that have no
  meeting folder.
- Open in File Explorer (desktop app only, via `Bridge.open_folder`): an item in the project,
  subfolder, Inbox / TBD and meeting menus (an inbox meeting opens its session folder in the data dir).
- Drag to move: press and hold a meeting row ~350 ms (pointer events; a click still opens it, moving
  more than 6 px before the hold ends cancels it). The row stays behind as a grey hole and a grey
  translucent copy of it (a clone of the row) follows the pointer. The place under it (nearest
  `[data-drop]` ancestor: project node = in the project without a subfolder, subfolder node, Inbox / TBD) lights up when
  the meeting is not already there, and its meetings slide apart (a `.drop-gap` spacer, 150 ms
  height transition) to open a gap where the meeting's date puts it (`gapPlace`: newest first, rows
  carry `data-ts`); leaving the place closes the gap. Hovering a closed project or subfolder for
  0.7 s opens it (only for this drag: restored on cancel); the sidebar scrolls near its top/bottom
  edge. Release: the copy settles into the gap, then the meeting and its folder move
  (`POST /api/meetings/<id>/move`, same as Move...). Escape or a release elsewhere cancels: the copy
  flies back to the hole. `prefers-reduced-motion`: no animation, same result. A recording meeting
  cannot be picked up.
- Moving to the inbox (`projects.move_to_inbox`): sessions always live in the data dir, so only
  Steno's files leave the meeting folder (marker, Steno's `.gitignore`, `transcript.md`,
  `summary.md`/`todo.md` identical to the session's); the folder goes when nothing else is left,
  otherwise (own notes, a hand-edited summary) it stays where it is. A meeting recorded into the
  user's own folder (`"own_folder": true` in the marker) keeps that folder's name on rename and
  move. The session link is rewritten last to `{"title"}` only, so the id stays resolvable.
  Moving back creates a fresh folder (`2026-11-20 Title (2)` on a name clash).
- Layout: sidebar tree: each project (and the Inbox) folds open to its meetings, newest
  first (6, then "Show all"); `+` on a project opens a "New meeting" screen in it. The main
  area shows one meeting at a time (header with the project name, running-job chip, ⋯ menu;
  then the meeting: title, Summary/To-do/Clean up buttons, summary/to-do cards, transcript)
  and the recording dock at the bottom (title, audio-source menu, Record; while recording:
  timer, level bars, speaker/status, Pause, Stop). Pressing Record opens the new meeting as
  soon as it is listed. Only Rename and Move sit in a meeting's ⋯ menu (owner's wish: main
  actions stay in sight, not in submenus).
  Open/closed projects are remembered in `localStorage` (`steno-expanded`). Hovering a
  meeting in the sidebar shows ⋯ (Open in File Explorer, Copy transcript, Rename, Move, Archive,
  Delete); archived meetings sit under "Archived (N)" at the end of the project.
- Sidebar width: drag its right edge (190-460 px); dragging past ~120 px hides it, the
  header's menu button, a drag from the left edge, double-click on the edge or Ctrl+B bring
  it back (`steno-side-w`, `steno-side-hidden` in `localStorage`).
- Pinned meeting head: title/date row, the action buttons and the participant row form one block
  (`.mstick`, `position: sticky` inside the meeting section; the feed (`#feed`) is the scroller and
  shows one meeting at a time). It stays at the top while the transcript scrolls, with an opaque
  fill (`--stick`: the solid surface colour, also with transparency on) and a hairline below it
  while it covers lines (`.stuck`, set in `onScroll`). Its padding-top is the gap above the title:
  a negative `top` leaves only 10 px of it visible when pinned. Job progress, summary/to-do cards
  and the transcript heading scroll away; "jump to latest" and `scrollToEnd` need no offset.
- Participants (`.mpeople`, `peopleOf` / `updatePeople` / `fitPeople` in app.js): one chip per
  speaker, grouped by the name the lines show and ordered by first appearance, so the dot colour is
  the same `speakerColor` as the transcript rows. Text "Name · lines" (unnamed: "Speaker 3 · 5").
  Counts come from `m.detail.segments`, so they follow `appendSegment` live and every rebuild
  (`refreshMeeting` after finalize or a rename). A chip opens the same namer as the name in the
  transcript (`openNamer`, form shown under the chips); it is only a button when a line of that
  speaker can be named (`canName`), else a plain label. Chips that do not fit are folded into a
  "+N" chip (one line when the row is narrower than 520 px, else two) that expands to all and then
  reads "Less"; measured by a `ResizeObserver` on the row.
- Settings has an X (top right, `#settings-close`) and Esc closes it (in a text field the first
  Esc only leaves the field); both go back to where it was opened from (`S.back`: the project and
  meeting with the scroll position, or the People list).
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
  and brand row are drag regions; in a normal browser none of this shows. The header is fixed at
  48 px (the window buttons 46x47, the 1px hairline stays visible below them) at every width, so
  the header's controls (⋯, job chip) and the buttons share one centre line.
- Files in a meeting folder can be locked by another program for a moment (Explorer preview,
  OneDrive, an editor: `PermissionError`, WinError 32). `projects.sync` retries the atomic write a
  few times (50/100/200 ms) and otherwise skips that round with a debug log; the next sync (every
  2 s while recording, and at the end) writes it.

## Not done yet (owner's list)

- Design pass from the owner's feedback.
- Swedish UI language; Mac/Linux; installer and first-run setup; global hotkey.
- Noticing a project folder moved outside the app.
- Moving a meeting recorded into an existing folder moves the whole folder, the user's files too.
- `summary.md`/`todo.md` the user writes into a meeting folder after the recording are replaced by
  the next summary/to-do run.

## Testing

`tests/test_ui.py` (server, API, recorder with a fake engine), `tests/test_projects.py` (meeting folders, moves between subfolders/projects/inbox, `folder_to_open`),
`tests/test_settings.py`, `tests/test_shortcuts.py`, `tests/test_ai_status.py` (Claude status, login, readable CLI errors, automatic title). For a manual run against an empty data
dir: set `STENO_DATA_DIR` to a temp folder (link its `models/` to the real models folder) and
start `steno ui --no-browser` or `steno app`.
