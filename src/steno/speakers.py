"""Speaker recognition: embeddings, profile matching, session clustering.

Pipeline for one finished VAD segment (see ``SpeakerTracker.identify``):

1. Shorter than ``min_embed_s``: no embedding; inherit the previous speaker of the
   same stream with confidence 0 (provisional).
2. Otherwise compute an L2-normalised embedding (``Embedder``; production:
   sherpa-onnx CAM++ via ``SherpaEmbedder``).
3. ``ProfileMatcher``: per enrolled person score = max(centroid cosine, mean of the
   top-3 cosines). Accept when score >= ``accept_threshold`` AND the margin to the
   second-best person is >= ``margin``.
4. Otherwise ``SessionClusterer`` (leader-follower): join the nearest running
   centroid when cosine >= ``cluster_threshold``, else open "Speaker N"; two
   clusters whose centroids reach ``merge_threshold`` are merged. Live labels are
   provisional; ``steno refine`` recomputes them offline.

The tracker never enrols anyone. Profiles change only through explicit commands.
All thresholds are uncalibrated defaults (see ``[speakers]`` in the config).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from steno import paths
from steno.config import SpeakersConfig
from steno.labels import PROFILE_PREFIX, UNKNOWN_ID, UNKNOWN_NAME, SessionLabels
from steno.profiles import Profile, ProfileStore, SessionEmbeddings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
#: GitHub release tag of k2-fsa/sherpa-onnx that hosts the speaker models. The
#: misspelling ("recongition") is the real tag name.
MODEL_URL_BASE = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
)
TOP_K = 3
INHERIT_CONF = 0.0


class SpeakerError(RuntimeError):
    """Speaker recognition cannot start (missing package, model download failed)."""


# --------------------------------------------------------------------------- math


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32)
    return vec / max(float(np.linalg.norm(vec)), 1e-12)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(l2_normalize(a), l2_normalize(b)))


# --------------------------------------------------------------------------- embedder


class Embedder(Protocol):
    """Maps 16 kHz mono float32 audio to an L2-normalised speaker embedding."""

    name: str

    def load(self) -> None: ...

    def embed(self, audio: np.ndarray) -> np.ndarray: ...


def model_path(model: str) -> Path:
    if not model or "/" in model or "\\" in model or model.startswith("."):
        raise SpeakerError(f"invalid [speakers] model name: {model!r}")
    return paths.models_dir() / model


def download_model(model: str, dest: Path | None = None) -> Path:
    """Fetch ``model`` from the sherpa-onnx release into the models dir (once)."""
    dest = dest or model_path(model)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    url = MODEL_URL_BASE + model
    tmp = dest.with_name(dest.name + ".part")
    log.info("Downloading speaker model %s", url)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as fh:  # noqa: S310
            expected = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
                done += len(chunk)
        if expected and done != expected:
            raise SpeakerError(f"incomplete download ({done} of {expected} bytes)")
        os.replace(tmp, dest)
    except (urllib.error.URLError, OSError, SpeakerError) as exc:
        tmp.unlink(missing_ok=True)
        raise SpeakerError(
            f"Could not download speaker model '{model}' from the sherpa-onnx releases ({exc}). "
            f"Download it manually into {dest.parent.name}/ or set [speakers] enabled = false."
        ) from exc
    return dest


class SherpaEmbedder:
    """sherpa-onnx ``SpeakerEmbeddingExtractor`` on CPU (thread-safe: calls are serialised)."""

    def __init__(self, model: str, num_threads: int = 2):
        self.name = model
        self._model = model
        self._threads = num_threads
        self._extractor = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._extractor is not None:
            return
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise SpeakerError(
                "Speaker recognition needs the 'speakers' extra (sherpa-onnx): "
                "uv sync --extra speakers  (or set [speakers] enabled = false)"
            ) from exc
        path = download_model(self._model)
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(path), num_threads=self._threads, debug=False, provider="cpu"
        )
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        log.info("Loaded speaker model %s (dim %d)", self._model, self._extractor.dim)

    def embed(self, audio: np.ndarray) -> np.ndarray:
        self.load()
        assert self._extractor is not None
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        with self._lock:
            stream = self._extractor.create_stream()
            stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=samples)
            stream.input_finished()
            if not self._extractor.is_ready(stream):
                raise SpeakerError("audio too short for the speaker model")
            return l2_normalize(np.asarray(self._extractor.compute(stream), dtype=np.float32))


def create_embedder(cfg: SpeakersConfig) -> Embedder:
    return SherpaEmbedder(cfg.model, cfg.num_threads)


# --------------------------------------------------------------------------- matching


@dataclass(frozen=True)
class Match:
    profile: Profile | None  # best-scoring person (even when not accepted)
    score: float
    second: float  # score of the runner-up (-1.0 when there is none)
    accepted: bool


def profile_score(profile: Profile, query: np.ndarray) -> float:
    """max(centroid cosine, mean of the top-3 cosines to the profile's embeddings)."""
    q = l2_normalize(query)
    sims = profile.embeddings @ q
    top = np.sort(sims)[-TOP_K:]
    return float(max(float(profile.centroid() @ q), float(top.mean())))


class ProfileMatcher:
    def __init__(self, profiles: list[Profile], accept_threshold: float, margin: float):
        self.profiles = [p for p in profiles if len(p.embeddings)]
        self.accept_threshold = accept_threshold
        self.margin = margin

    def match(self, query: np.ndarray) -> Match:
        if not self.profiles:
            return Match(None, -1.0, -1.0, False)
        scored = sorted(
            ((profile_score(p, query), p) for p in self.profiles), key=lambda t: t[0], reverse=True
        )
        best, profile = scored[0]
        second = scored[1][0] if len(scored) > 1 else -1.0
        accepted = best >= self.accept_threshold and (best - second) >= self.margin
        return Match(profile, best, second, accepted)


# --------------------------------------------------------------------------- clustering


class SessionClusterer:
    """Leader-follower clustering with running centroids and merging.

    Cluster ids are ``S1``, ``S2``, ... in order of creation. After a merge the
    lower-numbered cluster survives; ``pop_merges`` reports ``(dead, survivor)``
    pairs, and ``resolve`` maps any historic id to its current one.
    """

    def __init__(self, cluster_threshold: float = 0.5, merge_threshold: float = 0.7):
        self.cluster_threshold = cluster_threshold
        self.merge_threshold = merge_threshold
        self._sum: dict[str, np.ndarray] = {}
        self._count: dict[str, int] = {}
        self._next = 1
        self._alias: dict[str, str] = {}
        self._merges: list[tuple[str, str]] = []

    @property
    def cluster_ids(self) -> list[str]:
        return sorted(self._sum, key=lambda c: int(c[1:]))

    def centroid(self, cluster_id: str) -> np.ndarray:
        return l2_normalize(self._sum[cluster_id])

    def count(self, cluster_id: str) -> int:
        return self._count[cluster_id]

    def resolve(self, cluster_id: str) -> str:
        while cluster_id in self._alias:
            cluster_id = self._alias[cluster_id]
        return cluster_id

    def pop_merges(self) -> list[tuple[str, str]]:
        out, self._merges = self._merges, []
        return out

    def assign(self, emb: np.ndarray) -> tuple[str, float]:
        """Add ``emb``; return ``(cluster id, cosine to that cluster's centroid before adding)``.

        For a new cluster the score is the cosine to the nearest existing centroid (0 if none).
        """
        emb = l2_normalize(emb)
        best_id, best = None, -1.0
        for cid in self._sum:
            sim = float(self.centroid(cid) @ emb)
            if sim > best:
                best_id, best = cid, sim
        if best_id is not None and best >= self.cluster_threshold:
            self._sum[best_id] = self._sum[best_id] + emb
            self._count[best_id] += 1
            cid = best_id
        else:
            cid = f"S{self._next}"
            self._next += 1
            self._sum[cid] = emb.copy()
            self._count[cid] = 1
        cid = self._merge_close(cid)
        return cid, max(best, 0.0) if best_id is not None else 0.0

    def _merge_close(self, touched: str) -> str:
        """Merge clusters whose centroids reach the threshold; returns ``touched``'s survivor."""
        changed = True
        while changed:
            changed = False
            ids = self.cluster_ids
            best = (self.merge_threshold, None, None)
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    sim = float(self.centroid(a) @ self.centroid(b))
                    if sim >= best[0]:
                        best = (sim, a, b)
            if best[1] is not None:
                keep, dead = best[1], best[2]  # ids sorted by number: keep the older one
                self._sum[keep] = self._sum[keep] + self._sum.pop(dead)
                self._count[keep] += self._count.pop(dead)
                self._alias[dead] = keep
                self._merges.append((dead, keep))
                changed = True
        return self.resolve(touched)


# --------------------------------------------------------------------------- agglomerative


def agglomerative_average(emb: np.ndarray, threshold: float) -> np.ndarray:
    """Average-linkage agglomerative clustering on cosine similarity (pure numpy).

    Repeatedly merges the two clusters with the highest average pairwise similarity
    until that similarity falls below ``threshold``. Returns an int label per row,
    numbered in order of first appearance.
    """
    emb = np.asarray(emb, dtype=np.float64)
    n = len(emb)
    if n == 0:
        return np.zeros(0, dtype=int)
    emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12)
    sim = emb @ emb.T
    np.fill_diagonal(sim, -np.inf)
    size = np.ones(n)
    active = np.ones(n, dtype=bool)
    members: list[list[int]] = [[i] for i in range(n)]
    while active.sum() > 1:
        flat = int(np.argmax(sim))
        i, j = divmod(flat, n)
        if sim[i, j] < threshold:
            break
        merged = (size[i] * sim[i] + size[j] * sim[j]) / (size[i] + size[j])
        sim[i, :] = merged
        sim[:, i] = merged
        sim[i, i] = -np.inf
        sim[j, :] = -np.inf
        sim[:, j] = -np.inf
        size[i] += size[j]
        active[j] = False
        members[i].extend(members[j])
        members[j] = []
    labels = np.full(n, -1, dtype=int)
    for k, group in enumerate(g for g in members if g):
        labels[group] = k
    # renumber by first appearance
    order: dict[int, int] = {}
    for idx in range(n):
        order.setdefault(int(labels[idx]), len(order))
    return np.array([order[int(x)] for x in labels], dtype=int)


# --------------------------------------------------------------------------- tracker


@dataclass(frozen=True)
class SpeakerLabel:
    speaker_id: str
    name: str
    conf: float
    provisional: bool
    embedding: np.ndarray | None = None  # kept for the session embedding store


class SpeakerTracker:
    """Live speaker identification for one session. Not thread-safe: use from one thread."""

    def __init__(
        self,
        embedder: Embedder,
        cfg: SpeakersConfig,
        store: ProfileStore | None = None,
        labels: SessionLabels | None = None,
        embeddings: SessionEmbeddings | None = None,
        *,
        reload_interval_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.embedder = embedder
        self.cfg = cfg
        self.store = store
        self.labels = labels
        self.embeddings = embeddings
        self.clusterer = SessionClusterer(cfg.cluster_threshold, cfg.merge_threshold)
        self._last: dict[str, str] = {}  # stream -> last speaker id
        self._reload_interval = reload_interval_s
        self._clock = clock
        self._next_reload = 0.0
        self._signature: object = None
        self._matcher = ProfileMatcher([], cfg.accept_threshold, cfg.margin)
        self._maybe_reload(force=True)

    # -- profiles ------------------------------------------------------------------

    def _maybe_reload(self, force: bool = False) -> None:
        """Pick up profiles created meanwhile, e.g. by ``steno name`` during a meeting."""
        if self.store is None:
            return
        now = self._clock()
        if not force and now < self._next_reload:
            return
        self._next_reload = now + self._reload_interval
        signature = self.store.signature()
        if not force and signature == self._signature:
            return
        self._signature = signature
        try:
            profiles = self.store.list()
        except Exception:
            log.exception("Could not load voice profiles")
            return
        self._matcher = ProfileMatcher(profiles, self.cfg.accept_threshold, self.cfg.margin)

    # -- naming --------------------------------------------------------------------

    def _name_for(self, speaker_id: str, fallback: str = "") -> str:
        if self.labels is not None:
            return self.labels.display_name(speaker_id, fallback)
        n = speaker_id[1:] if speaker_id[:1] == "S" and speaker_id[1:].isdigit() else ""
        return f"Speaker {n}" if n else (fallback or speaker_id)

    def _inherit(self, stream: str) -> SpeakerLabel:
        prev = self._last.get(stream)
        if prev is None:
            return SpeakerLabel(UNKNOWN_ID, UNKNOWN_NAME, INHERIT_CONF, True)
        prev = self.clusterer.resolve(prev)
        self._last[stream] = prev
        return SpeakerLabel(prev, self._name_for(prev), INHERIT_CONF, True)

    # -- main entry ----------------------------------------------------------------

    def identify(self, stream: str, audio: np.ndarray) -> SpeakerLabel:
        if len(audio) / SAMPLE_RATE < self.cfg.min_embed_s:
            return self._inherit(stream)
        try:
            emb = self.embedder.embed(audio)
        except Exception:
            log.exception("Speaker embedding failed; inheriting previous speaker")
            return self._inherit(stream)
        self._maybe_reload()
        match = self._matcher.match(emb)
        if match.accepted and match.profile is not None:
            pid = match.profile.id
            sid = PROFILE_PREFIX + pid
            if self.labels is not None:
                self.labels.set_profile_name(pid, match.profile.name)
            self._last[stream] = sid
            return SpeakerLabel(sid, match.profile.name, match.score, False, emb)
        cid, score = self.clusterer.assign(emb)
        if self.labels is not None:
            self.labels.ensure_cluster(cid)
            for dead, survivor in self.clusterer.pop_merges():
                self.labels.merge(dead, survivor)
        else:
            self.clusterer.pop_merges()
        self._last[stream] = cid
        return SpeakerLabel(cid, self._name_for(cid), score, True, emb)

    def record(self, seq: int, label: SpeakerLabel) -> None:
        """Store the embedding of the segment that was written as ``seq``."""
        if self.embeddings is not None and label.embedding is not None:
            self.embeddings.add(seq, label.embedding)

    def close(self) -> None:
        if self.embeddings is not None:
            self.embeddings.close()
