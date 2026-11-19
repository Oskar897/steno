"""Projects: folders you pick on disk; every meeting gets a readable folder inside them.

Layout::

    <project folder>/
      Steno/
        .gitignore            # "*": meeting text never ends up in a git repo by accident
        CLAUDE.md             # how Claude Code should read the meetings (cheap first)
        index.md              # one line per meeting, newest first
        2026-11-19 10.30 Sprint planning/
          .steno-meeting.json # {"session": "<id>", "title": "..."}
          transcript.md       # best text so far; rewritten while recording
          summary.md, todo.md # only after you asked for them

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
from dataclasses import dataclass
from pathlib import Path

from steno import paths, transcript
from steno.transcript import atomic_write_text

log = logging.getLogger(__name__)

STENO_DIRNAME = "Steno"
MEETING_FILE = ".steno-meeting.json"
LINK_NAME = "meeting.json"  # in the session dir
REGISTRY_NAME = "projects.json"
DEFAULT_TITLE = "Meeting"
MAX_TITLE = 80
COPIED = ("summary.md", "todo.md")
_CLAUDE_MARK = "<!-- steno:generated -->"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_lock = threading.RLock()


class ProjectError(ValueError):
    """A request about projects that cannot be honoured; the message is shown to the user."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: Path

    @property
    def steno_dir(self) -> Path:
        return self.path / STENO_DIRNAME


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


def _load() -> list[str]:
    try:
        data = json.loads(_registry().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [p["path"] for p in data.get("projects", []) if isinstance(p, dict) and p.get("path")]


def _save(items: list[str]) -> None:
    atomic_write_text(_registry(), json.dumps(
        {"projects": [{"path": p} for p in items]}, indent=2, ensure_ascii=False) + "\n")


def list_projects() -> list[Project]:
    """Registered projects whose folder still exists, in the order they were added."""
    out = []
    for p in _load():
        path = Path(p)
        if path.is_dir():
            out.append(Project(project_id(path), path.name, path))
    return out


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
        items = _load()
        if not any(project_id(Path(p)) == project_id(base) for p in items):
            items.append(str(base))
            _save(items)
    project = Project(project_id(base), base.name, base)
    ensure_steno_dir(project)
    return project


def remove_project(pid: str) -> None:
    """Forget a project. Its files stay where they are."""
    with _lock:
        items = [p for p in _load() if project_id(Path(p)) != pid]
        _save(items)


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

Each folder here is one meeting, named `YYYY-MM-DD HH.MM <title>`; the newest sorts last.
`index.md` lists them all, newest first, with what each folder contains.

How to read them (save tokens):
1. Start with `index.md`.
2. For earlier meetings read `summary.md` and `todo.md` first; open `transcript.md` only
   when the question needs details that are not in them.
3. The meeting marked **recording** in `index.md` is happening right now: its
   `transcript.md` is rewritten every few seconds with the best text so far (lines
   may still improve). Re-read it when asked about the meeting "now".

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


def project_of(session_dir: Path) -> Project | None:
    folder = meeting_folder(session_dir)
    if folder is None:
        return None
    root = folder.parent.parent
    for p in list_projects():
        if project_id(p.path) == project_id(root):
            return p
    return None


def _prefix(session_id: str) -> str:
    """``20261119-103005`` -> ``2026-11-19 10.30``."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}.*", session_id)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}.{m[5]}" if m else session_id


def _free_folder(parent: Path, name: str, keep: Path | None = None) -> Path:
    candidate, n = parent / name, 2
    while candidate.exists() and candidate != keep:
        candidate, n = parent / f"{name} ({n})", n + 1
    return candidate


def _link(session_dir: Path, folder: Path, title: str) -> None:
    atomic_write_text(folder / MEETING_FILE, json.dumps(
        {"session": session_dir.name, "title": title}, ensure_ascii=False) + "\n")
    atomic_write_text(session_dir / LINK_NAME, json.dumps(
        {"folder": str(folder), "title": title}, ensure_ascii=False) + "\n")


def create_meeting(project: Project, session_dir: Path, title: str | None = None) -> Meeting:
    title = clean_title(title)
    with _lock:
        parent = ensure_steno_dir(project)
        folder = _free_folder(parent, f"{_prefix(session_dir.name)} {title}")
        folder.mkdir()
        _link(session_dir, folder, title)
    sync(session_dir, recording=True)
    write_index(project, recording=session_dir.name)
    return Meeting(session_dir.name, title, folder)


def list_meetings(project: Project) -> list[Meeting]:
    """Meetings of a project, oldest first."""
    d = project.steno_dir
    out = []
    if d.is_dir():
        for f in d.iterdir():
            meta = _read_json(f / MEETING_FILE) if f.is_dir() else {}
            if meta.get("session"):
                out.append(Meeting(str(meta["session"]), str(meta.get("title") or ""), f))
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


def move_meeting(session_dir: Path, project: Project) -> Meeting:
    session_dir = Path(session_dir)
    old_project = project_of(session_dir)
    folder = meeting_folder(session_dir)
    title = meeting_title(session_dir)
    with _lock:
        parent = ensure_steno_dir(project)
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


# --------------------------------------------------------------------------- files


def render_transcript(session_dir: Path, title: str, *, recording: bool = False) -> str:
    from steno import llm

    segments, kind = llm.load_transcript(session_dir)
    state = "recording, lines may still improve" if recording else f"{kind} text"
    head = f"# {title}\n\n_{_prefix(session_dir.name)} · {state}_\n\n"
    return head + transcript.render_markdown(segments) + "\n"


def sync(session_dir: Path, *, recording: bool = False, docs: bool = True) -> Path | None:
    """Write the readable files of a session into its meeting folder (if it has one)."""
    session_dir = Path(session_dir)
    folder = meeting_folder(session_dir)
    if folder is None:
        return None
    title = meeting_title(session_dir)
    try:
        atomic_write_text(folder / "transcript.md",
                          render_transcript(session_dir, title, recording=recording))
        if docs:
            for name in COPIED:
                src = session_dir / name
                if src.is_file():
                    atomic_write_text(folder / name, src.read_text(encoding="utf-8"))
    except OSError:
        log.exception("Could not write the meeting folder %s", folder)
    return folder


def write_index(project: Project, recording: str | None = None) -> None:
    """``Steno/index.md``: newest first; ``recording`` is the session being recorded."""
    lines = [f"# Meetings in {project.name}", "",
             "Newest first. Files: transcript.md always; summary.md / todo.md when made.", ""]
    for m in reversed(list_meetings(project)):
        files = ["transcript.md"] + [n for n in COPIED if (m.folder / n).is_file()]
        mark = " — **recording**" if m.session == recording else ""
        lines.append(f"- `{m.folder.name}/`{mark}: {', '.join(files)}")
    try:
        atomic_write_text(ensure_steno_dir(project) / "index.md", "\n".join(lines) + "\n")
    except OSError:
        log.exception("Could not write %s", project.steno_dir / "index.md")
