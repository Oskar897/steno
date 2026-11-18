"""Encrypted storage of biometric data: voice profiles and per-session embeddings.

Everything here is written through ``steno.secure`` (Windows DPAPI, current user).

    <data_dir>/profiles/<id>.bin              one person: embeddings + name + timestamps
    <data_dir>/sessions/<id>/embeddings.bin   seq -> embedding of every embedded segment
    <data_dir>/sessions/<id>/windows.bin      embedding of every 1.5 s window (for refine)

Both files are an ``npz`` archive (no pickles) holding float32 embeddings plus a
small JSON metadata string. A profile keeps up to ``MAX_EMBEDDINGS`` embeddings per
person; when more are added, the most redundant ones are dropped so the profile
stays diverse instead of favouring the oldest or newest recording.
"""

from __future__ import annotations

import io
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from steno import paths
from steno.secure import SecureStorageError, read_protected, write_protected

MAX_EMBEDDINGS = 30
EMBEDDINGS_NAME = "embeddings.bin"
WINDOWS_NAME = "windows.bin"
_ID_RE = re.compile(r"^[0-9a-f]{8}$")


class ProfileError(ValueError):
    """Unknown person, duplicate name, or other user-facing profile problem."""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalise_rows(emb: np.ndarray) -> np.ndarray:
    emb = np.atleast_2d(np.asarray(emb, dtype=np.float32))
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    return emb / np.maximum(norms, 1e-12)


def prune_redundant(emb: np.ndarray, tags: list[str], limit: int) -> tuple[np.ndarray, list[str]]:
    """Drop embeddings until ``limit`` remain, always removing one of the closest pair."""
    emb = _normalise_rows(emb)
    keep = list(range(len(emb)))
    while len(keep) > limit:
        sub = emb[keep]
        sim = sub @ sub.T
        np.fill_diagonal(sim, -np.inf)
        i, j = np.unravel_index(int(np.argmax(sim)), sim.shape)
        # Of the closest pair, drop the one that is more similar to the rest of the set.
        mean_i = float(np.mean(sim[i][np.isfinite(sim[i])]))
        mean_j = float(np.mean(sim[j][np.isfinite(sim[j])]))
        drop = i if mean_i >= mean_j else j
        keep.pop(int(drop))
    return emb[keep], [tags[k] for k in keep]


# --------------------------------------------------------------------------- profiles


@dataclass
class Profile:
    id: str
    name: str
    embeddings: np.ndarray  # (n, dim) float32, L2-normalised rows
    tags: list[str] = field(default_factory=list)  # origin of each embedding (session id etc.)
    created: str = ""
    updated: str = ""

    def centroid(self) -> np.ndarray:
        c = self.embeddings.mean(axis=0)
        return c / max(float(np.linalg.norm(c)), 1e-12)

    def to_bytes(self) -> bytes:
        meta = {
            "v": 1,
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "updated": self.updated,
            "tags": self.tags,
        }
        buf = io.BytesIO()
        np.savez(
            buf, embeddings=self.embeddings.astype(np.float32), meta=np.array(json.dumps(meta))
        )
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, raw: bytes) -> Profile:
        with np.load(io.BytesIO(raw), allow_pickle=False) as npz:
            meta = json.loads(str(npz["meta"]))
            emb = np.asarray(npz["embeddings"], dtype=np.float32)
        tags = list(meta.get("tags", []))
        tags += [""] * (len(emb) - len(tags))
        return cls(
            id=str(meta["id"]),
            name=str(meta["name"]),
            embeddings=emb,
            tags=tags[: len(emb)],
            created=str(meta.get("created", "")),
            updated=str(meta.get("updated", "")),
        )


class ProfileStore:
    """Profiles on disk. Names are unique (case-insensitive)."""

    def __init__(self, root: Path | None = None):
        self._root = Path(root) if root is not None else None
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        root = self._root if self._root is not None else paths.profiles_dir()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _path(self, profile_id: str) -> Path:
        if not _ID_RE.match(profile_id):
            raise ProfileError(f"invalid profile id: {profile_id!r}")
        return self.root / f"{profile_id}.bin"

    def signature(self) -> tuple[tuple[str, int], ...]:
        """Cheap change detector (file names + mtimes) for live reloads."""
        out = []
        for f in sorted(self.root.glob("*.bin")):
            try:
                out.append((f.name, f.stat().st_mtime_ns))
            except OSError:
                continue
        return tuple(out)

    def list(self) -> list[Profile]:
        profiles = []
        for f in sorted(self.root.glob("*.bin")):
            try:
                profiles.append(Profile.from_bytes(read_protected(f)))
            except SecureStorageError:
                raise
            except Exception as exc:
                raise ProfileError(f"unreadable profile file {f.name}: {exc}") from exc
        return sorted(profiles, key=lambda p: p.name.lower())

    def get(self, profile_id: str) -> Profile | None:
        path = self._path(profile_id)
        if not path.is_file():
            return None
        return Profile.from_bytes(read_protected(path))

    def find_by_name(self, name: str) -> Profile | None:
        key = name.strip().casefold()
        return next((p for p in self.list() if p.name.casefold() == key), None)

    def _save(self, profile: Profile) -> None:
        write_protected(self._path(profile.id), profile.to_bytes())

    def create(self, name: str, embeddings: np.ndarray, tag: str = "") -> Profile:
        name = name.strip()
        if not name:
            raise ProfileError("name must not be empty")
        with self._lock:
            if self.find_by_name(name) is not None:
                raise ProfileError(f"a person named {name!r} already exists")
            emb = _normalise_rows(embeddings)
            tags = [tag] * len(emb)
            emb, tags = prune_redundant(emb, tags, MAX_EMBEDDINGS)
            stamp = now_iso()
            while True:
                pid = secrets.token_hex(4)
                if not self._path(pid).exists():
                    break
            profile = Profile(pid, name, emb, tags, stamp, stamp)
            self._save(profile)
            return profile

    def add_embeddings(self, profile_id: str, embeddings: np.ndarray, tag: str = "") -> Profile:
        with self._lock:
            profile = self.get(profile_id)
            if profile is None:
                raise ProfileError(f"no such profile: {profile_id}")
            new = _normalise_rows(embeddings)
            emb = np.vstack([profile.embeddings, new])
            tags = profile.tags + [tag] * len(new)
            profile.embeddings, profile.tags = prune_redundant(emb, tags, MAX_EMBEDDINGS)
            profile.updated = now_iso()
            self._save(profile)
            return profile

    def rename(self, old: str, new: str) -> Profile:
        new = new.strip()
        if not new:
            raise ProfileError("name must not be empty")
        with self._lock:
            profile = self.find_by_name(old)
            if profile is None:
                raise ProfileError(f"no such person: {old!r}")
            clash = self.find_by_name(new)
            if clash is not None and clash.id != profile.id:
                raise ProfileError(f"a person named {new!r} already exists")
            profile.name = new
            profile.updated = now_iso()
            self._save(profile)
            return profile

    def delete(self, name: str) -> Profile:
        with self._lock:
            profile = self.find_by_name(name)
            if profile is None:
                raise ProfileError(f"no such person: {name!r}")
            self._path(profile.id).unlink()
            return profile


# --------------------------------------------------------------------------- session embeddings


class SessionEmbeddings:
    """seq -> embedding for one session, encrypted, flushed in batches.

    ``add`` buffers in memory and rewrites the whole file every ``flush_every`` items
    and on ``close``; a crash can therefore lose the last few embeddings (the
    transcript itself is unaffected, it is fsynced per segment).
    """

    def __init__(self, session_dir: Path, flush_every: int = 20):
        self.path = Path(session_dir) / EMBEDDINGS_NAME
        self._flush_every = flush_every
        self._items: dict[int, np.ndarray] = {}
        self._dirty = 0
        if self.path.is_file():
            self._items = self.load(session_dir)

    @staticmethod
    def load(session_dir: Path) -> dict[int, np.ndarray]:
        path = Path(session_dir) / EMBEDDINGS_NAME
        if not path.is_file():
            return {}
        with np.load(io.BytesIO(read_protected(path)), allow_pickle=False) as npz:
            seqs = npz["seq"]
            emb = np.asarray(npz["embeddings"], dtype=np.float32)
        return {int(s): emb[i] for i, s in enumerate(seqs)}

    def add(self, seq: int, embedding: np.ndarray) -> None:
        self._items[int(seq)] = np.asarray(embedding, dtype=np.float32)
        self._dirty += 1
        if self._dirty >= self._flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._dirty or not self._items:
            return
        seqs = np.array(sorted(self._items), dtype=np.int64)
        emb = np.vstack([self._items[int(s)] for s in seqs]).astype(np.float32)
        buf = io.BytesIO()
        np.savez(buf, seq=seqs, embeddings=emb)
        write_protected(self.path, buf.getvalue())
        self._dirty = 0

    def close(self) -> None:
        self.flush()


# --------------------------------------------------------------------------- session windows


@dataclass(frozen=True)
class WindowSet:
    """All window embeddings of a session, in the order they were produced."""

    stream: list[str]  # per window: audio stream name ("mic", "loopback")
    t0: np.ndarray  # (n,) float64, session seconds
    t1: np.ndarray
    emb: np.ndarray  # (n, dim) float32, L2-normalised rows

    def __len__(self) -> int:
        return len(self.t0)


class SessionWindows:
    """Window embeddings of one session (biometric: encrypted like ``embeddings.bin``).

    ``steno refine`` clusters these instead of one embedding per segment. ``add`` buffers
    and rewrites the whole file every ``flush_every`` windows and on ``close``.
    """

    def __init__(self, session_dir: Path, flush_every: int = 100):
        self.path = Path(session_dir) / WINDOWS_NAME
        self._flush_every = flush_every
        self._streams: list[str] = []
        self._t0: list[float] = []
        self._t1: list[float] = []
        self._emb: list[np.ndarray] = []
        self._dirty = 0
        existing = self.load(session_dir)
        if existing is not None:
            self._streams = list(existing.stream)
            self._t0, self._t1 = list(existing.t0), list(existing.t1)
            self._emb = list(existing.emb)

    @staticmethod
    def load(session_dir: Path) -> WindowSet | None:
        path = Path(session_dir) / WINDOWS_NAME
        if not path.is_file():
            return None
        with np.load(io.BytesIO(read_protected(path)), allow_pickle=False) as npz:
            meta = json.loads(str(npz["meta"]))
            names = list(meta["streams"])
            return WindowSet(
                stream=[names[int(i)] for i in npz["stream"]],
                t0=np.asarray(npz["t0"], dtype=np.float64),
                t1=np.asarray(npz["t1"], dtype=np.float64),
                emb=np.asarray(npz["embeddings"], dtype=np.float32),
            )

    def add(self, stream: str, t0: float, t1: float, embedding: np.ndarray) -> None:
        self._streams.append(stream)
        self._t0.append(float(t0))
        self._t1.append(float(t1))
        self._emb.append(np.asarray(embedding, dtype=np.float32))
        self._dirty += 1
        if self._dirty >= self._flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._dirty or not self._emb:
            return
        names = sorted(set(self._streams))
        index = {n: i for i, n in enumerate(names)}
        buf = io.BytesIO()
        np.savez(
            buf,
            stream=np.array([index[s] for s in self._streams], dtype=np.int16),
            t0=np.array(self._t0, dtype=np.float64),
            t1=np.array(self._t1, dtype=np.float64),
            embeddings=np.vstack(self._emb).astype(np.float32),
            meta=np.array(json.dumps({"v": 1, "streams": names})),
        )
        write_protected(self.path, buf.getvalue())
        self._dirty = 0

    def close(self) -> None:
        self.flush()
