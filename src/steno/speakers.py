"""Speaker recognition: embeddings, profile matching, session clustering, turns.

Pipeline for one finished VAD segment (see ``SpeakerTracker.identify_turns``):

1. The segment is cut into overlapping windows (``window_s`` every ``window_hop_s``,
   default 1.5 s / 0.75 s); a segment shorter than ``min_embed_s`` is not embedded and
   inherits the previous speaker of its stream (confidence 0, provisional).
2. Every window gets an L2-normalised embedding (``Embedder``; production: sherpa-onnx
   CAM++ via ``SherpaEmbedder``) and a label: ``ProfileMatcher`` first (per enrolled
   person score = max(centroid cosine, mean of the top-3 cosines); accepted when
   >= ``accept_threshold`` AND ahead of the second-best person by ``margin``), else the
   nearest session cluster, else "new".
3. The label sequence is median-filtered; "new" windows pass the ``SessionClusterer``
   gate: a new speaker starts as a tentative cluster and only counts after
   ``confirm_s`` seconds of speech from ``confirm_segments`` segments, otherwise it is
   merged into the nearest confirmed cluster (when it expires and at the end of the
   session; the merge lives in the label layer, the JSONL is never rewritten).
4. The segment is cut into turns where the label changes (``steno.windows``); the
   engine splits the ASR words between the turns.

``identify`` is the older whole-segment path (one embedding, one label) that remains
available with ``window_s = 0``. The tracker never enrols anyone; profiles change only
through explicit commands. All thresholds are uncalibrated defaults (see ``[speakers]``).
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
from steno.profiles import Profile, ProfileStore, SessionEmbeddings, SessionWindows
from steno.windows import (
    cut_turns,
    median_filter_labels,
    snap_boundaries,
    window_centers,
    window_spans,
)

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
    """Online clustering of speaker embeddings with an anti-fragmentation gate.

    Cluster ids are ``S1``, ``S2``, ... in order of creation. After a merge the
    lower-numbered cluster survives; ``pop_merges`` reports ``(dead, survivor)``
    pairs, and ``resolve`` maps any historic id to its current one.

    Three similarity levels (cosine to a running centroid) decide what an embedding
    does, the way diart's ``rho_update`` and 3D-Speaker's minor-cluster rule do:

    * ``>= cluster_threshold``: joins that cluster and moves its centroid;
    * ``>= assign_floor``: gets that cluster's label but does *not* move the centroid
      (an uncertain window must not drag a speaker's centroid towards someone else);
    * below ``assign_floor``: opens a new *tentative* cluster.

    A tentative cluster becomes a *confirmed* speaker only after ``confirm_s`` seconds
    of confidently joined speech from ``confirm_segments`` different segments. An
    unconfirmed cluster that stays untouched for ``ttl_segments`` segments, and every
    unconfirmed one when the session ends (``settle(final=True)``), is merged into the
    nearest confirmed cluster. Noises of one or two seconds therefore cannot create
    "Speaker 6"; the merge is recorded in the label layer, so earlier transcript lines
    follow without rewriting the JSONL. With ``assign_floor=None`` and
    ``confirm_s=0`` this is the classic leader-follower clustering of ``assign``.
    """

    def __init__(
        self,
        cluster_threshold: float = 0.5,
        merge_threshold: float = 0.7,
        *,
        assign_floor: float | None = None,
        confirm_s: float = 0.0,
        confirm_segments: int = 1,
        ttl_segments: int = 0,
        max_speakers: int = 0,
    ):
        self.cluster_threshold = cluster_threshold
        self.merge_threshold = merge_threshold
        self.assign_floor = cluster_threshold if assign_floor is None else assign_floor
        self.confirm_s = confirm_s
        self.confirm_segments = confirm_segments
        self.ttl_segments = ttl_segments
        self.max_speakers = max_speakers
        self._sum: dict[str, np.ndarray] = {}
        self._count: dict[str, int] = {}
        self._speech: dict[str, float] = {}
        self._segs: dict[str, set[int]] = {}
        self._touched: dict[str, int] = {}
        self._clock = 0  # segments seen so far
        self._next = 1
        self._alias: dict[str, str] = {}
        self._merges: list[tuple[str, str]] = []

    # -- queries ---------------------------------------------------------------------

    @property
    def cluster_ids(self) -> list[str]:
        return sorted(self._sum, key=lambda c: int(c[1:]))

    def centroid(self, cluster_id: str) -> np.ndarray:
        return l2_normalize(self._sum[cluster_id])

    def count(self, cluster_id: str) -> int:
        return self._count[cluster_id]

    def speech_s(self, cluster_id: str) -> float:
        return self._speech[cluster_id]

    def resolve(self, cluster_id: str) -> str:
        while cluster_id in self._alias:
            cluster_id = self._alias[cluster_id]
        return cluster_id

    def pop_merges(self) -> list[tuple[str, str]]:
        out, self._merges = self._merges, []
        return out

    def confirmed(self, cluster_id: str) -> bool:
        return (
            self._speech[cluster_id] >= self.confirm_s
            and len(self._segs[cluster_id]) >= self.confirm_segments
        )

    def nearest(self, emb: np.ndarray) -> tuple[str | None, float]:
        """Most similar cluster (any state) and its cosine; ``(None, -1)`` when empty."""
        emb = l2_normalize(emb)
        best_id, best = None, -1.0
        for cid in self._sum:
            sim = float(self.centroid(cid) @ emb)
            if sim > best:
                best_id, best = cid, sim
        return best_id, best

    # -- updates ---------------------------------------------------------------------

    def begin_segment(self) -> int:
        """Mark the start of a new segment; settles expired tentative clusters."""
        self._clock += 1
        if self.ttl_segments > 0:
            self.settle()
        return self._clock

    def update(
        self, cid: str, emb: np.ndarray, dur: float = 0.0, segment: int | None = None
    ) -> None:
        """Move ``cid``'s centroid towards ``emb`` (``dur`` seconds of speech from ``segment``)."""
        self._sum[cid] = self._sum[cid] + l2_normalize(emb)
        self._count[cid] += 1
        self._speech[cid] += dur
        self._segs[cid].add(self._clock if segment is None else segment)
        self._touched[cid] = self._clock

    def create(self, emb: np.ndarray, dur: float = 0.0, segment: int | None = None) -> str:
        cid = f"S{self._next}"
        self._next += 1
        self._sum[cid] = l2_normalize(emb)
        self._count[cid] = 1
        self._speech[cid] = dur
        self._segs[cid] = {self._clock if segment is None else segment}
        self._touched[cid] = self._clock
        return cid

    def observe(
        self, emb: np.ndarray, dur: float = 0.0, segment: int | None = None
    ) -> tuple[str, float]:
        """Classify ``emb`` and apply the gate. Returns ``(cluster id, score)``.

        The score is the cosine to the chosen cluster's centroid before the update
        (cosine to the nearest cluster for a new one, 0 if there is none).
        """
        emb = l2_normalize(emb)
        best_id, best = self.nearest(emb)
        full = self.max_speakers > 0 and len(self._sum) >= self.max_speakers
        if best_id is not None and best >= self.cluster_threshold:
            self.update(best_id, emb, dur, segment)
            cid = best_id
        elif best_id is not None and (best >= self.assign_floor or full):
            cid = best_id  # label only: the centroid stays where it is
        else:
            cid = self.create(emb, dur, segment)
        cid = self._merge_close(cid)
        return cid, max(best, 0.0) if best_id is not None else 0.0

    def assign(self, emb: np.ndarray) -> tuple[str, float]:
        """Classic whole-embedding leader-follower step (see ``observe``)."""
        return self.observe(emb)

    # -- settling and merging ----------------------------------------------------------

    def settle(self, final: bool = False) -> None:
        """Merge unconfirmed clusters (expired ones, or all when ``final``) into the nearest
        confirmed cluster. If nothing is confirmed, the best-supported cluster is kept."""
        if self.confirm_s <= 0 and self.confirm_segments <= 1:
            return
        while True:
            ids = self.cluster_ids
            confirmed = [c for c in ids if self.confirmed(c)]
            if not confirmed and ids:
                confirmed = [max(ids, key=lambda c: self._speech[c])]
            weak = [
                c
                for c in ids
                if c not in confirmed
                and (final or self._clock - self._touched[c] >= self.ttl_segments)
            ]
            if not weak or not confirmed:
                return
            dead = min(weak, key=lambda c: self._speech[c])
            keep = max(confirmed, key=lambda c: float(self.centroid(c) @ self.centroid(dead)))
            self._absorb(keep, dead)

    def _absorb(self, keep: str, dead: str) -> None:
        self._sum[keep] = self._sum[keep] + self._sum.pop(dead)
        self._count[keep] += self._count.pop(dead)
        self._speech[keep] += self._speech.pop(dead)
        self._segs[keep] |= self._segs.pop(dead)
        self._touched[keep] = max(self._touched[keep], self._touched.pop(dead))
        self._alias[dead] = keep
        self._merges.append((dead, keep))

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
                self._absorb(best[1], best[2])  # ids sorted by number: keep the older one
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


@dataclass(frozen=True)
class TurnLabel:
    """One speaker turn inside a segment; times are seconds from the start of its audio."""

    t_start: float
    t_end: float
    label: SpeakerLabel


NEW = "?"  # window label: dissimilar to every known speaker, still to be placed


class SpeakerTracker:
    """Live speaker identification for one session. Not thread-safe: use from one thread."""

    def __init__(
        self,
        embedder: Embedder,
        cfg: SpeakersConfig,
        store: ProfileStore | None = None,
        labels: SessionLabels | None = None,
        embeddings: SessionEmbeddings | None = None,
        windows: SessionWindows | None = None,
        *,
        reload_interval_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.embedder = embedder
        self.cfg = cfg
        self.store = store
        self.labels = labels
        self.embeddings = embeddings
        self.windows = windows
        self.clusterer = SessionClusterer(
            cfg.cluster_threshold,
            cfg.merge_threshold,
            assign_floor=cfg.assign_floor,
            confirm_s=cfg.confirm_s,
            confirm_segments=cfg.confirm_segments,
            ttl_segments=cfg.tentative_ttl,
            max_speakers=cfg.max_speakers,
        )
        self._last: dict[str, str] = {}  # stream -> last speaker id
        self._announced: set[str] = set()  # clusters already written to speakers.json
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

    def _sync_labels(self) -> None:
        """Mirror cluster creation and merges into ``speakers.json``."""
        merges = self.clusterer.pop_merges()
        if self.labels is None:
            return
        for cid in self.clusterer.cluster_ids:
            if cid not in self._announced:
                self.labels.ensure_cluster(cid)
                self._announced.add(cid)
        for dead, survivor in merges:
            self.labels.merge(dead, survivor)

    def _profile_label(self, match: Match) -> SpeakerLabel:
        assert match.profile is not None
        pid = match.profile.id
        if self.labels is not None:
            self.labels.set_profile_name(pid, match.profile.name)
        return SpeakerLabel(PROFILE_PREFIX + pid, match.profile.name, match.score, False)

    # -- whole-segment path (``window_s = 0``) -------------------------------------

    def identify(self, stream: str, audio: np.ndarray) -> SpeakerLabel:
        """One label for the whole segment (a single embedding)."""
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
            label = self._profile_label(match)
            self._last[stream] = label.speaker_id
            return SpeakerLabel(label.speaker_id, label.name, label.conf, False, emb)
        segment = self.clusterer.begin_segment()
        cid, score = self.clusterer.observe(emb, len(audio) / SAMPLE_RATE, segment)
        self._sync_labels()
        cid = self.clusterer.resolve(cid)
        self._last[stream] = cid
        return SpeakerLabel(cid, self._name_for(cid), score, True, emb)

    # -- turns ---------------------------------------------------------------------

    def identify_turns(
        self, stream: str, audio: np.ndarray, t_start: float = 0.0
    ) -> list[TurnLabel]:
        """Split one segment into speaker turns (see ``steno.windows``).

        ``t_start`` is the segment's session time, only used to date the stored window
        embeddings. Never returns an empty list: a segment too short to embed is one
        turn that inherits the previous speaker of its stream.
        """
        cfg = self.cfg
        dur = len(audio) / SAMPLE_RATE
        if cfg.window_s <= 0:
            return [TurnLabel(0.0, dur, self.identify(stream, audio))]
        spans = window_spans(dur, cfg.window_s, cfg.window_hop_s, cfg.min_embed_s)
        embs: list[np.ndarray] = []
        kept: list[tuple[float, float]] = []
        for a, b in spans:
            try:
                embs.append(self.embedder.embed(audio[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)]))
                kept.append((a, b))
            except Exception:
                log.exception("Speaker embedding failed for a window")
        if not embs:
            return [TurnLabel(0.0, dur, self._inherit(stream))]
        spans = kept
        self._maybe_reload()
        segment = self.clusterer.begin_segment()
        if self.windows is not None:
            for (a, b), emb in zip(spans, embs, strict=True):
                self.windows.add(stream, t_start + a, t_start + b, emb)

        # Pass 1: classify every window without changing any cluster.
        raw: list[str] = []
        matches: list[Match | None] = []
        profile_labels: dict[str, SpeakerLabel] = {}
        for emb in embs:
            match = self._matcher.match(emb)
            if match.accepted and match.profile is not None:
                sid = PROFILE_PREFIX + match.profile.id
                raw.append(sid)
                matches.append(match)
                profile_labels.setdefault(sid, self._profile_label(match))
                continue
            matches.append(None)
            cid, sim = self.clusterer.nearest(emb)
            raw.append(cid if cid is not None and sim >= self.clusterer.assign_floor else NEW)
        smooth = median_filter_labels(raw, cfg.smooth_windows)

        # Pass 2: apply the clustering gate to the smoothed labels.
        centers = window_centers(spans)
        cuts = [0.0] + [(centers[i] + centers[i + 1]) / 2 for i in range(len(spans) - 1)] + [dur]
        final: list[str] = []
        scores: list[float] = []
        for i, (emb, lab) in enumerate(zip(embs, smooth, strict=True)):
            core = cuts[i + 1] - cuts[i]
            if lab == NEW:
                cid, score = self.clusterer.observe(emb, core, segment)
            elif lab.startswith(PROFILE_PREFIX):
                cid = lab
                m = matches[i]
                score = m.score if m is not None and raw[i] == lab else 0.0
            else:
                cid = self.clusterer.resolve(lab)
                score = float(self.clusterer.centroid(cid) @ l2_normalize(emb))
                if raw[i] == lab and score >= self.clusterer.cluster_threshold:
                    self.clusterer.update(cid, emb, core, segment)
            final.append(cid)
            scores.append(score)
        self._sync_labels()
        final = [x if x.startswith(PROFILE_PREFIX) else self.clusterer.resolve(x) for x in final]

        turns = cut_turns(spans, final, dur, cfg.min_turn_s)
        if cfg.snap_boundaries:
            turns = snap_boundaries(turns, audio)
        out: list[TurnLabel] = []
        for turn in turns:
            idx = [i for i, c in enumerate(centers) if turn.t_start <= c < turn.t_end]
            same = [i for i in idx if final[i] == turn.label]
            idx = same or idx or [min(range(len(centers)), key=lambda i: abs(
                centers[i] - (turn.t_start + turn.t_end) / 2))]
            emb = l2_normalize(np.mean([embs[i] for i in idx], axis=0))
            conf = float(np.mean([scores[i] for i in idx]))
            sid = turn.label
            if sid.startswith(PROFILE_PREFIX):
                name = profile_labels[sid].name if sid in profile_labels else self._name_for(sid)
                label = SpeakerLabel(sid, name, conf, False, emb)
            else:
                label = SpeakerLabel(sid, self._name_for(sid), conf, True, emb)
            out.append(TurnLabel(turn.t_start, turn.t_end, label))
        self._last[stream] = out[-1].label.speaker_id
        return out

    def record(self, seq: int, label: SpeakerLabel) -> None:
        """Store the embedding of the segment that was written as ``seq``."""
        if self.embeddings is not None and label.embedding is not None:
            self.embeddings.add(seq, label.embedding)

    def close(self) -> None:
        """Merge speakers that never got enough evidence, then flush the stores."""
        self.clusterer.settle(final=True)
        try:
            self._sync_labels()
        except Exception:
            log.exception("Could not record final speaker merges")
        if self.embeddings is not None:
            self.embeddings.close()
        if self.windows is not None:
            self.windows.close()
