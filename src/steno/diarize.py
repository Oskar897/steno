"""Offline diarization: pyannote segmentation windows + CAM++ embeddings + Steno clustering.

This is the "who spoke when" half of ``steno finalize`` (pyannote 3.1's recipe, with
Steno's own clustering and profile matching in the middle):

1. ``extract_stream``: slide the segmentation model over the audio (10 s windows, a new
   one every ``step_s``). In every window up to three *local* speakers are active, and up
   to two at the same time. Each local speaker's audio (frames where only that speaker is
   active; all of its frames when too little is clean) gets a CAM++ embedding. This is the
   expensive step and it does not depend on any clustering choice.
2. ``resolve``: the clean embeddings of all streams are clustered together with Steno's
   spectral / eigengap clustering and minor-cluster reassignment (``steno.cluster``;
   ``num_speakers`` fixes the count). Every local speaker is then linked to a global
   cluster by the similarity to the cluster centroids, never giving two local speakers
   that overlap in time the same cluster. Clusters are matched to enrolled profiles.
3. The per-window activities are stitched (Hamming-weighted mean on the global frame grid,
   see ``steno.segmentation``) into one activity curve per global speaker; the number of
   simultaneous speakers per frame is the rounded sum of the local activities, so overlap
   frames can carry two speakers and a spurious extra speaker is not added to a clean frame.

The result is a list of speaker turns per stream with cluster labels; ``steno.finalize``
turns them into the final transcript. Everything but the two model calls is pure numpy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from steno.cluster import cluster_embeddings
from steno.config import SpeakersConfig
from steno.labels import PROFILE_PREFIX
from steno.segmentation import (
    FRAME_S,
    NUM_FRAMES,
    NUM_LOCAL,
    SAMPLE_RATE,
    PyannoteSegmenter,
    SegmentationResult,
    aggregate_windows,
    frame_start_sample,
    step_frames_for,
    total_frames,
)
from steno.speakers import Embedder, ProfileMatcher, l2_normalize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiarizeParams:
    step_s: float = 2.5  # a segmentation window every this many seconds (10 s windows)
    min_clean_s: float = 1.0  # clean speech a local speaker needs to take part in clustering
    min_weak_s: float = 0.4  # ... to be embedded at all (then only assigned, never clustered)
    max_embed_s: float = 8.0  # audio per local-speaker embedding is cut at this length
    conflict_frames: int = 5  # local speakers sharing this many frames cannot be one person
    min_act: float = 0.2  # a speaker needs this aggregated activity to be picked for a frame
    min_on_s: float = 0.1  # shorter speaker turns are dropped ...
    min_off_s: float = 0.15  # ... and shorter gaps inside one speaker's turn are filled
    minor_points: int = 4  # clusters of at most this many local speakers are reassigned
    min_speech: float = 0.4  # aggregated speech probability a frame needs to get a speaker
    count_bias: float = 0.5  # a 2nd speaker needs summed activity >= 2 - bias (0.5: 1.5)
    split_cos: float | None = 0.78  # estimate of 1: accept a 2-way split when the centroid cosine
    split_min_s: float = 20.0  # ... is at most this and the minority has this much solo speech


@dataclass
class LocalEmb:
    k: int  # window index
    s: int  # local speaker index (0-2)
    vec: np.ndarray
    dur: float  # seconds of audio the embedding came from
    clean: bool  # True when taken from frames where this speaker is alone


@dataclass
class StreamStats:
    """Everything ``resolve`` needs about one audio stream (no audio)."""

    stream: str
    n_samples: int
    step_frames: int
    soft: np.ndarray  # (K, 589, 3) float32, activity probability of each local speaker
    hard: np.ndarray  # (K, 589, 3) bool, speakers of the most likely powerset class
    embs: list[LocalEmb] = field(default_factory=list)
    speech: np.ndarray | None = None  # (K, 589) float32, P(any speaker); None: derived from soft

    @property
    def n_windows(self) -> int:
        return len(self.soft)


@dataclass(frozen=True)
class SpeakerTurn:
    t_start: float
    t_end: float
    cluster: int  # index into DiarizationResult.speaker_ids
    stream: str


@dataclass
class DiarizationResult:
    turns: list[SpeakerTurn]
    speaker_ids: list[str]  # per cluster: "S1", "S2", ... or "P:<profile id>"
    profile_names: dict[str, str]  # profile id -> name, for clusters matched to a profile
    centroids: np.ndarray  # (n_clusters, dim)
    n_points: int = 0  # local speakers that were clustered

    def labelled(self) -> list[tuple[float, float, str, str]]:
        """``(start, end, speaker id, stream)`` per turn."""
        return [(t.t_start, t.t_end, self.speaker_ids[t.cluster], t.stream) for t in self.turns]


# --------------------------------------------------------------------------- extraction


def mask_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """``[a, b)`` index ranges of the True runs in a boolean vector."""
    m = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    edges = np.flatnonzero(m[1:] != m[:-1])
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def frames_audio(
    audio: np.ndarray, base: int, mask: np.ndarray, max_s: float = 0.0
) -> np.ndarray:
    """Concatenated audio of the frames flagged in ``mask`` (window-local indices).

    ``base`` is the global index of the window's first frame; ``max_s`` > 0 cuts the result.
    """
    parts = []
    n = len(audio)
    for a, b in mask_runs(mask):
        lo, hi = int(frame_start_sample(base + a)), int(frame_start_sample(base + b))
        lo, hi = max(0, min(lo, n)), max(0, min(hi, n))
        if hi > lo:
            parts.append(audio[lo:hi])
    if not parts:
        return np.zeros(0, dtype=np.float32)
    wave = np.concatenate(parts)
    return wave[: int(max_s * SAMPLE_RATE)] if max_s > 0 else wave


def local_masks(hard: np.ndarray, valid: int) -> list[tuple[int, np.ndarray, np.ndarray]]:
    """``(speaker, all active frames, frames where it is the only speaker)`` for one window."""
    h = hard[:valid]
    single = h.sum(axis=-1) == 1
    return [(s, h[:, s], h[:, s] & single) for s in range(NUM_LOCAL) if h[:, s].any()]


def extract_stream(
    stream: str,
    audio: np.ndarray,
    segmenter: PyannoteSegmenter,
    embedder: Embedder,
    params: DiarizeParams | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> StreamStats:
    """Segmentation windows plus one embedding per local speaker of every window."""
    params = params or DiarizeParams()
    audio = np.asarray(audio, dtype=np.float32)
    step = step_frames_for(params.step_s)
    seg: SegmentationResult = segmenter.run(
        audio, step, (lambda d, n: progress("segmentation", d, n)) if progress else None
    )
    stats = StreamStats(stream, len(audio), step, seg.soft(), seg.hard(),
                        speech=seg.speech())
    n_frames = seg.n_frames
    for k in range(stats.n_windows):
        base = k * step
        valid = min(NUM_FRAMES, n_frames - base)
        if valid <= 0:
            break
        for s, active, clean in local_masks(stats.hard[k], valid):
            if clean.sum() * FRAME_S >= params.min_clean_s:
                mask, is_clean = clean, True
            elif active.sum() * FRAME_S >= params.min_weak_s:
                mask, is_clean = active, False
            else:
                continue
            wave = frames_audio(audio, base, mask, params.max_embed_s)
            if len(wave) < params.min_weak_s * SAMPLE_RATE:
                continue
            try:
                vec = embedder.embed(wave)
            except Exception:
                log.debug("embedding failed for window %d speaker %d", k, s, exc_info=True)
                continue
            stats.embs.append(LocalEmb(k, s, vec, len(wave) / SAMPLE_RATE, is_clean))
        if progress is not None:
            progress("embedding", k + 1, stats.n_windows)
    return stats


# --------------------------------------------------------------------------- resolving


def link_local_speakers(
    stats: StreamStats,
    centroids: np.ndarray,
    params: DiarizeParams,
    constrain: bool = True,
) -> dict[tuple[int, int], int]:
    """Global cluster of every embedded local speaker: ``(window, local speaker) -> cluster``.

    Each local speaker takes its most similar centroid; with ``constrain``, a cluster that
    is already taken by a local speaker of the same window that overlaps in time
    (``conflict_frames`` shared frames) is skipped, strongest similarity first, and a
    speaker with no free cluster is left unassigned.
    """
    by_window: dict[int, list[LocalEmb]] = {}
    for e in stats.embs:
        by_window.setdefault(e.k, []).append(e)
    out: dict[tuple[int, int], int] = {}
    for k, locals_ in by_window.items():
        sims = {e.s: centroids @ l2_normalize(e.vec) for e in locals_}
        if not constrain or len(locals_) == 1:
            for e in locals_:
                out[(k, e.s)] = int(np.argmax(sims[e.s]))
            continue
        hard = stats.hard[k]
        order = sorted(locals_, key=lambda e: -float(sims[e.s].max()))
        taken: dict[int, int] = {}  # cluster -> local speaker holding it
        for e in order:
            for c in np.argsort(-sims[e.s]):
                holder = taken.get(int(c))
                clash = holder is not None and (
                    int((hard[:, e.s] & hard[:, holder]).sum()) >= params.conflict_frames
                )
                if not clash:
                    taken[int(c)] = e.s
                    out[(k, e.s)] = int(c)
                    break
    return out


def stitch_stream(
    stats: StreamStats,
    assignment: dict[tuple[int, int], int],
    n_clusters: int,
    params: DiarizeParams,
) -> np.ndarray:
    """Boolean ``(n_frames, n_clusters)`` speaker activity of one stream."""
    n_frames = total_frames(stats.n_samples)
    k_win = stats.n_windows
    per_cluster = np.zeros((k_win, NUM_FRAMES, n_clusters), dtype=np.float32)
    for (k, s), c in assignment.items():
        np.maximum(per_cluster[k, :, c], stats.soft[k, :, s], out=per_cluster[k, :, c])
    num, den = aggregate_windows(per_cluster, stats.step_frames, n_frames)
    act = num / np.maximum(den, 1e-9)
    # Speech or not comes from P(any speaker); a second simultaneous speaker from the summed
    # activity of all local speakers (linked or not), so that a spurious extra speaker does
    # not turn a clean frame into overlap.
    total = stats.soft.sum(axis=-1, keepdims=True)
    cnum, cden = aggregate_windows(total, stats.step_frames, n_frames)
    expected = (cnum / np.maximum(cden, 1e-9))[:, 0]
    speech = stats.speech if stats.speech is not None else np.minimum(stats.soft.sum(-1), 1.0)
    snum, sden = aggregate_windows(speech[..., None], stats.step_frames, n_frames)
    p_speech = (snum / np.maximum(sden, 1e-9))[:, 0]
    count = (p_speech >= params.min_speech).astype(int) + (
        (p_speech >= params.min_speech) & (expected >= 2.0 - params.count_bias)
    ).astype(int)
    rank = np.argsort(np.argsort(-act, axis=1), axis=1)
    chosen = (rank < count[:, None]) & (act >= params.min_act)
    return chosen


def smooth_activity(active: np.ndarray, min_on: int, min_off: int) -> np.ndarray:
    """Fill gaps shorter than ``min_off`` frames, then drop runs shorter than ``min_on``."""
    out = np.asarray(active, dtype=bool).copy()
    if min_off > 0:
        for a, b in mask_runs(~out):
            if 0 < a and b < len(out) and b - a < min_off:
                out[a:b] = True
    if min_on > 0:
        for a, b in mask_runs(out):
            if b - a < min_on:
                out[a:b] = False
    return out


def activity_turns(
    chosen: np.ndarray, stream: str, params: DiarizeParams
) -> list[SpeakerTurn]:
    """Speaker turns from a boolean ``(frames, clusters)`` activity matrix."""
    min_on = int(round(params.min_on_s / FRAME_S))
    min_off = int(round(params.min_off_s / FRAME_S))
    turns = []
    for c in range(chosen.shape[1]):
        for a, b in mask_runs(smooth_activity(chosen[:, c], min_on, min_off)):
            turns.append(
                SpeakerTurn(
                    float(frame_start_sample(a)) / SAMPLE_RATE,
                    float(frame_start_sample(b)) / SAMPLE_RATE,
                    c,
                    stream,
                )
            )
    return sorted(turns, key=lambda t: (t.t_start, t.cluster))


def name_clusters(
    turns: Sequence[SpeakerTurn], centroids: np.ndarray, matcher: ProfileMatcher | None
) -> tuple[list[str], dict[str, str]]:
    """Speaker ids per cluster: enrolled people (``P:<id>``), others ``S1``, ``S2``, ... by
    first appearance. Two clusters that match the same person share the id (that merges an
    over-split speaker)."""
    first: dict[int, float] = {}
    for t in turns:
        first[t.cluster] = min(first.get(t.cluster, np.inf), t.t_start)
    ids: dict[int, str] = {}
    profile_names: dict[str, str] = {}
    if matcher is not None:
        for c in range(len(centroids)):
            match = matcher.match(centroids[c])
            if match.accepted and match.profile is not None:
                ids[c] = PROFILE_PREFIX + match.profile.id
                profile_names[match.profile.id] = match.profile.name
    n = 1
    for c in sorted(range(len(centroids)), key=lambda c: first.get(c, np.inf)):
        if c not in ids:
            ids[c] = f"S{n}"
            n += 1
    return [ids[c] for c in range(len(centroids))], profile_names


def resolve(
    stats: Sequence[StreamStats],
    params: DiarizeParams,
    cfg: SpeakersConfig,
    *,
    num_speakers: int | None = None,
    matcher: ProfileMatcher | None = None,
    constrain: bool = True,
) -> DiarizationResult:
    """Cluster, link, stitch and name (see the module docstring)."""
    points = [e for st in stats for e in st.embs if e.clean and e.dur >= params.min_clean_s]
    if not points:  # nothing clean (a very short recording): use whatever was embedded
        points = [e for st in stats for e in st.embs]
    if not points:
        return DiarizationResult([], [], {}, np.zeros((0, 0), dtype=np.float32), 0)
    emb = np.vstack([e.vec for e in points])
    # Every speech frame is seen by ~589/step_frames windows, so each embedding has that many
    # near-duplicates; the p-pruned graph needs neighbours beyond them (2x) or it breaks
    # into chains of consecutive windows and the eigengap counts far too many speakers.
    overlap = NUM_FRAMES / max(stats[0].step_frames, 1)
    # Seconds of distinct speech behind each embedding: its audio length / the number of windows
    # that see the same speech.
    seconds = np.array([e.dur for e in points]) / max(overlap, 1.0)
    labels = cluster_embeddings(
        emb,
        num_speakers=num_speakers,
        max_speakers=cfg.refine_max_speakers,
        pval=cfg.refine_pval,
        min_pnum=max(cfg.refine_min_pnum, int(round(2 * overlap))),
        min_spectral=cfg.refine_min_spectral,
        ahc_threshold=cfg.refine_threshold,
        minor_size=params.minor_points,
        weights=seconds,
        split_max_cos=params.split_cos,
        split_min_weight=params.split_min_s,
    )
    n_clusters = int(labels.max()) + 1
    centroids = np.vstack([l2_normalize(emb[labels == c].mean(axis=0)) for c in range(n_clusters)])
    turns: list[SpeakerTurn] = []
    for st in stats:
        assignment = link_local_speakers(st, centroids, params, constrain)
        chosen = stitch_stream(st, assignment, n_clusters, params)
        turns.extend(activity_turns(chosen, st.stream, params))
    turns.sort(key=lambda t: (t.t_start, t.cluster))
    ids, profile_names = name_clusters(turns, centroids, matcher)
    return DiarizationResult(turns, ids, profile_names, centroids, len(points))


def diarize(
    streams: dict[str, np.ndarray],
    segmenter: PyannoteSegmenter,
    embedder: Embedder,
    cfg: SpeakersConfig,
    params: DiarizeParams | None = None,
    *,
    num_speakers: int | None = None,
    matcher: ProfileMatcher | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> DiarizationResult:
    """Extract and resolve in one go (``streams``: name -> 16 kHz mono float32 audio)."""
    params = params or DiarizeParams()
    stats = [
        extract_stream(name, audio, segmenter, embedder, params, progress)
        for name, audio in streams.items()
    ]
    return resolve(stats, params, cfg, num_speakers=num_speakers, matcher=matcher)
