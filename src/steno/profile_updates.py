"""Pending voice-profile updates of a session: candidates that wait for your confirmation.

With ``[profiles] update = "ask"`` (the default) a finished session does not change any profile:
``SpeakerTracker.close`` only stores which new voice samples it *would* add to the people it
recognised. If the recognition was wrong the wrong voice would otherwise be learnt, so the app
shows "Update voice profiles from this meeting?" and applies (or drops) them on request.

Two files in the session directory::

    profile-updates.bin    ENCRYPTED (steno.secure): per profile id the candidate embeddings
    profile-updates.json   plain summary without any embedding (see ``summary``)

The summary is only what the notice shows: profile id, name, number of samples, seconds of
speech recognised. Everything else (the embeddings are voiceprints) stays in the ``.bin``.
Reads never raise for a missing or damaged file; a damaged ``.bin`` is the same as nothing
pending. Deleting the session deletes both files with it.
"""

from __future__ import annotations

import io
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from steno.profiles import ProfileStore, _normalise_rows
from steno.secure import SecureStorageError, read_protected, write_protected
from steno.transcript import atomic_write_text

DATA_NAME = "profile-updates.bin"
SUMMARY_NAME = "profile-updates.json"
_lock = threading.Lock()


@dataclass(frozen=True)
class Pending:
    """The candidates of one person from one session."""

    profile_id: str
    name: str
    seconds: float  # speech the session recognised as this person
    embeddings: np.ndarray  # (n, dim) float32, L2-normalised rows

    @property
    def samples(self) -> int:
        return len(self.embeddings)


def _data_path(session_dir: Path) -> Path:
    return Path(session_dir) / DATA_NAME


def _summary_path(session_dir: Path) -> Path:
    return Path(session_dir) / SUMMARY_NAME


def write(session_dir: Path, items: list[Pending]) -> None:
    """Store ``items`` as the session's pending updates (replacing earlier ones); no items
    removes the files."""
    items = [i for i in items if i.samples]
    with _lock:
        _write(session_dir, items)


def _write(session_dir: Path, items: list[Pending]) -> None:
    if not items:
        _remove(session_dir)
        return
    arrays = {f"emb_{i.profile_id}": _normalise_rows(i.embeddings) for i in items}
    meta = {"v": 1, "profiles": {i.profile_id: {"name": i.name, "seconds": round(i.seconds, 1)}
                                 for i in items}}
    buf = io.BytesIO()
    np.savez(buf, meta=np.array(json.dumps(meta)), **arrays)
    write_protected(_data_path(session_dir), buf.getvalue())
    summary = {"v": 1, "profiles": [
        {"id": i.profile_id, "name": i.name, "samples": i.samples,
         "seconds": round(i.seconds, 1)} for i in items]}
    atomic_write_text(_summary_path(session_dir), json.dumps(summary, indent=2) + "\n")


def _remove(session_dir: Path) -> None:
    for path in (_data_path(session_dir), _summary_path(session_dir)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def clear(session_dir: Path) -> None:
    """Drop every pending update of the session."""
    with _lock:
        _remove(session_dir)


def load(session_dir: Path) -> list[Pending]:
    """The stored candidates ([] when none or unreadable)."""
    path = _data_path(session_dir)
    if not path.is_file():
        return []
    try:
        with np.load(io.BytesIO(read_protected(path)), allow_pickle=False) as npz:
            meta = json.loads(str(npz["meta"]))
            out = []
            for pid, info in meta["profiles"].items():
                emb = np.asarray(npz[f"emb_{pid}"], dtype=np.float32)
                if len(emb):
                    out.append(Pending(str(pid), str(info.get("name", "")),
                                       float(info.get("seconds", 0.0)), emb))
            return out
    except (SecureStorageError, OSError, KeyError, ValueError):
        return []


def summary(session_dir: Path) -> list[dict[str, Any]]:
    """The plain summary: ``[{id, name, samples, seconds}]`` ([] when nothing is pending).
    Never touches the encrypted file."""
    try:
        data = json.loads(_summary_path(session_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("profiles") if isinstance(data, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            out.append({"id": r["id"], "name": str(r.get("name", "")),
                        "samples": int(r.get("samples", 0)),
                        "seconds": float(r.get("seconds", 0.0))})
    return out


def prune(session_dir: Path, store: ProfileStore) -> list[dict[str, Any]]:
    """``summary`` after dropping people who no longer exist; current names are used (a person
    may have been renamed since the meeting)."""
    rows = summary(session_dir)
    if not rows:
        return []
    names = {p.id: p.name for p in store.list()}
    live = [r for r in rows if r["id"] in names]
    if len(live) != len(rows) or any(names[r["id"]] != r["name"] for r in live):
        with _lock:
            keep = [Pending(i.profile_id, names[i.profile_id], i.seconds, i.embeddings)
                    for i in load(session_dir) if i.profile_id in names]
            _write(session_dir, keep)
        rows = summary(session_dir)
    return rows


def resolve(
    session_dir: Path,
    store: ProfileStore,
    *,
    apply: list[str] | None = None,
    skip: list[str] | None = None,
) -> dict[str, int]:
    """Apply the pending samples of the profile ids in ``apply`` and discard those of ``skip``
    (and of people who no longer exist). Applied or skipped data is deleted; the rest stays
    pending. Returns ``{profile id: samples added}`` for the applied people."""
    apply, skip = set(apply or ()), set(skip or ())
    tag = f"auto:{Path(session_dir).name}"
    added: dict[str, int] = {}
    with _lock:
        items = load(session_dir)
        existing = {p.id for p in store.list()}
        keep: list[Pending] = []
        for item in items:
            pid = item.profile_id
            if pid not in existing:
                continue  # the person was deleted meanwhile
            if pid in apply:
                store.add_embeddings(pid, item.embeddings, tag)
                added[pid] = item.samples
            elif pid not in skip:
                keep.append(item)
        _write(session_dir, keep)
    return added
