"""Projects: folders you pick on disk; every meeting gets a readable folder inside them.

Layout::

    <project folder>/
      Steno/
        .gitignore            # "*": meeting text never ends up in a git repo by accident
        CLAUDE.md             # how Claude Code should read the meetings (cheap first)
        index.md              # one line per meeting, newest first
        2026-11-19 Sprint planning/
          .gitignore          # "*", also in meeting folders outside Steno/
          .steno-meeting.json # {"session": "<id>", "title": "..."}
          transcript.md       # best text so far; rewritten while recording
          summary.md, todo.md # only after you asked for them
      Meetings/Board/         # optional meeting types: each has its own folder
        2026-11-20 Lobby days and build night/

A project can have meeting types (``Board`` -> ``Meetings/Board``). Without types every
meeting goes into ``Steno/``; meetings without a type stay there when types exist.

The session itself (JSONL, revisions, voice windows, kept audio) stays in the global data
dir (``steno.paths``); its ``meeting.json`` points to the meeting folder. Voice profiles
are global too. The registry of projects is ``<data dir>/projects.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from steno import paths, transcript
from steno.transcript import atomic_write_text

log = logging.getLogger(__name__)

STENO_DIRNAME = "Steno"
MEETING_FILE = ".steno-meeting.json"
LINK_NAME = "meeting.json"  # in the session dir
ARCHIVED_NAME = "archived.json"  # in the session dir: hidden in the app
REGISTRY_NAME = "projects.json"
DEFAULT_TITLE = "Meeting"
MAX_TITLE = 80
COPIED = ("summary.md", "todo.md")
_CLAUDE_MARK = "<!-- steno:generated -->"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_lock = threading.RLock()


MAX_TYPES = 30
MAX_TYPE_DEPTH = 4


class ProjectError(ValueError):
    """A request about projects that cannot be honoured; the message is shown to the user."""


@dataclass(frozen=True)
class MeetingType:
    name: str
    folder: str  # relative to the project folder, "/"-separated


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: Path
    types: tuple[MeetingType, ...] = ()

    @property
    def steno_dir(self) -> Path:
        return self.path / STENO_DIRNAME

    def type_dir(self, mtype: MeetingType) -> Path:
        return self.path.joinpath(*mtype.folder.split("/"))

    def find_type(self, name: str | None) -> MeetingType | None:
        if not name:
            return None
        for t in self.types:
            if t.name.casefold() == name.casefold():
                return t
        raise ProjectError(f"no meeting type {name!r} in {self.name}")


@dataclass(frozen=True)
class Meeting:
    session: str
    title: str
    folder: Path


# --------------------------------------------------------------------------- registry


def project_id(path: Path) -> str:
    key = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _registry() -> Path:
    return paths.data_dir() / REGISTRY_NAME


def _load_entries() -> list[dict]:
    try:
        data = json.loads(_registry().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [p for p in data.get("projects", []) if isinstance(p, dict) and p.get("path")]


def _save_entries(entries: list[dict]) -> None:
    atomic_write_text(_registry(), json.dumps(
        {"projects": entries}, indent=2, ensure_ascii=False) + "\n")


def _types_of(entry: dict) -> tuple[MeetingType, ...]:
    out = []
    for t in entry.get("types") or []:
        if isinstance(t, dict) and t.get("name") and t.get("folder"):
            try:
                out.append(MeetingType(str(t["name"]), clean_type_folder(str(t["folder"]))))
            except ProjectError:
                log.warning("ignoring a meeting type with a bad folder: %r", t.get("folder"))
    return tuple(out)


def list_projects() -> list[Project]:
    """Registered projects whose folder still exists, in the order they were added."""
    out = []
    for entry in _load_entries():
        path = Path(entry["path"])
        if path.is_dir():
            out.append(Project(project_id(path), path.name, path, _types_of(entry)))
    return out


def clean_type_folder(folder: str) -> str:
    """A safe folder path inside the project: ``Meetings/Board``; no ``..``, not ``Steno``."""
    raw = (folder or "").strip()
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ProjectError("give a folder inside the project, not a full path")
    parts = [p for p in re.split(r"[\\/]+", raw) if p.strip()]
    if not parts or len(parts) > MAX_TYPE_DEPTH:
        raise ProjectError(f"give a folder inside the project, at most {MAX_TYPE_DEPTH} levels")
    out = []
    for part in parts:
        if part.strip().strip(".") == "":
            raise ProjectError("the folder must stay inside the project")
        name = clean_title(part, "")
        if not name:
            raise ProjectError(f"not a valid folder name: {part!r}")
        out.append(name)
    if len(out) == 1 and out[0].casefold() == STENO_DIRNAME.casefold():
        raise ProjectError("Steno/ holds the meetings without a type; pick another folder")
    return "/".join(out)


def set_types(pid: str, types: list[dict]) -> Project:
    """Replace a project's meeting types. Folders of types that hold meetings cannot be
    removed or moved (the meetings would fall out of the app)."""
    project = get_project(pid)
    if len(types) > MAX_TYPES:
        raise ProjectError(f"at most {MAX_TYPES} meeting types")
    new: list[MeetingType] = []
    for t in types:
        if not isinstance(t, dict):
            raise ProjectError("a meeting type is {name, folder}")
        name = clean_title(str(t.get("name") or ""), "")
        if not name:
            raise ProjectError("every meeting type needs a name")
        folder = clean_type_folder(str(t.get("folder") or f"Meetings/{name}"))
        if any(x.name.casefold() == name.casefold() for x in new):
            raise ProjectError(f"two meeting types are called {name!r}")
        if any(x.folder.casefold() == folder.casefold() for x in new):
            raise ProjectError(f"two meeting types use the folder {folder!r}")
        new.append(MeetingType(name, folder))
    kept = {t.folder.casefold() for t in new}
    for old in project.types:
        if old.folder.casefold() not in kept and _meeting_dirs(project.type_dir(old)):
            raise ProjectError(f"{old.name} has meetings: move or delete them before "
                               "removing the type or changing its folder")
    with _lock:
        entries = _load_entries()
        for entry in entries:
            if project_id(Path(entry["path"])) == pid:
                entry["types"] = [{"name": t.name, "folder": t.folder} for t in new]
        _save_entries(entries)
    project = get_project(pid)
    for t in project.types:  # the folder exists from the start: visible in Explorer, ready to use
        try:
            project.type_dir(t).mkdir(parents=True, exist_ok=True)
        except OSError:
            log.debug("Could not create the folder of %s", t.name, exc_info=True)
    write_index(project)
    return project


def get_project(pid: str) -> Project:
    for p in list_projects():
        if p.id == pid:
            return p
    raise ProjectError("no such project")


def clean_title(title: str | None, default: str = DEFAULT_TITLE) -> str:
    """A title that is also a valid folder name on Windows."""
    text = _BAD_CHARS.sub(" ", title or "").strip().strip(".")
    text = re.sub(r"\s+", " ", text)[:MAX_TITLE].strip()
    return text or default


def add_project(path: str | Path, *, name: str | None = None, create: bool = False) -> Project:
    """Register a folder (``create``: make ``path/name`` first) and set up its Steno dir."""
    base = Path(path).expanduser()
    if create:
        if not base.is_dir():
            raise ProjectError(f"folder not found: {base}")
        folder_name = clean_title(name, "")
        if not folder_name:
            raise ProjectError("give the new project a name")
        base = base / folder_name
        base.mkdir(exist_ok=True)
    if not base.is_dir():
        raise ProjectError(f"folder not found: {base}")
    base = base.resolve()
    with _lock:
        entries = _load_entries()
        if not any(project_id(Path(p["path"])) == project_id(base) for p in entries):
            entries.append({"path": str(base)})
            _save_entries(entries)
    project = get_project(project_id(base))
    ensure_steno_dir(project)
    return project


def remove_project(pid: str) -> None:
    """Forget a project. Its files stay where they are."""
    with _lock:
        entries = [p for p in _load_entries() if project_id(Path(p["path"])) != pid]
        _save_entries(entries)


def ensure_steno_dir(project: Project) -> Path:
    d = project.steno_dir
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("# Meeting transcripts are private: keep them out of git.\n*\n",
                      encoding="utf-8")
    claude = d / "CLAUDE.md"
    if not claude.exists() or _CLAUDE_MARK in claude.read_text(encoding="utf-8"):
        atomic_write_text(claude, CLAUDE_MD)
    return d


CLAUDE_MD = f"""{_CLAUDE_MARK}
# Meetings (written by Steno)

Each meeting is one folder named `YYYY-MM-DD <title>`. Meetings without a type are
here; meetings with a type (Board, 1-1, ...) are in that type's folder in the project.
`index.md` lists them all, newest first, with their path from the project folder.

How to read them (save tokens):
1. Start with `index.md`.
2. Earlier meetings: read `summary.md` and `todo.md` first; open `transcript.md` only
   when the question needs details that are not in them.
3. The meeting marked **recording** in `index.md` is live; for it, or the latest one, do
   not re-read `transcript.md`. Run `steno new --since start` the first time (from this
   project folder it picks this project's latest meeting). The output ends with a line
   `next: <token>`; on every later question run `steno new --since <that token>` and you
   get only what was said since. Lines under "Corrections" replace their earlier wording.
   If the header says it is a different meeting, the older text no longer applies.
4. If the `steno` command is missing, read `transcript.md` (rewritten every few seconds).

Transcript lines look like `[MM:SS] Speaker: text`. Speech recognition errors happen,
mostly with names and English terms inside Swedish sentences.
"""


# --------------------------------------------------------------------------- meetings


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def meeting_folder(session_dir: Path) -> Path | None:
    """The meeting folder of a session, or None (no project, or the folder is gone)."""
    folder = _read_json(Path(session_dir) / LINK_NAME).get("folder")
    if not folder:
        return None
    f = Path(folder)
    meta = _read_json(f / MEETING_FILE)
    return f if meta.get("session") == Path(session_dir).name else None


def meeting_title(session_dir: Path) -> str:
    folder = meeting_folder(session_dir)
    if folder is not None:
        return str(_read_json(folder / MEETING_FILE).get("title") or DEFAULT_TITLE)
    return str(_read_json(Path(session_dir) / LINK_NAME).get("title") or DEFAULT_TITLE)


def _key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _place(folder: Path) -> tuple[Project, MeetingType | None] | None:
    """Which project (and meeting type) a meeting folder belongs to, from where it lies."""
    parent = _key(folder.parent)
    for p in list_projects():
        if parent == _key(p.steno_dir):
            return p, None
        for t in p.types:
            if parent == _key(p.type_dir(t)):
                return p, t
    return None


def project_of(session_dir: Path) -> Project | None:
    folder = meeting_folder(session_dir)
    place = _place(folder) if folder is not None else None
    return place[0] if place else None


def type_of(session_dir: Path) -> str | None:
    """The meeting type's name, or None (no project, or no type)."""
    folder = meeting_folder(session_dir)
    place = _place(folder) if folder is not None else None
    return place[1].name if place and place[1] else None


def _prefix(session_id: str) -> str:
    """``20261119-103005`` -> ``2026-11-19`` (folder names: date and title)."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})-.*", session_id)
    return f"{m[1]}-{m[2]}-{m[3]}" if m else session_id


def _when(session_id: str) -> str:
    """``20261119-103005`` -> ``2026-11-19 10.30`` (shown inside transcripts)."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}.*", session_id)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}.{m[5]}" if m else session_id


def _parent_dir(project: Project, mtype: MeetingType | None) -> Path:
    ensure_steno_dir(project)
    if mtype is None:
        return project.steno_dir
    d = project.type_dir(mtype)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meeting_dirs(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return [f for f in parent.iterdir() if f.is_dir() and (f / MEETING_FILE).is_file()]


def _free_folder(parent: Path, name: str, keep: Path | None = None) -> Path:
    candidate, n = parent / name, 2
    while candidate.exists() and candidate != keep:
        candidate, n = parent / f"{name} ({n})", n + 1
    return candidate


def _link(session_dir: Path, folder: Path, title: str) -> None:
    gi = folder / ".gitignore"
    if not gi.exists():
        gi.write_text("# Meeting text is private: keep it out of git.\n*\n", encoding="utf-8")
    atomic_write_text(folder / MEETING_FILE, json.dumps(
        {"session": session_dir.name, "title": title}, ensure_ascii=False) + "\n")
    atomic_write_text(session_dir / LINK_NAME, json.dumps(
        {"folder": str(folder), "title": title}, ensure_ascii=False) + "\n")


def create_meeting(project: Project, session_dir: Path, title: str | None = None,
                   mtype: str | None = None) -> Meeting:
    title = clean_title(title)
    kind = project.find_type(mtype)
    with _lock:
        parent = _parent_dir(project, kind)
        folder = _free_folder(parent, f"{_prefix(session_dir.name)} {title}")
        folder.mkdir()
        _link(session_dir, folder, title)
    sync(session_dir, recording=True)
    write_index(project, recording=session_dir.name)
    return Meeting(session_dir.name, title, folder)


def list_meetings(project: Project) -> list[Meeting]:
    """Meetings of a project (``Steno/`` and every type folder), oldest first."""
    out = []
    seen: set[str] = set()
    for parent in [project.steno_dir, *(project.type_dir(t) for t in project.types)]:
        for f in _meeting_dirs(parent):
            meta = _read_json(f / MEETING_FILE)
            sid = str(meta.get("session") or "")
            if sid and sid not in seen:
                seen.add(sid)
                out.append(Meeting(sid, str(meta.get("title") or ""), f))
    return sorted(out, key=lambda m: m.session)


def inbox_sessions() -> list[transcript.SessionInfo]:
    """Sessions without a (still existing) meeting folder, oldest first."""
    return [s for s in transcript.list_sessions() if meeting_folder(s.dir) is None]


def rename_meeting(session_dir: Path, title: str) -> Meeting:
    title = clean_title(title)
    folder = meeting_folder(session_dir)
    if folder is None:  # inbox: only the name is kept
        atomic_write_text(Path(session_dir) / LINK_NAME,
                          json.dumps({"title": title}, ensure_ascii=False) + "\n")
        return Meeting(Path(session_dir).name, title, Path(session_dir))
    with _lock:
        target = _free_folder(folder.parent, f"{_prefix(Path(session_dir).name)} {title}",
                              keep=folder)
        if target != folder:
            folder.rename(target)
        _link(Path(session_dir), target, title)
    sync(session_dir)
    project = project_of(session_dir)
    if project is not None:
        write_index(project)
    return Meeting(Path(session_dir).name, title, target)


def move_meeting(session_dir: Path, project: Project, mtype: str | None = None) -> Meeting:
    session_dir = Path(session_dir)
    old_project = project_of(session_dir)
    folder = meeting_folder(session_dir)
    title = meeting_title(session_dir)
    kind = project.find_type(mtype)
    with _lock:
        parent = _parent_dir(project, kind)
        if folder is not None and _key(folder.parent) == _key(parent):
            return Meeting(session_dir.name, title, folder)  # already there
        target = _free_folder(parent, f"{_prefix(session_dir.name)} {title}")
        if folder is None:
            target.mkdir()
        else:
            shutil.move(str(folder), str(target))
        _link(session_dir, target, title)
    sync(session_dir)
    write_index(project)
    if old_project is not None and old_project.id != project.id:
        write_index(old_project)
    return Meeting(session_dir.name, title, target)


def move_to_inbox(session_dir: Path) -> Meeting:
    """Take a meeting out of its project: it becomes an inbox session again.

    The session (transcript, summary, to-do, voices, kept audio) always lives in the data dir,
    so nothing has to be copied there; the meeting folder only holds readable copies. It is
    removed when it holds nothing else. Files that are not copies of what the session has (a
    hand-edited summary, the user's own notes) keep the folder: it then just stops being a
    meeting (its marker goes) and stays where it is."""
    session_dir = Path(session_dir)
    folder = meeting_folder(session_dir)
    title = meeting_title(session_dir)
    if folder is None:
        return Meeting(session_dir.name, title, session_dir)  # already in the inbox
    place = _place(folder)
    with _lock:
        if place is not None and _only_copies(session_dir, folder):
            shutil.rmtree(folder)
        else:
            log.warning("Meeting folder %s holds other files: kept without its marker", folder)
            (folder / MEETING_FILE).unlink(missing_ok=True)
        # last: until now the session still pointed at the folder (a failure above keeps it a
        # meeting); a link to a folder that is gone already reads as inbox
        atomic_write_text(session_dir / LINK_NAME,
                          json.dumps({"title": title}, ensure_ascii=False) + "\n")
    if place is not None:
        write_index(place[0])
    return Meeting(session_dir.name, title, session_dir)


def _only_copies(session_dir: Path, folder: Path) -> bool:
    """True when everything in the meeting folder is written by Steno from the session: the
    marker, .gitignore, transcript.md and summary/to-do identical to the session's own."""
    for f in folder.iterdir():
        if f.name in (MEETING_FILE, ".gitignore", "transcript.md") and f.is_file():
            continue
        mine = session_dir / f.name
        if f.name in COPIED and f.is_file() and mine.is_file():
            try:
                if f.read_text(encoding="utf-8") == mine.read_text(encoding="utf-8"):
                    continue
            except (OSError, ValueError):
                pass
        return False
    return True


_SESSION_REF = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PROJECT_REF = re.compile(r"[0-9a-f]{12}")


def folder_to_open(kind: str, ref: str | None = None, mtype: str | None = None) -> Path | None:
    """The folder "Open in File Explorer" shows, found from ids and names only (never a path
    from the page). ``kind``: ``meeting`` (``ref`` = session id: its meeting folder, or for an
    inbox session its session folder), ``project`` (``ref`` = project id), ``type`` (a project's
    subfolder: ``ref`` = project id, ``mtype`` = its name; if the folder is not there yet the
    nearest existing parent inside the project), ``inbox`` (where inbox sessions live).
    None when nothing suitable exists."""
    if kind == "inbox":
        return paths.sessions_dir()
    if not isinstance(ref, str):
        return None
    if kind == "meeting":
        if not _SESSION_REF.fullmatch(ref):
            return None
        d = transcript.resolve_session(ref)
        if d is None:
            return None
        return meeting_folder(d) or d
    if kind not in ("project", "type") or not _PROJECT_REF.fullmatch(ref):
        return None
    try:
        project = get_project(ref)
        kind_ = project.find_type(mtype) if kind == "type" and mtype else None
    except ProjectError:
        return None
    if kind_ is None:
        return project.path
    root = Path(os.path.abspath(project.path))
    folder = Path(os.path.abspath(project.type_dir(kind_)))
    if root not in folder.parents:  # a folder that does not stay inside the project
        return None
    while not folder.is_dir() and folder != root:
        folder = folder.parent
    return folder


def is_archived(session_dir: Path) -> bool:
    return (Path(session_dir) / ARCHIVED_NAME).is_file()


def set_archived(session_dir: Path, archived: bool) -> None:
    """Archived meetings stay on disk and in the meeting folder; the app just hides them."""
    marker = Path(session_dir) / ARCHIVED_NAME
    if archived:
        atomic_write_text(marker, json.dumps(
            {"archived_at": datetime.now().isoformat(timespec="seconds")}) + "\n")
    else:
        marker.unlink(missing_ok=True)


def delete_meeting(session_dir: Path) -> None:
    """Delete a meeting for good: its session dir (transcript, embeddings, audio) and its
    meeting folder in the project. Only a folder whose marker names this session is removed."""
    session_dir = Path(session_dir)
    folder = meeting_folder(session_dir)
    place = _place(folder) if folder is not None else None
    project = place[0] if place else None
    with _lock:
        if folder is not None and place is not None:  # only where Steno puts meetings
            shutil.rmtree(folder)
        shutil.rmtree(session_dir)
    if project is not None:
        write_index(project)


# --------------------------------------------------------------------------- files


def render_transcript(session_dir: Path, title: str, *, recording: bool = False) -> str:
    from steno import llm

    segments, kind = llm.load_transcript(session_dir)
    state = "recording, lines may still improve" if recording else f"{kind} text"
    head = f"# {title}\n\n_{_when(session_dir.name)} · {state}_\n\n"
    return head + transcript.render_markdown(segments) + "\n"


_LOCK_RETRIES = 4  # tries per write; 0.05 s, 0.1 s, 0.2 s between them
_LOCK_BACKOFF = 0.05


def _write_synced(path: Path, text: str) -> bool:
    """Atomic write into a meeting folder, tolerant of another program holding the file.

    On Windows Explorer's preview pane, OneDrive, an editor or a virus scanner can lock
    ``transcript.md`` (or its ``.tmp``) for a moment: ``PermissionError``. Retry a few times
    with a short backoff; if it stays locked return False and let the caller skip this round
    (the file is rewritten every few seconds while recording, and once more at the end).
    Other errors still raise."""
    delay = _LOCK_BACKOFF
    for attempt in range(_LOCK_RETRIES):
        try:
            atomic_write_text(path, text)
            return True
        except PermissionError:
            if attempt + 1 < _LOCK_RETRIES:
                time.sleep(delay)
                delay *= 2
    try:
        path.with_name(path.name + ".tmp").unlink(missing_ok=True)  # best effort: not left behind
    except OSError:
        pass
    log.debug("%s is locked by another program, skipped this round", path)
    return False


def sync(session_dir: Path, *, recording: bool = False, docs: bool = True) -> Path | None:
    """Write the readable files of a session into its meeting folder (if it has one)."""
    session_dir = Path(session_dir)
    folder = meeting_folder(session_dir)
    if folder is None:
        return None
    title = meeting_title(session_dir)
    try:
        _write_synced(folder / "transcript.md",
                      render_transcript(session_dir, title, recording=recording))
        if docs:
            for name in COPIED:
                src = session_dir / name
                if src.is_file():
                    _write_synced(folder / name, src.read_text(encoding="utf-8"))
    except OSError:
        log.exception("Could not write the meeting folder %s", folder)
    return folder


def write_index(project: Project, recording: str | None = None) -> None:
    """``Steno/index.md``: newest first; ``recording`` is the session being recorded."""
    lines = [f"# Meetings in {project.name}", "",
             "Newest first; paths from the project folder. Files: transcript.md always; "
             "summary.md / todo.md when made.", ""]
    for m in reversed(list_meetings(project)):
        files = ["transcript.md"] + [n for n in COPIED if (m.folder / n).is_file()]
        mark = " — **recording**" if m.session == recording else ""
        place = _place(m.folder)
        kind = f" ({place[1].name})" if place and place[1] else ""
        try:
            rel = m.folder.relative_to(project.path).as_posix()
        except ValueError:
            rel = m.folder.name
        lines.append(f"- `{rel}/`{kind}{mark}: {', '.join(files)}")
    try:
        atomic_write_text(ensure_steno_dir(project) / "index.md", "\n".join(lines) + "\n")
    except OSError:
        log.exception("Could not write %s", project.steno_dir / "index.md")
