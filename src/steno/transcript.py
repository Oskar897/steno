"""Session directories, the append-only transcript and the per-consumer cursor.

Layout and schema are specified in ``docs/formats.md``. The writer appends one
JSON line per final segment (flush + fsync). Readers (``read_segments``,
``fetch_new``) are safe to run while the writer is active: a trailing line
without a newline is considered partial and ignored.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from steno import __version__, paths

SCHEMA_VERSION = 1
TRANSCRIPT_NAME = "transcript.jsonl"
MARKDOWN_NAME = "transcript.md"
META_NAME = "session.json"
CURSORS_DIR = "cursors"
SESSION_ID_FORMAT = "%Y%m%d-%H%M%S"

_FIELD_ORDER = ("v", "seq", "t_start", "t_end", "wall", "source", "speaker", "text", "lang")
#: Written only when speaker recognition produced them (layer 2).
_OPTIONAL_FIELDS = ("speaker_id", "speaker_conf", "provisional")


@dataclass(frozen=True)
class Segment:
    seq: int
    t_start: float
    t_end: float
    wall: str
    source: str
    speaker: str
    text: str
    lang: str
    v: int = SCHEMA_VERSION
    speaker_id: str | None = None  # "S1" (session cluster), "P:<profile id>", "U" (unknown)
    speaker_conf: float | None = None
    provisional: bool | None = None

    def to_json(self) -> str:
        data = asdict(self)
        ordered = {k: data[k] for k in _FIELD_ORDER}
        for key in _OPTIONAL_FIELDS:
            if data[key] is not None:
                ordered[key] = data[key]
        return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        return cls(
            v=int(data.get("v", SCHEMA_VERSION)),
            seq=int(data["seq"]),
            t_start=float(data["t_start"]),
            t_end=float(data["t_end"]),
            wall=str(data["wall"]),
            source=str(data["source"]),
            speaker=str(data["speaker"]),
            text=str(data["text"]),
            lang=str(data["lang"]),
            speaker_id=None if data.get("speaker_id") is None else str(data["speaker_id"]),
            speaker_conf=(
                None if data.get("speaker_conf") is None else float(data["speaker_conf"])
            ),
            provisional=None if data.get("provisional") is None else bool(data["provisional"]),
        )


def format_clock(seconds: float) -> str:
    """``mm:ss``, or ``h:mm:ss`` once past an hour."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_line(seg: Segment) -> str:
    """Markdown line shared by the mirror file and `steno new` output."""
    return f"**[{format_clock(seg.t_start)}] {seg.speaker}:** {seg.text}"


def render_markdown(segments: Iterable[Segment]) -> str:
    return "\n".join(format_line(s) for s in segments)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------- writer


class SessionWriter:
    """Creates a session directory and appends segments. Thread-safe."""

    def __init__(self, session_dir: Path, started_at: datetime, meta: dict[str, Any]):
        self.dir = session_dir
        self.started_at = started_at
        self._meta = meta
        self._lock = threading.Lock()
        self._seq = 0
        self._closed = False
        self._jsonl = (session_dir / TRANSCRIPT_NAME).open("ab")
        self._md = (session_dir / MARKDOWN_NAME).open("ab")

    @classmethod
    def create(
        cls,
        *,
        root: Path | None = None,
        model: str = "",
        sources: Iterable[str] = (),
        extra: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SessionWriter:
        root = root if root is not None else paths.sessions_dir()
        root.mkdir(parents=True, exist_ok=True)
        started = (now or datetime.now()).replace(microsecond=0)
        while True:  # one-second ids: bump on collision
            session_id = started.strftime(SESSION_ID_FORMAT)
            try:
                (root / session_id).mkdir()
                break
            except FileExistsError:
                started += timedelta(seconds=1)
        session_dir = root / session_id
        (session_dir / CURSORS_DIR).mkdir()
        meta: dict[str, Any] = {
            "id": session_id,
            "started_at": started.isoformat(timespec="seconds"),
            "ended_at": None,
            "model": model,
            "sources": list(sources),
            "steno_version": __version__,
            **(extra or {}),
        }
        atomic_write_text(session_dir / META_NAME, json.dumps(meta, indent=2) + "\n")
        return cls(session_dir, started, meta)

    @property
    def session_id(self) -> str:
        return self.dir.name

    @property
    def count(self) -> int:
        return self._seq

    def append(
        self,
        *,
        t_start: float,
        t_end: float,
        source: str,
        speaker: str,
        text: str,
        lang: str,
        speaker_id: str | None = None,
        speaker_conf: float | None = None,
        provisional: bool | None = None,
    ) -> Segment:
        with self._lock:
            if self._closed:
                raise ValueError("session is closed")
            self._seq += 1
            wall = (self.started_at + timedelta(seconds=t_start)).isoformat(timespec="seconds")
            seg = Segment(
                seq=self._seq,
                t_start=round(t_start, 2),
                t_end=round(t_end, 2),
                wall=wall,
                source=source,
                speaker=speaker,
                text=text,
                lang=lang,
                speaker_id=speaker_id,
                speaker_conf=None if speaker_conf is None else round(speaker_conf, 3),
                provisional=provisional,
            )
            self._jsonl.write((seg.to_json() + "\n").encode("utf-8"))
            self._jsonl.flush()
            os.fsync(self._jsonl.fileno())
            self._md.write((format_line(seg) + "\n\n").encode("utf-8"))
            self._md.flush()
            return seg

    def close(self) -> None:
        """Write ``ended_at`` and close files. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._jsonl.close()
            self._md.close()
            self._meta["ended_at"] = datetime.now().isoformat(timespec="seconds")
            self._meta["segments"] = self._seq
            atomic_write_text(self.dir / META_NAME, json.dumps(self._meta, indent=2) + "\n")

    def __enter__(self) -> SessionWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- reading


def read_segments(session_dir: Path, after_seq: int = 0) -> list[Segment]:
    """Segments with ``seq > after_seq``. Ignores a trailing partial line and corrupt lines."""
    path = Path(session_dir) / TRANSCRIPT_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    complete, _, _partial = raw.rpartition(b"\n")  # drop anything after the last newline
    out: list[Segment] = []
    for line in complete.split(b"\n"):
        if not line.strip():
            continue
        try:
            seg = Segment.from_dict(json.loads(line.decode("utf-8")))
        except (ValueError, KeyError, TypeError):
            continue
        if seg.seq > after_seq:
            out.append(seg)
    return out


def _cursor_path(session_dir: Path, consumer: str) -> Path:
    bad = ("/", "\\", ":")
    if not consumer or consumer.startswith(".") or any(c in consumer for c in bad):
        raise ValueError(f"invalid consumer name: {consumer!r}")
    return Path(session_dir) / CURSORS_DIR / f"{consumer}.json"


def read_cursor(session_dir: Path, consumer: str) -> int:
    path = _cursor_path(session_dir, consumer)  # raises on invalid names
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(data["last_seq"]))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return 0


def write_cursor(session_dir: Path, consumer: str, last_seq: int) -> None:
    path = _cursor_path(session_dir, consumer)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"last_seq": int(last_seq)}) + "\n")


def fetch_new(
    session_dir: Path,
    consumer: str,
    *,
    peek: bool = False,
    resolve: Callable[[Path, list[Segment]], list[Segment]] | None = None,
) -> list[Segment]:
    """Segments the consumer has not seen yet; advances its cursor unless ``peek``.

    ``resolve`` may rewrite the segments for display (see ``steno.labels``), e.g. to
    apply speaker names that were assigned after the segment was written.
    """
    last = read_cursor(session_dir, consumer)
    segments = read_segments(session_dir, after_seq=last)
    if segments and not peek:
        write_cursor(session_dir, consumer, segments[-1].seq)
    return resolve(Path(session_dir), segments) if resolve else segments


# --------------------------------------------------------------------------- sessions


@dataclass(frozen=True)
class SessionInfo:
    id: str
    dir: Path
    started_at: str
    ended_at: str | None
    model: str
    sources: list[str]
    segments: int

    @property
    def recording(self) -> bool:
        return self.ended_at is None


def _count_lines(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except FileNotFoundError:
        return 0


def load_session_info(session_dir: Path) -> SessionInfo:
    try:
        meta = json.loads((session_dir / META_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        meta = {}
    return SessionInfo(
        id=session_dir.name,
        dir=session_dir,
        started_at=str(meta.get("started_at", "")),
        ended_at=meta.get("ended_at"),
        model=str(meta.get("model", "")),
        sources=list(meta.get("sources", [])),
        segments=_count_lines(session_dir / TRANSCRIPT_NAME),
    )


def list_sessions(root: Path | None = None) -> list[SessionInfo]:
    """All sessions, oldest first (ids sort chronologically)."""
    root = root if root is not None else paths.sessions_dir()
    dirs = sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
    return [load_session_info(d) for d in dirs]


def resolve_session(spec: str = "latest", root: Path | None = None) -> Path | None:
    """Directory for a session id or ``latest``; None when it does not exist."""
    root = root if root is not None else paths.sessions_dir()
    if spec == "latest":
        sessions = list_sessions(root)
        return sessions[-1].dir if sessions else None
    if "/" in spec or "\\" in spec or spec.startswith("."):
        return None
    candidate = root / spec
    return candidate if candidate.is_dir() else None
