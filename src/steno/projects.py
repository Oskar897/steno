"""Projects: labels for your meetings, with subfolders that are folders anywhere on disk.

A project is a name ("Chess Club", "Thesis"), optionally linked to a folder. It writes no
files by itself. A meeting of a project without a subfolder lives only in Steno's own
storage (the data dir); its ``meeting.json`` names the project. A **subfolder** is a name
plus any folder on disk (absolute path, e.g. a meetings folder in Google Drive); every
meeting recorded there gets a readable folder inside it::

    <subfolder folder>/
      2026-11-22 One-on-One/
        .gitignore            # "*", only inside a git repo: meeting text is never committed
        .steno-meeting.json   # {"session": "<id>", "title": "..."}, hidden on Windows
        agenda.md             # the user's own files are never touched
        transcript.md         # best text so far; rewritten while recording
        summary.md, todo.md   # only after you asked for them

A folder belongs to one subfolder of one project only. A subfolder whose folder is missing
(drive offline, moved) is kept: recordings then fall back to Steno's storage, and "Save
to..." writes them out later.

Older registries are still read: projects that are a folder (id from the path) with a
``Steno/`` folder for meetings without a subfolder, and subfolder folders relative to the
project folder.

The session itself (JSONL, revisions, voice windows, kept audio) stays in the global data
dir (``steno.paths``); its ``meeting.json`` points to the meeting folder (or names the
project). Voice profiles are global too. The registry is ``<data dir>/projects.json``.
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
import uuid
from collections.abc import Callable
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
_INDEX_HEAD = "# Meetings in "
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_lock = threading.RLock()


MAX_TYPES = 30
MAX_TYPE_DEPTH = 4
MAX_PROJECTS = 200


class ProjectError(ValueError):
    """A request about projects that cannot be honoured; the message is shown to the user."""


@dataclass(frozen=True)
class MeetingType:
    """A subfolder: a name and its folder (absolute; older ones relative to the project)."""

    name: str
    folder: str


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: Path | None = None  # the linked folder, if any
    types: tuple[MeetingType, ...] = ()
    archived: bool = False

    @property
    def steno_dir(self) -> Path | None:
        """``<folder>/Steno`` of an older project (meetings without a subfolder were there)."""
        return self.path / STENO_DIRNAME if self.path is not None else None

    def type_dir(self, mtype: MeetingType) -> Path:
        folder = Path(mtype.folder)
        if folder.is_absolute() or self.path is None:
            return folder
        return self.path.joinpath(*mtype.folder.split("/"))

    def find_type(self, name: str | None) -> MeetingType | None:
        if not name:
            return None
        for t in self.types:
            if t.name.casefold() == name.casefold():
                return t
        raise ProjectError(f"no subfolder {name!r} in {self.name}")


@dataclass(frozen=True)
class Meeting:
    session: str
    title: str
    folder: Path | None  # None: kept in Steno's storage only (no files written)


# --------------------------------------------------------------------------- registry


_PROJECT_REF = re.compile(r"[0-9a-f]{12}")
_SESSION_REF = re.compile(r"[A-Za-z0-9_-]{1,64}")


def project_id(path: Path) -> str:
    """The id of an older project: derived from its folder."""
    key = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _registry() -> Path:
    return paths.data_dir() / REGISTRY_NAME


def _entry_id(entry: dict) -> str:
    pid = str(entry.get("id") or "")
    return pid if _PROJECT_REF.fullmatch(pid) else project_id(Path(entry["path"]))


def _load_entries() -> list[dict]:
    try:
        data = json.loads(_registry().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    out = []
    for p in data.get("projects", []) if isinstance(data, dict) else []:
        if isinstance(p, dict) and (
                p.get("path") or _PROJECT_REF.fullmatch(str(p.get("id") or ""))):
            out.append(p)
    return out


def _save_entries(entries: list[dict]) -> None:
    for entry in entries:  # older entries get their id and name written down: they no
        entry["id"] = _entry_id(entry)  # longer follow the folder once it can change
        if not entry.get("name") and entry.get("path"):
            entry["name"] = Path(entry["path"]).name
    atomic_write_text(_registry(), json.dumps(
        {"projects": entries}, indent=2, ensure_ascii=False) + "\n")


def _types_of(entry: dict, path: Path | None) -> tuple[MeetingType, ...]:
    out = []
    for t in entry.get("types") or []:
        if not (isinstance(t, dict) and t.get("name") and t.get("folder")):
            continue
        folder = str(t["folder"])
        if Path(folder).is_absolute():
            out.append(MeetingType(str(t["name"]), folder))
            continue
        try:
            if path is None:
                raise ProjectError("a relative subfolder needs a project folder")
            out.append(MeetingType(str(t["name"]), clean_type_folder(folder)))
        except ProjectError:
            log.warning("ignoring a subfolder with a bad folder: %r", folder)
    return tuple(out)


def _project(entry: dict) -> Project:
    path = Path(entry["path"]) if entry.get("path") else None
    name = clean_title(str(entry.get("name") or ""), "") or (path.name if path else "Project")
    return Project(_entry_id(entry), name, path, _types_of(entry, path),
                   bool(entry.get("archived")))


def list_projects() -> list[Project]:
    """Registered projects (archived ones included), in the order they were added."""
    return [_project(e) for e in _load_entries()]


def get_project(pid: str) -> Project:
    for p in list_projects():
        if p.id == pid:
            return p
    raise ProjectError("no such project")


def _update_entry(pid: str, change: Callable[[dict], None]) -> Project:
    """Apply ``change(entry)`` to one project's registry entry and save."""
    with _lock:
        entries = _load_entries()
        for entry in entries:
            if _entry_id(entry) == pid:
                entry["id"] = pid
                change(entry)
                break
        else:
            raise ProjectError("no such project")
        _save_entries(entries)
    return get_project(pid)


def clean_title(title: str | None, default: str = DEFAULT_TITLE) -> str:
    """A title that is also a valid folder name on Windows."""
    text = _BAD_CHARS.sub(" ", title or "").strip().strip(".")
    text = re.sub(r"\s+", " ", text)[:MAX_TITLE].strip()
    return text or default


def _check_name_free(name: str, skip: str | None = None) -> None:
    for p in list_projects():
        if p.id != skip and p.name.casefold() == name.casefold():
            raise ProjectError(f"there is already a project called {p.name}")


def _existing_folder(folder: str | Path) -> Path:
    """A folder the user picked: absolute, existing, not inside Steno's own storage."""
    f = Path(str(folder).strip()).expanduser()
    if not f.is_absolute():
        raise ProjectError("pick a folder (a full path)")
    if not f.is_dir():
        raise ProjectError(f"folder not found: {f}")
    f = f.resolve()
    data = Path(os.path.abspath(paths.data_dir()))
    if f == data or data in f.parents:
        raise ProjectError("that folder is Steno's own storage; pick another one")
    return f


def create_project(name: str, folder: str | Path | None = None) -> Project:
    """A new project: a name, optionally linked to an existing folder. Writes no files."""
    name = clean_title(name, "")
    if not name:
        raise ProjectError("give the project a name")
    path = _existing_folder(folder) if folder else None
    with _lock:
        entries = _load_entries()
        if len(entries) >= MAX_PROJECTS:
            raise ProjectError(f"at most {MAX_PROJECTS} projects")
        _check_name_free(name)
        pid = uuid.uuid4().hex[:12]
        entry: dict = {"id": pid, "name": name}
        if path is not None:
            entry["path"] = str(path)
        _save_entries([*entries, entry])
    return get_project(pid)


def add_project(path: str | Path, *, name: str | None = None, create: bool = False) -> Project:
    """Register a folder as a project named after it (``create``: make ``path/name`` first).
    Adding the same folder twice returns the existing project. Writes no files."""
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
    key = _key(base)
    with _lock:
        entries = _load_entries()
        for entry in entries:
            if entry.get("path") and _key(Path(entry["path"])) == key:
                return get_project(_entry_id(entry))
        entries.append({"id": project_id(base), "name": base.name, "path": str(base)})
        _save_entries(entries)
    return get_project(project_id(base))


def rename_project(pid: str, name: str) -> Project:
    """Only the label changes; no folder is renamed."""
    name = clean_title(name, "")
    if not name:
        raise ProjectError("give the project a name")
    get_project(pid)
    _check_name_free(name, skip=pid)
    return _update_entry(pid, lambda e: e.__setitem__("name", name))


def set_project_folder(pid: str, folder: str | Path | None) -> Project:
    """Link the project to a folder (``None``: unlink). Subfolders keep their own folders
    (older ones relative to the project folder are written down as full paths first)."""
    project = get_project(pid)
    path = _existing_folder(folder) if folder else None

    def change(entry: dict) -> None:
        entry["name"] = project.name
        entry["types"] = [{"name": t.name, "folder": str(project.type_dir(t))}
                          for t in project.types]
        if path is None:
            entry.pop("path", None)
        else:
            entry["path"] = str(path)

    return _update_entry(pid, change)


def set_project_archived(pid: str, archived: bool) -> Project:
    """Archived projects are hidden in the sidebar (under "Archived"); nothing is deleted."""
    def change(entry: dict) -> None:
        if archived:
            entry["archived"] = True
        else:
            entry.pop("archived", None)
    return _update_entry(pid, change)


def remove_project(pid: str) -> None:
    """Forget a project. Its files stay where they are."""
    with _lock:
        entries = [p for p in _load_entries() if _entry_id(p) != pid]
        _save_entries(entries)


# --------------------------------------------------------------------------- subfolders


def clean_type_folder(folder: str) -> str:
    """A safe folder path inside an older project: ``Meetings/Board``; no ``..``."""
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
        raise ProjectError("Steno/ is taken; pick another folder")
    return "/".join(out)


def _check_folder_free(folder: Path, skip: tuple[str, str] | None = None,
                       skip_project: str | None = None) -> None:
    """One folder belongs to one subfolder of one project only."""
    key = _key(folder)
    for p in list_projects():
        if p.id == skip_project:
            continue
        for t in p.types:
            if skip is not None and (p.id, t.name.casefold()) == skip:
                continue
            if _key(p.type_dir(t)) == key:
                raise ProjectError(f"{folder.name} is already the subfolder {t.name} "
                                   f"in {p.name}")


def _types_entry(project: Project, types: list[MeetingType]) -> Callable[[dict], None]:
    def change(entry: dict) -> None:
        entry["name"] = project.name
        entry["types"] = [{"name": t.name, "folder": t.folder} for t in types]
    return change


def set_types(pid: str, types: list[dict]) -> Project:
    """Replace a project's subfolders (``{name, folder}``; a relative folder lies inside the
    project folder and is created). Subfolders that hold meetings cannot be removed or get
    another folder this way (the meetings would fall out of the app)."""
    project = get_project(pid)
    if len(types) > MAX_TYPES:
        raise ProjectError(f"at most {MAX_TYPES} subfolders")
    new: list[MeetingType] = []
    for t in types:
        if not isinstance(t, dict):
            raise ProjectError("a subfolder is {name, folder}")
        name = clean_title(str(t.get("name") or ""), "")
        if not name:
            raise ProjectError("every subfolder needs a name")
        raw = str(t.get("folder") or f"Meetings/{name}")
        if Path(raw).is_absolute():
            folder = str(Path(raw).resolve())
        elif project.path is None:
            raise ProjectError("pick a folder for the subfolder (the project has no folder)")
        else:
            folder = clean_type_folder(raw)
        if any(x.name.casefold() == name.casefold() for x in new):
            raise ProjectError(f"two subfolders are called {name!r}")
        mt = MeetingType(name, folder)
        if any(_key(project.type_dir(x)) == _key(project.type_dir(mt)) for x in new):
            raise ProjectError(f"two subfolders use the folder {folder!r}")
        new.append(mt)
    kept = {_key(project.type_dir(t)) for t in new}
    for old in project.types:
        if _key(project.type_dir(old)) not in kept and _meeting_dirs(project.type_dir(old)):
            raise ProjectError(f"{old.name} has meetings: move or delete them before "
                               "removing the subfolder or changing its folder")
    for t in new:
        _check_folder_free(project.type_dir(t), skip_project=pid)
    project = _update_entry(pid, _types_entry(project, new))
    for t in project.types:  # an older relative folder exists from the start
        if not Path(t.folder).is_absolute():
            try:
                project.type_dir(t).mkdir(parents=True, exist_ok=True)
            except OSError:
                log.debug("Could not create the folder of %s", t.name, exc_info=True)
    write_index(project)
    return project


def _clean_type_name(project: Project, name: str, skip: str | None = None) -> str:
    name = clean_title(name, "")
    if not name:
        raise ProjectError("give the subfolder a name")
    for t in project.types:
        if t.name.casefold() == name.casefold() and t.name.casefold() != (skip or "").casefold():
            raise ProjectError(f"{project.name} already has a subfolder called {t.name}")
    return name


def add_type(pid: str, name: str | None, folder: str | Path, *, create: bool = False
             ) -> Project:
    """Add a subfolder. ``folder``: an existing folder anywhere (name defaults to the folder's
    name), or with ``create`` the place where a new folder ``name`` is made."""
    project = get_project(pid)
    if len(project.types) >= MAX_TYPES:
        raise ProjectError(f"at most {MAX_TYPES} subfolders")
    base = _existing_folder(folder)
    if create:
        name = _clean_type_name(project, name or "")
        target = base / name
        _check_folder_free(target)
        target.mkdir(exist_ok=True)
        base = target.resolve()
    else:
        name = _clean_type_name(project, name or base.name)
        _check_folder_free(base)
    types = [*project.types, MeetingType(name, str(base))]
    return _update_entry(pid, _types_entry(project, types))


def rename_type(pid: str, old: str, new: str) -> Project:
    """Only the name in Steno changes; the folder stays as it is."""
    project = get_project(pid)
    kind = project.find_type(old)
    if kind is None:
        raise ProjectError("no such subfolder")
    name = _clean_type_name(project, new, skip=kind.name)
    types = [MeetingType(name, t.folder) if t == kind else t for t in project.types]
    return _update_entry(pid, _types_entry(project, types))


def set_type_folder(pid: str, name: str, folder: str | Path) -> Project:
    """Point a subfolder at another folder (it was moved, or the drive letter changed). The
    old folder must not hold meetings any more (they would fall out of the app)."""
    project = get_project(pid)
    kind = project.find_type(name)
    if kind is None:
        raise ProjectError("no such subfolder")
    base = _existing_folder(folder)
    _check_folder_free(base, skip=(pid, kind.name.casefold()))
    old = project.type_dir(kind)
    if _key(old) != _key(base) and _meeting_dirs(old):
        raise ProjectError(f"{kind.name}'s current folder still holds meetings; move the "
                           "folder itself in File Explorer first, then pick it here")
    types = [MeetingType(t.name, str(base)) if t == kind else t for t in project.types]
    project = _update_entry(pid, _types_entry(project, types))
    _relink(base)
    return project


def _relink(parent: Path) -> None:
    """Meeting folders that were moved along with their subfolder (File Explorer, a new drive
    letter): point their sessions at where they are now."""
    with _lock:
        for f in _meeting_dirs(parent):
            meta = _read_json(f / MEETING_FILE)
            d = _session_dir_of(str(meta.get("session") or ""))
            if d is None or meeting_folder(d) is not None:
                continue  # gone, or still linked to a folder that exists
            atomic_write_text(d / LINK_NAME, json.dumps(
                {"folder": str(f), "title": str(meta.get("title") or DEFAULT_TITLE)},
                ensure_ascii=False) + "\n")


def type_available(project: Project, mtype: MeetingType) -> bool:
    return project.type_dir(mtype).is_dir()


# --------------------------------------------------------------------------- files on disk


_STENO_GITIGNORE = "# Meeting transcripts are private: keep them out of git.\n*\n"
_MEETING_GITIGNORE = "# Meeting text is private: keep it out of git.\n*\n"


def _in_git(folder: Path) -> bool:
    """True when ``folder`` lies inside a git work tree (a ``.git`` in it or a parent).

    The ``.gitignore`` files that keep meeting text out of git are only written there; in a
    plain or cloud-synced folder (Google Drive, OneDrive) they would just be clutter."""
    folder = Path(os.path.abspath(folder))
    return any((d / ".git").exists() for d in (folder, *folder.parents))


def _remove_if_ours(path: Path, text: str) -> None:
    """Delete a file Steno wrote, only while it still holds exactly what Steno wrote."""
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            path.unlink()
    except OSError:
        log.debug("Could not remove %s", path, exc_info=True)


def _remove_if_starts(path: Path, head: str) -> None:
    """Delete a generated file (it starts with Steno's own marker or heading)."""
    try:
        if path.is_file() and path.read_text(encoding="utf-8").startswith(head):
            path.unlink()
    except OSError:
        log.debug("Could not remove %s", path, exc_info=True)


def _set_hidden(path: Path, hidden: bool) -> None:
    """Windows: show or hide a file in Explorer (the meeting marker is not for reading)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        attrs = kernel32.GetFileAttributesW(str(path))
        if attrs in (-1, 0xFFFFFFFF):
            return
        new = attrs | 0x2 if hidden else attrs & ~0x2  # FILE_ATTRIBUTE_HIDDEN
        if new != attrs:
            kernel32.SetFileAttributesW(str(path), new)
    except Exception:
        log.debug("Could not change the hidden flag of %s", path, exc_info=True)


def _clean_steno_dir(project: Project) -> None:
    """An older project's ``Steno/`` folder: once it holds no meetings, take Steno's own
    files out (index.md, a generated CLAUDE.md, its .gitignore) and the folder if empty."""
    d = project.steno_dir
    if d is None or not d.is_dir() or _meeting_dirs(d):
        return
    _remove_if_starts(d / "index.md", _INDEX_HEAD)
    _remove_if_starts(d / "CLAUDE.md", _CLAUDE_MARK)
    _remove_if_ours(d / ".gitignore", _STENO_GITIGNORE)
    try:
        d.rmdir()
    except OSError:
        log.info("%s keeps files that Steno did not write", d)


# --------------------------------------------------------------------------- meetings


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def meeting_folder(session_dir: Path) -> Path | None:
    """The meeting folder of a session, or None (kept in Steno only, or the folder is gone)."""
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


def _place(folder: Path, plist: list[Project] | None = None
           ) -> tuple[Project, MeetingType | None] | None:
    """Which project (and subfolder) a meeting folder belongs to, from where it lies."""
    parent = _key(folder.parent)
    for p in list_projects() if plist is None else plist:
        if p.steno_dir is not None and parent == _key(p.steno_dir):
            return p, None
        for t in p.types:
            if parent == _key(p.type_dir(t)):
                return p, t
    return None


def _label_project(session_dir: Path) -> str | None:
    """The project id a meeting without a folder is filed under (its ``meeting.json``)."""
    pid = _read_json(Path(session_dir) / LINK_NAME).get("project")
    return pid if isinstance(pid, str) and _PROJECT_REF.fullmatch(pid) else None


def project_of(session_dir: Path, plist: list[Project] | None = None) -> Project | None:
    plist = list_projects() if plist is None else plist
    folder = meeting_folder(session_dir)
    if folder is not None:
        place = _place(folder, plist)
        return place[0] if place else None
    pid = _label_project(session_dir)
    return next((p for p in plist if p.id == pid), None) if pid else None


def type_of(session_dir: Path) -> str | None:
    """The subfolder's name, or None (no project, or no subfolder)."""
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


def _type_parent(project: Project, mtype: MeetingType) -> Path:
    """The folder new meetings of a subfolder go into. An older relative folder is created;
    a picked folder that is missing (drive offline, moved) is an error: never recreate it."""
    d = project.type_dir(mtype)
    if not Path(mtype.folder).is_absolute() and project.path is not None \
            and project.path.is_dir():
        d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir():
        raise ProjectError(f"the folder of {mtype.name} is missing ({d}); use Change folder…")
    return d


def _meeting_dirs(parent: Path | None) -> list[Path]:
    if parent is None or not parent.is_dir():
        return []
    try:
        return [f for f in parent.iterdir() if f.is_dir() and (f / MEETING_FILE).is_file()]
    except OSError:
        return []


def _free_folder(parent: Path, name: str, keep: Path | None = None) -> Path:
    candidate, n = parent / name, 2
    while candidate.exists() and candidate != keep:
        candidate, n = parent / f"{name} ({n})", n + 1
    return candidate


def _own_folder(folder: Path) -> bool:
    """True when the meeting was recorded into a folder the user made (its name is kept)."""
    return bool(_read_json(folder / MEETING_FILE).get("own_folder"))


def _link(session_dir: Path, folder: Path, title: str, own_folder: bool | None = None) -> None:
    if own_folder is None:
        own_folder = _own_folder(folder)
    gi = folder / ".gitignore"
    if not gi.exists() and _in_git(folder):
        gi.write_text(_MEETING_GITIGNORE, encoding="utf-8")
    marker = folder / MEETING_FILE
    if marker.exists():
        _set_hidden(marker, False)  # replacing a hidden file can be refused on Windows
    meta = {"session": session_dir.name, "title": title}
    if own_folder:
        meta["own_folder"] = True
    atomic_write_text(marker, json.dumps(meta, ensure_ascii=False) + "\n")
    _set_hidden(marker, True)
    atomic_write_text(session_dir / LINK_NAME, json.dumps(
        {"folder": str(folder), "title": title}, ensure_ascii=False) + "\n")


def _label(session_dir: Path, title: str, pid: str | None) -> None:
    """A meeting without a folder: its title and (if any) its project, in Steno only."""
    data: dict = {"title": title}
    if pid:
        data["project"] = pid
    atomic_write_text(Path(session_dir) / LINK_NAME,
                      json.dumps(data, ensure_ascii=False) + "\n")


#: Files Steno writes into a meeting folder; a folder that already has its own cannot be used.
STENO_FILES = ("transcript.md", *COPIED)
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}\.\d{2})?\s+")


def _claimed(folder: Path) -> bool:
    """True when the folder's marker names a session that still points at this folder."""
    d = _session_dir_of(str(_read_json(folder / MEETING_FILE).get("session") or ""))
    linked = meeting_folder(d) if d is not None else None
    return linked is not None and _key(linked) == _key(folder)


def _session_dir_of(sid: str) -> Path | None:
    """The session dir of an id read from a marker file (never "latest" or a path)."""
    if not sid or sid == "latest" or not _SESSION_REF.fullmatch(sid):
        return None
    return transcript.resolve_session(sid)


def _blocking_files(folder: Path) -> list[str]:
    return [n for n in STENO_FILES if (folder / n).exists()]


def _places(project: Project) -> list[tuple[str, MeetingType | None]]:
    out: list[tuple[str, MeetingType | None]] = []
    if project.steno_dir is not None and project.steno_dir.is_dir():
        out.append((_key(project.steno_dir), None))
    out += [(_key(project.type_dir(t)), t) for t in project.types]
    return out


def check_existing_folder(project: Project, folder: str | Path
                          ) -> tuple[MeetingType | None, str, Path]:
    """Can a meeting of ``project`` go into this existing folder (the user's own meeting
    folder with agenda and notes)? Returns ``(subfolder, title, folder)``.

    The folder must lie directly in the folder of one of the project's subfolders (that is
    where the app finds meetings), must not be a meeting already and must not have its own
    ``transcript.md``/``summary.md``/``todo.md`` (Steno would overwrite them). The title is the
    folder name without its leading date."""
    f = Path(folder)
    if not f.is_dir():
        raise ProjectError(f"folder not found: {f}")
    f = f.resolve()
    parent = _key(f.parent)
    places = _places(project)
    if any(key == _key(f) for key, _t in places):
        raise ProjectError(f"{f.name} is a subfolder itself; pick a meeting folder inside it")
    kind = next((t for key, t in places if key == parent), "none")
    if kind == "none":
        where = ", ".join(t.name for t in project.types) or "none yet"
        raise ProjectError(
            f"Pick a meeting folder that lies directly in one of {project.name}'s subfolders "
            f"(subfolders: {where}). Add the subfolder first if it is missing.")
    if _claimed(f):
        raise ProjectError(f"{f.name} already holds a Steno meeting")
    blocking = _blocking_files(f)
    if blocking:
        raise ProjectError(f"{f.name} already has its own {', '.join(blocking)}; rename "
                           "or move it first (Steno writes files with those names)")
    title = clean_title(_DATE_PREFIX.sub("", f.name))
    return (kind if isinstance(kind, MeetingType) else None), title, f


def locate_existing_folder(folder: str | Path
                           ) -> tuple[Project, MeetingType | None, str, Path]:
    """``check_existing_folder`` for whichever project has a subfolder the folder lies in."""
    f = Path(folder)
    if not f.is_dir():
        raise ProjectError(f"folder not found: {f}")
    parent = _key(f.resolve().parent)
    for p in list_projects():
        if any(key in (parent, _key(f)) for key, _t in _places(p)):
            return (p, *check_existing_folder(p, f))
    raise ProjectError("Pick a meeting folder that lies directly in one of your subfolders' "
                       "folders. Add the subfolder first if it is missing.")


def create_meeting(project: Project, session_dir: Path, title: str | None = None,
                   mtype: str | None = None, folder: str | Path | None = None) -> Meeting:
    """File a new session under a project.

    ``folder``: record into that existing folder (see ``check_existing_folder``). ``mtype``: a
    folder ``YYYY-MM-DD title`` in the subfolder's folder (an existing one of that name that is
    not a meeting yet, made by hand for the agenda, is taken as it is). Neither: the meeting
    stays in Steno's storage, no files are written. A subfolder whose folder is missing falls
    back to Steno's storage too (the folder field of the result is None then)."""
    title = clean_title(title) if title else None
    with _lock:
        if folder is not None:
            _kind, own_title, target = check_existing_folder(project, folder)
            title = title or own_title
            own = True
        else:
            title = title or DEFAULT_TITLE
            kind = project.find_type(mtype)
            try:
                parent = _type_parent(project, kind) if kind is not None else None
            except ProjectError:
                log.warning("Subfolder %s of %s is missing: kept in Steno", mtype, project.name)
                parent = None
            if parent is None:
                _label(session_dir, title, project.id)
                return Meeting(session_dir.name, title, None)
            name = f"{_prefix(session_dir.name)} {title}"
            target = parent / name
            own = target.is_dir() and not _claimed(target) and not _blocking_files(target)
            if not own:
                target = _free_folder(parent, name)
                target.mkdir()
        _link(session_dir, target, title, own_folder=own)
    sync(session_dir, recording=True)
    write_index(project, recording=session_dir.name)
    return Meeting(session_dir.name, title, target)


def _label_meetings(pid: str) -> list[Meeting]:
    """Meetings of a project that have no folder (kept in Steno's storage)."""
    root = paths.sessions_dir()
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir() and _label_project(d) == pid and meeting_folder(d) is None:
            out.append(Meeting(d.name, meeting_title(d), None))
    return out


def list_meetings(project: Project) -> list[Meeting]:
    """Meetings of a project (every subfolder, and those without a folder), oldest first."""
    out = []
    seen: set[str] = set()
    parents = [project.steno_dir, *(project.type_dir(t) for t in project.types)]
    for parent in parents:
        for f in _meeting_dirs(parent):
            meta = _read_json(f / MEETING_FILE)
            sid = str(meta.get("session") or "")
            if sid and sid not in seen:
                seen.add(sid)
                out.append(Meeting(sid, str(meta.get("title") or ""), f))
    out += [m for m in _label_meetings(project.id) if m.session not in seen]
    return sorted(out, key=lambda m: m.session)


def inbox_sessions() -> list[transcript.SessionInfo]:
    """Sessions that belong to no (still registered) project, oldest first."""
    plist = list_projects()
    return [s for s in transcript.list_sessions() if project_of(s.dir, plist) is None]


def rename_meeting(session_dir: Path, title: str) -> Meeting:
    title = clean_title(title)
    folder = meeting_folder(session_dir)
    if folder is None:  # kept in Steno: only the name changes
        _label(Path(session_dir), title, _label_project(session_dir))
        return Meeting(Path(session_dir).name, title, None)
    with _lock:
        if _own_folder(folder):  # the user's folder: only the title in Steno changes
            target = folder
        else:
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


def _detach(session_dir: Path) -> Project | None:
    """Take a meeting out of its folder (Steno's files go, the user's stay). Returns the
    project whose folder it was in. The session link is rewritten by the caller."""
    folder = meeting_folder(session_dir)
    if folder is None:
        return None
    place = _place(folder)
    if place is not None:
        _remove_steno_files(session_dir, folder)  # the folder goes only when empty then
    else:
        log.warning("Meeting folder %s is outside its project: kept without its marker",
                    folder)
        _set_hidden(folder / MEETING_FILE, False)
        (folder / MEETING_FILE).unlink(missing_ok=True)
    return place[0] if place else None


def move_meeting(session_dir: Path, project: Project, mtype: str | None = None) -> Meeting:
    """File a meeting under ``project``: into a subfolder's folder (``mtype``), or without
    one (it is then kept in Steno only; Steno's files leave its old folder)."""
    session_dir = Path(session_dir)
    old_project = project_of(session_dir)
    folder = meeting_folder(session_dir)
    title = meeting_title(session_dir)
    kind = project.find_type(mtype)
    with _lock:
        if kind is None:
            old = _detach(session_dir)
            _label(session_dir, title, project.id)
            if old is not None:
                write_index(old)
            return Meeting(session_dir.name, title, None)
        parent = _type_parent(project, kind)
        if folder is not None and _key(folder.parent) == _key(parent):
            return Meeting(session_dir.name, title, folder)  # already there
        own = folder is not None and _own_folder(folder)
        name = folder.name if own else f"{_prefix(session_dir.name)} {title}"
        target = _free_folder(parent, name)
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


def save_to_folder(session_dir: Path, folder: str | Path) -> tuple[Project, Meeting]:
    """"Save to…" an existing folder: the user's own meeting folder inside one of the
    subfolders of any project. The meeting joins that project; its files are written there
    (the folder keeps its name)."""
    session_dir = Path(session_dir)
    project, _kind, own_title, target = locate_existing_folder(folder)
    title = meeting_title(session_dir)
    if title == DEFAULT_TITLE:  # an untitled meeting is named after the user's folder
        title = own_title
    old_project = project_of(session_dir)
    with _lock:
        _detach(session_dir)
        _link(session_dir, target, title, own_folder=True)
    sync(session_dir)
    write_index(project)
    if old_project is not None and old_project.id != project.id:
        write_index(old_project)
    return project, Meeting(session_dir.name, title, target)


def move_to_inbox(session_dir: Path) -> Meeting:
    """Take a meeting out of its project: it becomes an inbox session again.

    The session (transcript, summary, to-do, voices, kept audio) always lives in the data dir,
    so nothing has to be copied there; the meeting folder only holds readable copies. It is
    removed when it holds nothing else. Files that are not copies of what the session has (a
    hand-edited summary, the user's own notes) keep the folder: it then just stops being a
    meeting (its marker goes) and stays where it is."""
    session_dir = Path(session_dir)
    title = meeting_title(session_dir)
    with _lock:
        old = _detach(session_dir)
        # last: until now the session still pointed at the folder (a failure above keeps it a
        # meeting); a link to a folder that is gone already reads as Steno-only
        _label(session_dir, title, None)
    if old is not None:
        write_index(old)
    return Meeting(session_dir.name, title, None)


def folder_to_open(kind: str, ref: str | None = None, mtype: str | None = None) -> Path | None:
    """The folder "Open in File Explorer" shows, found from ids and names only (never a path
    from the page). ``kind``: ``meeting`` (``ref`` = session id: its meeting folder, or for a
    meeting kept in Steno its session folder), ``project`` (``ref`` = project id: its linked
    folder), ``type`` (a subfolder: ``ref`` = project id, ``mtype`` = its name; an older one
    that is not there yet gives the nearest existing parent inside the project), ``inbox``
    (where Steno's sessions live). None when nothing suitable exists."""
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
        return project.path if project.path is not None and project.path.is_dir() else None
    if Path(kind_.folder).is_absolute():
        folder = Path(kind_.folder)
        return folder if folder.is_dir() else None
    if project.path is None:
        return None
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


def _remove_steno_files(session_dir: Path | None, folder: Path) -> None:
    """Take Steno's files out of a meeting folder; the folder goes only if it is empty then.

    Removed: the marker, Steno's own ``.gitignore``, ``transcript.md`` and ``summary.md`` /
    ``todo.md`` while they are identical to the session's copy. Everything else (the user's
    agenda and notes, a hand-edited summary) stays, and so does the folder that holds it."""
    (folder / "transcript.md").unlink(missing_ok=True)
    for name in COPIED:
        mine = session_dir / name if session_dir is not None else None
        if mine is not None and mine.is_file():
            _remove_if_ours(folder / name, mine.read_text(encoding="utf-8"))
    _remove_if_ours(folder / ".gitignore", _MEETING_GITIGNORE)
    marker = folder / MEETING_FILE
    if marker.exists():
        _set_hidden(marker, False)
        marker.unlink()
    try:
        folder.rmdir()  # only succeeds when nothing is left
    except OSError:
        log.info("Meeting folder %s keeps the user's own files", folder)


def delete_meeting(session_dir: Path) -> None:
    """Delete a meeting for good: its session dir (transcript, embeddings, audio) and Steno's
    files in its meeting folder. The folder itself only goes when nothing else is in it, so
    the user's own files (agenda, notes) are never deleted. Only a folder whose marker names
    this session is touched."""
    session_dir = Path(session_dir)
    folder = meeting_folder(session_dir)
    place = _place(folder) if folder is not None else None
    project = place[0] if place else None
    with _lock:
        if folder is not None and place is not None:  # only where Steno puts meetings
            _remove_steno_files(session_dir, folder)
        shutil.rmtree(session_dir)
    if project is not None:
        write_index(project)


def type_meetings(project: Project, mtype: MeetingType | None) -> list[Meeting]:
    """The meetings in one subfolder (``None``: those without a subfolder: kept in Steno, or
    in an older project's ``Steno/`` folder)."""
    if mtype is None:
        out = [Meeting(str(_read_json(f / MEETING_FILE).get("session") or ""),
                       meeting_title_of(f), f) for f in _meeting_dirs(project.steno_dir)]
        out += _label_meetings(project.id)
    else:
        out = [Meeting(str(_read_json(f / MEETING_FILE).get("session") or ""),
                       meeting_title_of(f), f) for f in _meeting_dirs(project.type_dir(mtype))]
    return sorted(out, key=lambda m: m.session)


def all_meetings(project: Project) -> list[Meeting]:
    """``type_meetings`` of every subfolder and of none, oldest first."""
    found = [*type_meetings(project, None),
             *(m for t in project.types for m in type_meetings(project, t))]
    return sorted(found, key=lambda m: m.session)


def meeting_title_of(folder: Path) -> str:
    return str(_read_json(folder / MEETING_FILE).get("title") or DEFAULT_TITLE)


def _delete_folder_meeting(m: Meeting) -> None:
    """Delete a meeting found by its folder (its session may already be gone)."""
    d = _session_dir_of(m.session)
    if m.folder is None:
        if d is not None:
            delete_meeting(d)
        return
    linked = meeting_folder(d) if d is not None else None
    if linked is not None and _key(linked) == _key(m.folder):
        delete_meeting(d)
    else:  # an orphaned marker: only Steno's files go
        with _lock:
            _remove_steno_files(None, m.folder)


def _prune_empty(folder: Path, root: Path) -> None:
    """Remove ``folder`` and then its parents while they are empty, never ``root`` itself."""
    root = Path(os.path.abspath(root))
    folder = Path(os.path.abspath(folder))
    while folder != root and root in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def delete_type(pid: str, name: str, *, meetings: bool = True) -> int:
    """Remove a subfolder from Steno. ``meetings``: also delete its meetings (as
    ``delete_meeting``); otherwise they stay in the project, kept in Steno only, and their
    files stay in the folder (only Steno's marker goes). The folder itself is never deleted
    (an older one inside the project folder only while empty). Returns how many meetings were
    deleted."""
    project = get_project(pid)
    kind = project.find_type(name)
    if kind is None:
        raise ProjectError("no such subfolder")
    found = type_meetings(project, kind)
    count = 0
    for m in found:
        if meetings:
            _delete_folder_meeting(m)
            count += 1
            continue
        d = _session_dir_of(m.session)
        linked = meeting_folder(d) if d is not None else None
        if d is not None and linked is not None and _key(linked) == _key(m.folder):
            with _lock:
                title = meeting_title(d)
                _set_hidden(m.folder / MEETING_FILE, False)
                (m.folder / MEETING_FILE).unlink(missing_ok=True)
                _label(d, title, pid)
    project = _update_entry(pid, _types_entry(
        project, [t for t in project.types if t != kind]))
    if meetings and not Path(kind.folder).is_absolute() and project.path is not None:
        _prune_empty(project.type_dir(kind), project.path)
    write_index(project)
    return count


def delete_project(pid: str, *, meetings: bool) -> int:
    """Remove a project from Steno. ``meetings``: also delete all its meetings (as
    ``delete_meeting``); otherwise they go to the inbox (files stay where they are). An older
    project's ``Steno/`` folder loses Steno's own files once it holds no meetings. Folders and
    every file Steno did not write stay. Returns how many meetings were deleted."""
    project = get_project(pid)
    count = 0
    if meetings:
        found = all_meetings(project)
        for m in found:
            _delete_folder_meeting(m)
        count = len(found)
        if project.path is not None:
            for t in project.types:
                if not Path(t.folder).is_absolute():
                    _prune_empty(project.type_dir(t), project.path)
    _clean_steno_dir(project)
    remove_project(pid)
    return count


# --------------------------------------------------------------------------- readable files


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
    """``Steno/index.md`` of an older project that still has that folder: newest first;
    ``recording`` is the session being recorded. New projects write no index."""
    d = project.steno_dir
    if d is None or not d.is_dir():
        return
    lines = [f"{_INDEX_HEAD}{project.name}", "",
             "Newest first; paths from the project folder. Files: transcript.md always; "
             "summary.md / todo.md when made.", ""]
    for m in reversed(list_meetings(project)):
        if m.folder is None:
            continue
        files = ["transcript.md"] + [n for n in COPIED if (m.folder / n).is_file()]
        mark = " — **recording**" if m.session == recording else ""
        place = _place(m.folder)
        kind = f" ({place[1].name})" if place and place[1] else ""
        try:
            rel = m.folder.relative_to(project.path).as_posix()
        except ValueError:
            rel = str(m.folder)
        lines.append(f"- `{rel}/`{kind}{mark}: {', '.join(files)}")
    try:
        atomic_write_text(d / "index.md", "\n".join(lines) + "\n")
    except OSError:
        log.exception("Could not write %s", d / "index.md")
