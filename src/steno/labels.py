"""The mutable label layer on top of the append-only transcript.

The JSONL file never changes after a segment is written. Who a segment belongs to
can still change, through two small JSON files in the session directory (no
biometric data, plain text):

``speakers.json``  cluster/profile id -> display name
    {"v": 1,
     "clusters": {"S1": {"name": "Alice", "profile_id": "1a2b3c4d"}, "S2": {}},
     "merged":   {"S3": "S1", "S2": "P:1a2b3c4d"},  # clusters merged into another / a person
     "profiles": {"1a2b3c4d": "Alice"}}           # names of recognised/enrolled people

``labels.json``    refine overlay, seq -> speaker id (preferred over the live label)
    {"v": 1, "created": "...", "segments": {"1": {"speaker_id": "S1", "name": "Speaker 1",
                                                  "conf": 0.71}}}

Speaker ids: ``S<n>`` session cluster, ``P:<profile id>`` an enrolled person,
``U`` unknown. ``resolve_segments`` turns the ids into the current display name.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from steno.transcript import (
    MARKDOWN_NAME,
    Segment,
    atomic_write_text,
    format_line,
    read_best,
)

SPEAKERS_NAME = "speakers.json"
OVERLAY_NAME = "labels.json"
UNKNOWN_ID = "U"
UNKNOWN_NAME = "Unknown"
PROFILE_PREFIX = "P:"

_CLUSTER_RE = re.compile(r"^[SR](\d+)$")
_SPEC_RE = re.compile(r"^\s*(?:speaker\s*|s)?(\d+)\s*$", re.IGNORECASE)


def cluster_number(speaker_id: str) -> int | None:
    m = _CLUSTER_RE.match(speaker_id)
    return int(m.group(1)) if m else None


def parse_cluster_spec(spec: str) -> str | None:
    """``"S2"``, ``"s2"``, ``"Speaker 2"`` or ``"2"`` -> ``"S2"``; None if not a cluster."""
    m = _SPEC_RE.match(spec)
    return f"S{int(m.group(1))}" if m else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- speakers.json


class SessionLabels:
    """Read-modify-write access to ``speakers.json``.

    Every mutation re-reads the file first, so a ``steno name`` run while the engine
    is recording does not get overwritten by the engine's next update.
    """

    def __init__(self, session_dir: Path):
        self.path = Path(session_dir) / SPEAKERS_NAME

    # -- raw ---------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        data = _read_json(self.path)
        data.setdefault("v", 1)
        for key in ("clusters", "merged", "profiles"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def _update(self, mutate) -> None:
        data = self.load()
        mutate(data)
        _write_json(self.path, data)

    def init(self) -> None:
        if not self.path.exists():
            _write_json(self.path, self.load())

    # -- mutations ---------------------------------------------------------------

    def ensure_cluster(self, cluster_id: str) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["clusters"].setdefault(cluster_id, {})

        if cluster_id not in self.load()["clusters"]:
            self._update(mutate)

    def merge(self, dead: str, survivor: str) -> None:
        """Record that ``dead`` now means ``survivor``; a name carries over if needed."""

        def mutate(data: dict[str, Any]) -> None:
            clusters = data["clusters"]
            gone = clusters.pop(dead, {})
            if survivor in data["merged"]:  # survivor already means a profile (bound cluster)
                data["merged"][dead] = survivor
                return
            keep = clusters.setdefault(survivor, {})
            if gone.get("name") and not keep.get("name"):
                keep.update({k: v for k, v in gone.items() if k in ("name", "profile_id")})
            data["merged"][dead] = survivor

        self._update(mutate)

    def bind_profile(self, cluster_id: str, profile_id: str, name: str) -> bool:
        """Record that a live cluster is the enrolled person ``profile_id``.

        Earlier lines of the cluster then resolve to ``P:<profile_id>`` through the merge
        map (the JSONL is not rewritten). Returns False, changing nothing, when the
        cluster was already named for somebody else by hand (``steno name``).
        """
        target = PROFILE_PREFIX + profile_id
        done = True

        def mutate(data: dict[str, Any]) -> None:
            nonlocal done
            entry = data["clusters"].get(cluster_id) or {}
            if entry.get("name") and entry.get("profile_id") != profile_id:
                done = False
                return
            data["clusters"].pop(cluster_id, None)
            data["merged"][cluster_id] = target
            data["profiles"][profile_id] = name

        self._update(mutate)
        return done

    def set_cluster_name(self, cluster_id: str, name: str, profile_id: str | None) -> None:
        def mutate(data: dict[str, Any]) -> None:
            entry = data["clusters"].setdefault(cluster_id, {})
            entry["name"] = name
            if profile_id:
                entry["profile_id"] = profile_id
                data["profiles"][profile_id] = name

        self._update(mutate)

    def set_profile_name(self, profile_id: str, name: str) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["profiles"][profile_id] = name

        if self.load()["profiles"].get(profile_id) != name:
            self._update(mutate)

    def rename_profile(self, profile_id: str, name: str) -> bool:
        """Update a person's name everywhere in this session. False if they do not occur."""
        data = self.load()
        clusters = [c for c in data["clusters"].values() if c.get("profile_id") == profile_id]
        if profile_id not in data["profiles"] and not clusters:
            return False
        data["profiles"][profile_id] = name
        for cluster in clusters:
            cluster["name"] = name
        _write_json(self.path, data)
        return True

    def replace_clusters(self, cluster_ids: list[str], profiles: dict[str, str]) -> None:
        """Used by refine: new cluster namespace, old merge map dropped."""

        def mutate(data: dict[str, Any]) -> None:
            data["clusters"] = {cid: {} for cid in cluster_ids}
            data["merged"] = {}
            data["profiles"].update(profiles)

        self._update(mutate)

    # -- resolution --------------------------------------------------------------

    @staticmethod
    def root(data: dict[str, Any], speaker_id: str) -> str:
        merged = data.get("merged", {})
        seen = set()
        while speaker_id in merged and speaker_id not in seen:
            seen.add(speaker_id)
            speaker_id = merged[speaker_id]
        return speaker_id

    def resolve_root(self, speaker_id: str) -> str:
        return self.root(self.load(), speaker_id)

    @classmethod
    def display(cls, data: dict[str, Any], speaker_id: str, fallback: str = "") -> str:
        sid = cls.root(data, speaker_id)
        if sid == UNKNOWN_ID:
            return UNKNOWN_NAME
        if sid.startswith(PROFILE_PREFIX):
            return data.get("profiles", {}).get(sid[len(PROFILE_PREFIX):]) or fallback or sid
        entry = data.get("clusters", {}).get(sid) or {}
        if entry.get("name"):
            return str(entry["name"])
        n = cluster_number(sid)
        return f"Speaker {n}" if n is not None else (fallback or sid)

    def display_name(self, speaker_id: str, fallback: str = "") -> str:
        return self.display(self.load(), speaker_id, fallback)


# --------------------------------------------------------------------------- overlay


def read_overlay(session_dir: Path) -> dict[int, dict[str, Any]]:
    raw = _read_json(Path(session_dir) / OVERLAY_NAME).get("segments", {})
    out: dict[int, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                if isinstance(value, dict) and value.get("speaker_id"):
                    out[int(key)] = value
            except ValueError:
                continue
    return out


def write_overlay(session_dir: Path, entries: dict[int, dict[str, Any]], created: str) -> None:
    data = {
        "v": 1,
        "created": created,
        "segments": {str(seq): entries[seq] for seq in sorted(entries)},
    }
    _write_json(Path(session_dir) / OVERLAY_NAME, data)


# --------------------------------------------------------------------------- resolution


def effective_speaker_ids(session_dir: Path, segments: list[Segment]) -> dict[int, str]:
    """seq -> current speaker id (overlay preferred, merges followed). Skips id-less segments."""
    session_dir = Path(session_dir)
    overlay = read_overlay(session_dir)
    data = SessionLabels(session_dir).load()
    out: dict[int, str] = {}
    for seg in segments:
        entry = overlay.get(seg.seq)
        sid = entry["speaker_id"] if entry else seg.speaker_id
        if sid:
            out[seg.seq] = SessionLabels.root(data, sid)
    return out


def resolve_segments(session_dir: Path, segments: list[Segment]) -> list[Segment]:
    """Segments with ``speaker`` replaced by the current display name.

    Order of preference: refine overlay, then the live ``speaker_id``, then the label
    that was written with the segment (legacy files, speakers disabled).
    """
    session_dir = Path(session_dir)
    overlay = read_overlay(session_dir)
    data = SessionLabels(session_dir).load()
    out = []
    for seg in segments:
        entry = overlay.get(seg.seq)
        if entry:
            sid = SessionLabels.root(data, entry["speaker_id"])
            name = SessionLabels.display(data, sid, fallback=str(entry.get("name", "")))
            out.append(replace(seg, speaker=name, speaker_id=sid))
        elif seg.speaker_id:
            sid = SessionLabels.root(data, seg.speaker_id)
            out.append(
                replace(seg, speaker=SessionLabels.display(data, sid, fallback=seg.speaker),
                        speaker_id=sid)
            )
        else:
            out.append(seg)
    return out


def regenerate_markdown(session_dir: Path) -> bool:
    """Rewrite ``transcript.md`` with current names. False if it is locked (still recording)."""
    session_dir = Path(session_dir)
    segments = resolve_segments(session_dir, read_best(session_dir))
    text = "".join(format_line(s) + "\n\n" for s in segments)
    try:
        atomic_write_text(session_dir / MARKDOWN_NAME, text)
    except PermissionError:  # the live writer holds the file open on Windows
        (session_dir / (MARKDOWN_NAME + ".tmp")).unlink(missing_ok=True)
        return False
    return True
