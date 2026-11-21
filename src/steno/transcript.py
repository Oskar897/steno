"""Session directories, the append-only transcript and the per-consumer cursor.

Layout and schema are specified in ``docs/formats.md``. The writer appends one
JSON line per final segment (flush + fsync). Readers (``read_segments``,
``fetch_new``) are safe to run while the writer is active: a trailing line
without a newline is considered partial and ignored.

``revisions.jsonl`` (``Revision``, written by ``SessionWriter.append_revisions``) holds better
text for lines of the live transcript, appended by the engine's refine worker while recording;
``read_best`` applies the newest revision of every line and ``fetch_new_best`` implements the
"only what is new" rule for consumers with revisions (see ``docs/formats.md``).
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from steno import __version__, paths

SCHEMA_VERSION = 1
TRANSCRIPT_NAME = "transcript.jsonl"
MARKDOWN_NAME = "transcript.md"
REVISIONS_NAME = "revisions.jsonl"
META_NAME = "session.json"
CURSORS_DIR = "cursors"
SESSION_ID_FORMAT = "%Y%m%d-%H%M%S"

_FIELD_ORDER = ("v", "seq", "t_start", "t_end", "wall", "source", "speaker", "text", "lang")
#: Written only when speaker recognition produced them (layer 2).
_OPTIONAL_FIELDS = (
    "speaker_id", "speaker_conf", "provisional", "avg_logprob", "words", "final",
)


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
    avg_logprob: float | None = None  # ASR confidence of the utterance the line came from
    words: list[list] | None = None  # [[start, end, word], ...] in session seconds
    final: bool | None = None  # True in final.jsonl (written by `steno finalize`)
    #: Number of the revision whose text this segment carries (``read_best``); never written.
    rev: int | None = None

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
            avg_logprob=(
                None if data.get("avg_logprob") is None else float(data["avg_logprob"])
            ),
            words=_parse_words(data.get("words")),
            final=None if data.get("final") is None else bool(data["final"]),
        )


def _parse_words(raw: Any) -> list[list] | None:
    """``[[start, end, word], ...]`` from JSON; None when absent or malformed."""
    if not isinstance(raw, list):
        return None
    out: list[list] = []
    for item in raw:
        if not (isinstance(item, list | tuple) and len(item) == 3):
            return None
        out.append([float(item[0]), float(item[1]), str(item[2])])
    return out


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


# --------------------------------------------------------------------------- revisions


@dataclass(frozen=True)
class Revision:
    """A better text for an already written line (``revisions.jsonl``, see docs/formats.md).

    ``transcript.jsonl`` is append-only and never changes; the refine worker of the live
    engine re-transcribes stretches of speech with more context and appends one revision
    per line it improved. The line keeps its ``seq``, speaker and times.
    """

    rev: int  # 1-based, strictly increasing within a session
    seq: int  # the line this text replaces
    text: str
    wall: str = ""  # ISO 8601 local time the revision was written
    lang: str | None = None
    words: list[list] | None = None  # [[start, end, word], ...] in session seconds
    avg_logprob: float | None = None
    model: str | None = None
    v: int = SCHEMA_VERSION

    def to_json(self) -> str:
        data = asdict(self)
        ordered = {k: data[k] for k in ("v", "rev", "seq", "wall", "text")}
        for key in ("lang", "words", "avg_logprob", "model"):
            if data[key] is not None:
                ordered[key] = data[key]
        return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Revision:
        return cls(
            v=int(data.get("v", SCHEMA_VERSION)),
            rev=int(data["rev"]),
            seq=int(data["seq"]),
            text=str(data["text"]),
            wall=str(data.get("wall", "")),
            lang=None if data.get("lang") is None else str(data["lang"]),
            words=_parse_words(data.get("words")),
            avg_logprob=(
                None if data.get("avg_logprob") is None else float(data["avg_logprob"])
            ),
            model=None if data.get("model") is None else str(data["model"]),
        )


def read_revisions(session_dir: Path) -> list[Revision]:
    """All revisions in file order. Ignores a trailing partial line and corrupt lines."""
    try:
        raw = (Path(session_dir) / REVISIONS_NAME).read_bytes()
    except FileNotFoundError:
        return []
    complete, _, _partial = raw.rpartition(b"\n")
    out: list[Revision] = []
    for line in complete.split(b"\n"):
        if not line.strip():
            continue
        try:
            out.append(Revision.from_dict(json.loads(line.decode("utf-8"))))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def latest_revisions(
    revisions: Iterable[Revision], max_rev: int | None = None
) -> dict[int, Revision]:
    """seq -> its newest revision (highest ``rev``); only ``rev <= max_rev`` when given."""
    best: dict[int, Revision] = {}
    for r in revisions:
        if max_rev is not None and r.rev > max_rev:
            continue
        if r.seq not in best or r.rev >= best[r.seq].rev:
            best[r.seq] = r
    return best


def apply_revision(seg: Segment, rev: Revision | None) -> Segment:
    """``seg`` with the text, words and confidence of ``rev`` (unchanged without one)."""
    if rev is None:
        return seg
    return replace(
        seg,
        text=rev.text,
        words=rev.words if rev.words is not None else seg.words,
        avg_logprob=rev.avg_logprob if rev.avg_logprob is not None else seg.avg_logprob,
        rev=rev.rev,
    )


def read_best(
    session_dir: Path, after_seq: int = 0, name: str = TRANSCRIPT_NAME
) -> list[Segment]:
    """The live segments with the newest revision of every line applied (``rev`` set on them).

    Same lines, ``seq`` values, speakers and times as ``read_segments``; only text, words
    and confidence differ. Safe while the engine is running. ``name`` other than the live
    transcript (``final.jsonl``) has no revisions and is returned as is.
    """
    segments = read_segments(session_dir, after_seq, name)
    if name != TRANSCRIPT_NAME:
        return segments
    best = latest_revisions(read_revisions(session_dir))
    return [apply_revision(s, best.get(s.seq)) for s in segments]


_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


def same_words(a: str, b: str) -> bool:
    """True when two texts differ only in case, punctuation and spacing."""
    return _WORD_RE.findall(a.casefold()) == _WORD_RE.findall(b.casefold())


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
        self._revisions = None  # opened by the first revision
        self._rev = 0
        self._segments: dict[int, Segment] = {}  # what the Markdown mirror currently shows

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
        avg_logprob: float | None = None,
        words: list[list] | None = None,
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
                avg_logprob=None if avg_logprob is None else round(avg_logprob, 3),
                words=(
                    None if not words
                    else [[round(a, 2), round(b, 2), str(w)] for a, b, w in words]
                ),
            )
            self._jsonl.write((seg.to_json() + "\n").encode("utf-8"))
            self._jsonl.flush()
            os.fsync(self._jsonl.fileno())
            self._md.write((format_line(seg) + "\n\n").encode("utf-8"))
            self._md.flush()
            self._segments[seg.seq] = seg
            return seg

    def append_revisions(
        self,
        items: Iterable[tuple[int, str, dict[str, Any]]],
        *,
        model: str | None = None,
    ) -> list[Segment]:
        """Append revisions ``(seq, text, extra)`` to ``revisions.jsonl``; returns revised lines.

        ``extra`` may carry ``lang``, ``words`` (session seconds) and ``avg_logprob``. Items for
        an unknown ``seq`` or with empty text are skipped. The Markdown mirror is rewritten
        once afterwards so that it shows the best text (``transcript.jsonl`` is not touched).
        """
        out: list[Segment] = []
        with self._lock:
            if self._closed:
                raise ValueError("session is closed")
            for seq, text, extra in items:
                seg = self._segments.get(seq)
                if seg is None or not text.strip():
                    continue
                self._rev += 1
                words = extra.get("words")
                lp = extra.get("avg_logprob")
                rev = Revision(
                    rev=self._rev,
                    seq=seq,
                    text=text,
                    wall=datetime.now().isoformat(timespec="seconds"),
                    lang=extra.get("lang"),
                    words=(
                        None if not words
                        else [[round(a, 2), round(b, 2), str(w)] for a, b, w in words]
                    ),
                    avg_logprob=None if lp is None else round(lp, 3),
                    model=model,
                )
                if self._revisions is None:
                    self._revisions = (self.dir / REVISIONS_NAME).open("ab")
                self._revisions.write((rev.to_json() + "\n").encode("utf-8"))
                revised = apply_revision(seg, rev)
                self._segments[seq] = replace(revised, rev=None)  # the mirror only needs text
                out.append(revised)
            if out:
                self._revisions.flush()
                os.fsync(self._revisions.fileno())
                self._rewrite_markdown()
        return out

    def _rewrite_markdown(self) -> None:
        """Replace ``transcript.md`` with the current best text (caller holds the lock).

        Windows cannot replace a file that is open, so the append handle is closed for the
        moment. A reader that holds the file open makes the swap fail; the next revision
        (or the end of the session) tries again.
        """
        text = "".join(format_line(self._segments[q]) + "\n\n" for q in sorted(self._segments))
        self._md.close()
        try:
            atomic_write_text(self.dir / MARKDOWN_NAME, text)
        except OSError:
            (self.dir / (MARKDOWN_NAME + ".tmp")).unlink(missing_ok=True)
        finally:
            self._md = (self.dir / MARKDOWN_NAME).open("ab")

    def set_meta(self, key: str, value: Any) -> None:
        """Add a key to session.json (written when the session closes)."""
        with self._lock:
            self._meta[key] = value

    def close(self) -> None:
        """Write ``ended_at`` and close files. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._jsonl.close()
            self._md.close()
            if self._revisions is not None:
                self._revisions.close()
            self._meta["ended_at"] = datetime.now().isoformat(timespec="seconds")
            self._meta["segments"] = self._seq
            atomic_write_text(self.dir / META_NAME, json.dumps(self._meta, indent=2) + "\n")

    def __enter__(self) -> SessionWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- reading


def read_segments(
    session_dir: Path, after_seq: int = 0, name: str = TRANSCRIPT_NAME
) -> list[Segment]:
    """Segments with ``seq > after_seq``. Ignores a trailing partial line and corrupt lines.

    ``name`` selects another file of the session with the same format (``final.jsonl``).
    """
    path = Path(session_dir) / name
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


def read_cursor_state(session_dir: Path, consumer: str) -> tuple[int, int]:
    """``(last_seq, last_rev)`` of a consumer: the last line and the last revision it has seen."""
    path = _cursor_path(session_dir, consumer)  # raises on invalid names
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        last_seq = max(0, int(data["last_seq"]))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return 0, 0
    try:
        return last_seq, max(0, int(data.get("last_rev", 0)))
    except (ValueError, TypeError):
        return last_seq, 0


def read_cursor(session_dir: Path, consumer: str) -> int:
    return read_cursor_state(session_dir, consumer)[0]


def write_cursor(
    session_dir: Path, consumer: str, last_seq: int, last_rev: int | None = None
) -> None:
    """Store the cursor; ``last_rev`` None keeps the revision position that is already stored."""
    path = _cursor_path(session_dir, consumer)
    path.parent.mkdir(parents=True, exist_ok=True)
    if last_rev is None:
        last_rev = read_cursor_state(session_dir, consumer)[1]
    data: dict[str, int] = {"last_seq": int(last_seq)}
    if last_rev:
        data["last_rev"] = int(last_rev)
    atomic_write_text(path, json.dumps(data) + "\n")


def fetch_new(
    session_dir: Path,
    consumer: str,
    *,
    peek: bool = False,
    resolve: Callable[[Path, list[Segment]], list[Segment]] | None = None,
) -> list[Segment]:
    """Segments the consumer has not seen yet; advances its cursor unless ``peek``.

    The draft lines exactly as written to ``transcript.jsonl`` (no revisions, and the
    revision position of the cursor is left alone); ``fetch_new_best`` is the version that
    delivers refined text. ``resolve`` may rewrite the segments for display (see
    ``steno.labels``), e.g. to apply speaker names that were assigned after the segment
    was written.
    """
    last = read_cursor(session_dir, consumer)
    segments = read_segments(session_dir, after_seq=last)
    if segments and not peek:
        write_cursor(session_dir, consumer, segments[-1].seq)
    return resolve(Path(session_dir), segments) if resolve else segments


@dataclass(frozen=True)
class NewText:
    """What ``fetch_new_best`` delivers: new lines and corrections of lines delivered before."""

    new: list[Segment]  # lines after the cursor, with the best text known right now
    corrections: list[Segment]  # earlier lines whose text has since been improved

    def __bool__(self) -> bool:
        return bool(self.new or self.corrections)


def compute_new(
    session_dir: Path,
    last_seq: int,
    last_rev: int,
    *,
    resolve: Callable[[Path, list[Segment]], list[Segment]] | None = None,
) -> tuple[NewText, int, int]:
    """What is new after the position ``(last_seq, last_rev)``, without touching any cursor.

    Returns the text plus the position after it, ``(new_text, next_seq, next_rev)``: the
    newest line and the newest revision that were read. ``fetch_new_best`` (stored cursor) and
    the stateless token mode of ``steno new --since`` are both built on this.
    """
    session_dir = Path(session_dir)
    revisions = read_revisions(session_dir)
    drafts = read_segments(session_dir)
    newest = latest_revisions(revisions)
    seen = latest_revisions(revisions, max_rev=last_rev)  # what the reader was shown
    new = [apply_revision(s, newest.get(s.seq)) for s in drafts if s.seq > last_seq]
    corrections = []
    for s in drafts:
        if s.seq > last_seq:
            continue
        cur = newest.get(s.seq)
        if cur is None or cur.rev <= last_rev:
            continue
        shown = seen[s.seq].text if s.seq in seen else s.text
        if not same_words(shown, cur.text):
            corrections.append(apply_revision(s, cur))
    top_rev = max((r.rev for r in revisions), default=0)
    next_seq = max(last_seq, new[-1].seq if new else 0)
    next_rev = max(last_rev, top_rev)
    if resolve:
        new, corrections = resolve(session_dir, new), resolve(session_dir, corrections)
    return NewText(new, corrections), next_seq, next_rev


def fetch_new_best(
    session_dir: Path,
    consumer: str,
    *,
    peek: bool = False,
    resolve: Callable[[Path, list[Segment]], list[Segment]] | None = None,
) -> NewText:
    """Like ``fetch_new`` but refined: the rule for "only what is new" with revisions.

    * Lines after ``last_seq`` are delivered once, with the newest revision already applied
      (a line the refine worker has reached is never delivered as a draft *and* again).
    * A line the consumer got before (``seq <= last_seq``) is delivered again, as a
      *correction*, only when a revision written after the consumer's last read
      (``rev > last_rev``) changes its text beyond case and punctuation. A revision that
      says the same thing is skipped silently.
    * The cursor then moves to the newest line and the newest revision read, so nothing is
      delivered twice. Peeking changes nothing.
    """
    session_dir = Path(session_dir)
    last_seq, last_rev = read_cursor_state(session_dir, consumer)
    text, next_seq, next_rev = compute_new(session_dir, last_seq, last_rev, resolve=resolve)
    if not peek and (next_seq, next_rev) != (last_seq, last_rev):
        write_cursor(session_dir, consumer, next_seq, next_rev)
    return text


# --------------------------------------------------------------------------- tokens

_TOKEN_SESSION = re.compile(r"[A-Za-z0-9_-]{1,64}")


class TokenError(ValueError):
    """A ``--since`` token that is not ``<session>:<seq>:<rev>`` (the message says how)."""


def format_token(session_id: str, last_seq: int, last_rev: int) -> str:
    """Position in a session as a compact, stateless cursor: ``20261121-195539:42:7``."""
    return f"{session_id}:{int(last_seq)}:{int(last_rev)}"


def parse_token(token: str) -> tuple[str, int, int]:
    """``(session_id, last_seq, last_rev)`` of a token from ``format_token``."""
    parts = token.strip().split(":")
    if (len(parts) != 3 or not _TOKEN_SESSION.fullmatch(parts[0])
            or not all(p.isascii() and p.isdigit() for p in parts[1:])):
        raise TokenError(
            f"not a valid token: {token!r}. Use 'start' or the value after 'next:' in the "
            "previous output (SESSION:SEQ:REV, e.g. 20261121-195539:42:7).")
    return parts[0], int(parts[1]), int(parts[2])


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
