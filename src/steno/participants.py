"""Who is in a meeting: the session's participant list (``participants.json``).

A small plain-text file in the session directory (no biometric data, only voice profile ids)::

    {"v": 1, "profiles": ["1a2b3c4d", "5e6f7a8b"], "count": 4}

``profiles`` are the enrolled people expected in the meeting; ``count`` is the expected number
of speakers (optional, may be larger than the list: guests). An empty or missing list means
"anybody": live recognition and finalize then match against every voice profile, exactly as
without the file. A non-empty list narrows matching to those people, which makes a small
meeting much less error-prone. The file is read while recording (every few seconds), so it
can change during the meeting.

Reads never raise (a damaged file is the same as no file); writes are atomic and serialised
inside this process, so the UI thread and the engine thread cannot tear it.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steno.transcript import atomic_write_text

NAME = "participants.json"
MAX_COUNT = 20
MAX_PROFILES = 50
_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_lock = threading.Lock()


@dataclass(frozen=True)
class Participants:
    profiles: tuple[str, ...] = ()
    count: int | None = None

    @property
    def restricts(self) -> bool:
        """True when matching is narrowed to ``profiles`` (a non-empty list)."""
        return bool(self.profiles)

    def to_json(self) -> dict[str, Any]:
        return {"profiles": list(self.profiles), "count": self.count}


def _clean(profiles: Iterable[Any], count: Any) -> Participants:
    seen: dict[str, None] = {}
    for p in profiles:
        if isinstance(p, str) and _ID_RE.match(p):
            seen.setdefault(p)
    n = None
    if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= MAX_COUNT:
        n = count
    return Participants(tuple(seen)[:MAX_PROFILES], n)


def path_of(session_dir: Path) -> Path:
    return Path(session_dir) / NAME


def read(session_dir: Path) -> Participants:
    """The session's participants; empty (everybody) when the file is missing or damaged."""
    try:
        data = json.loads(path_of(session_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Participants()
    if not isinstance(data, dict):
        return Participants()
    profiles = data.get("profiles")
    return _clean(profiles if isinstance(profiles, list) else [], data.get("count"))


def exists(session_dir: Path) -> bool:
    return path_of(session_dir).is_file()


def write(session_dir: Path, participants: Participants) -> Participants:
    """Store ``participants`` (cleaned). Returns what was written."""
    clean = _clean(participants.profiles, participants.count)
    data = {"v": 1, **clean.to_json()}
    atomic_write_text(path_of(session_dir), json.dumps(data, indent=2) + "\n")
    return clean


def signature(session_dir: Path) -> tuple[int, int] | None:
    """Cheap change detector (mtime, size); None when there is no file."""
    try:
        st = path_of(session_dir).stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def update(
    session_dir: Path,
    *,
    profiles: Iterable[str] | None = None,
    add: str | None = None,
    remove: str | None = None,
    count: int | None = None,
    clear_count: bool = False,
) -> Participants:
    """Read-modify-write: replace the list (``profiles``), add or remove one profile id, set (or
    clear) the expected count. Anything not mentioned stays as it was."""
    with _lock:
        cur = read(session_dir)
        ids = list(cur.profiles if profiles is None else profiles)
        if add is not None and add not in ids:
            ids.append(add)
        if remove is not None:
            ids = [p for p in ids if p != remove]
        n = None if clear_count else (count if count is not None else cur.count)
        return write(session_dir, Participants(tuple(ids), n))


def restrict(profiles: list, participants: Participants) -> list:
    """``profiles`` (objects with an ``id``) narrowed to the participants; all when no list."""
    if not participants.restricts:
        return list(profiles)
    keep = set(participants.profiles)
    return [p for p in profiles if p.id in keep]
