"""Diarization error rate (DER) in pure numpy, plus RTTM helpers.

Inputs are lists of ``(start, end, label)`` turns in seconds. The metric follows the
NIST / ``md-eval`` definition:

    DER = (missed + false alarm + confusion) / total reference speaker time

Scoring is frame based (default 10 ms). Reference and hypothesis labels live in
different namespaces, so the hypothesis speakers are mapped to reference speakers
with the mapping that maximises the overlap (Hungarian algorithm, implemented
below; no scipy needed). Frames within ``collar`` seconds of a reference speaker
boundary (each side) are not scored, and frames where two or more reference
speakers talk can optionally be skipped as well.

Per frame, with ``R`` active reference speakers, ``H`` active hypothesis speakers and
``C`` of them correct under the mapping: missed = max(0, R - H), false alarm =
max(0, H - R), confusion = min(R, H) - C. Overlapping speech is therefore scored
properly (a hypothesis that finds only one of two talkers has one miss).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

Turn = tuple[float, float, str]

FRAME_S = 0.01
DEFAULT_COLLAR_S = 0.25


# --------------------------------------------------------------------------- RTTM


def read_rttm(path: str | Path) -> dict[str, list[Turn]]:
    """Parse an RTTM file into ``{file id: [(start, end, speaker), ...]}``.

    Only ``SPEAKER`` lines are used; fields are whitespace separated:
    ``SPEAKER <file> <chan> <start> <duration> <NA> <NA> <speaker> <NA> <NA>``.
    """
    out: dict[str, list[Turn]] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        parts = raw.split()
        if len(parts) < 8 or parts[0] != "SPEAKER":
            continue
        start, dur = float(parts[3]), float(parts[4])
        if dur <= 0:
            continue
        out.setdefault(parts[1], []).append((start, start + dur, parts[7]))
    for turns in out.values():
        turns.sort()
    return out


def write_rttm(path: str | Path, turns: Iterable[Turn], file_id: str = "file") -> None:
    """Write turns as RTTM (channel 1)."""
    lines = [
        f"SPEAKER {file_id} 1 {start:.3f} {end - start:.3f} <NA> <NA> {label} <NA> <NA>"
        for start, end, label in sorted(turns)
        if end > start
    ]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def crop_turns(turns: Iterable[Turn], start: float, end: float) -> list[Turn]:
    """Clip turns to the window ``[start, end]`` (turns outside are dropped)."""
    out: list[Turn] = []
    for s, e, label in turns:
        s2, e2 = max(s, start), min(e, end)
        if e2 > s2:
            out.append((s2, e2, label))
    return out


# --------------------------------------------------------------------------- assignment


def hungarian_max(weights: np.ndarray) -> list[tuple[int, int]]:
    """Assignment maximising the summed weight for a rectangular matrix.

    Returns ``(row, col)`` pairs, ``min(rows, cols)`` of them. Classic O(n^3)
    shortest-augmenting-path algorithm with potentials.
    """
    w = np.asarray(weights, dtype=np.float64)
    if w.size == 0:
        return []
    transposed = w.shape[0] > w.shape[1]
    if transposed:
        w = w.T
    cost = -w  # minimise
    n, m = cost.shape  # n <= m
    inf = float("inf")
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    match = np.zeros(m + 1, dtype=int)  # match[j] = row (1-based) assigned to column j
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        match[0] = i
        j0 = 0
        minv = np.full(m + 1, inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = match[j0]
            delta, j1 = inf, 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if match[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1
    pairs = [(int(match[j]) - 1, j - 1) for j in range(1, m + 1) if match[j] != 0]
    if transposed:
        pairs = [(c, r) for r, c in pairs]
    return sorted(pairs)


# --------------------------------------------------------------------------- DER


@dataclass(frozen=True)
class DerResult:
    """Rates are fractions of ``ref_speech_s`` (multiply by 100 for percent)."""

    der: float
    miss: float
    false_alarm: float
    confusion: float
    ref_speech_s: float  # total scored reference speaker time (overlap counts twice)
    miss_s: float
    false_alarm_s: float
    confusion_s: float
    scored_s: float  # duration of the scored region (after collar / overlap removal)
    n_ref: int
    n_hyp: int
    mapping: dict[str, str]  # hypothesis label -> reference label

    def as_dict(self) -> dict:
        return asdict(self)


def _masks(
    turns: Sequence[Turn], n_frames: int, frame_s: float
) -> tuple[list[str], np.ndarray]:
    """Boolean frame activity per label: ``(labels, array[len(labels), n_frames])``."""
    labels = sorted({t[2] for t in turns if t[1] > t[0]})
    index = {label: i for i, label in enumerate(labels)}
    mask = np.zeros((len(labels), n_frames), dtype=bool)
    for start, end, label in turns:
        a = max(0, int(round(start / frame_s)))
        b = min(n_frames, int(round(end / frame_s)))
        if b > a:
            mask[index[label], a:b] = True
    return labels, mask


def compute_der(
    ref: Sequence[Turn],
    hyp: Sequence[Turn],
    *,
    collar: float = DEFAULT_COLLAR_S,
    skip_overlap: bool = False,
    uem: tuple[float, float] | None = None,
    frame_s: float = FRAME_S,
) -> DerResult:
    """Diarization error rate of ``hyp`` against ``ref``.

    ``collar`` is excluded on *each* side of every reference speaker boundary (the
    md-eval convention; pyannote's ``collar`` is the total width, so 0.25 here equals
    0.5 there). ``uem`` = ``(start, end)`` limits scoring to that window; the default
    spans both inputs. Raises ``ValueError`` when the reference has no speech.
    """
    ref = [t for t in ref if t[1] > t[0]]
    hyp = [t for t in hyp if t[1] > t[0]]
    if not ref:
        raise ValueError("reference has no speech: DER is undefined")
    if uem is None:
        uem = (0.0, max(max(t[1] for t in ref), max((t[1] for t in hyp), default=0.0)))
    n_frames = int(round(uem[1] / frame_s))
    ref_labels, ref_m = _masks(ref, n_frames, frame_s)
    hyp_labels, hyp_m = _masks(hyp, n_frames, frame_s)

    scored = np.ones(n_frames, dtype=bool)
    scored[: max(0, int(round(uem[0] / frame_s)))] = False
    if collar > 0:
        half = int(round(collar / frame_s))
        for row in ref_m:  # transitions of each speaker (merged turns, no inner gaps lost)
            edges = np.flatnonzero(np.diff(row.astype(np.int8)) != 0) + 1
            if row[0]:
                edges = np.concatenate([[0], edges])
            if row[-1]:
                edges = np.concatenate([edges, [n_frames]])
            for e in edges:
                scored[max(0, e - half) : min(n_frames, e + half)] = False
    n_ref_active = ref_m.sum(axis=0)
    if skip_overlap:
        scored &= n_ref_active < 2

    ref_s = ref_m & scored
    hyp_s = hyp_m & scored

    mapping: dict[str, str] = {}
    correct = np.zeros(n_frames, dtype=np.int64)
    if ref_labels and hyp_labels:
        overlap = ref_s.astype(np.float32) @ hyp_s.astype(np.float32).T  # (R, H) frame counts
        for r, h in hungarian_max(overlap):
            if overlap[r, h] <= 0:
                continue
            mapping[hyp_labels[h]] = ref_labels[r]
            correct += ref_s[r] & hyp_s[h]

    n_r = ref_s.sum(axis=0)
    n_h = hyp_s.sum(axis=0)
    miss = np.maximum(0, n_r - n_h).sum()
    fa = np.maximum(0, n_h - n_r).sum()
    conf = (np.minimum(n_r, n_h) - correct).sum()
    total = n_r.sum()
    to_s = frame_s
    if total == 0:
        raise ValueError("nothing left to score (reference empty after collar / overlap removal)")
    return DerResult(
        der=float(miss + fa + conf) / float(total),
        miss=float(miss) / float(total),
        false_alarm=float(fa) / float(total),
        confusion=float(conf) / float(total),
        ref_speech_s=float(total) * to_s,
        miss_s=float(miss) * to_s,
        false_alarm_s=float(fa) * to_s,
        confusion_s=float(conf) * to_s,
        scored_s=float(scored.sum()) * to_s,
        n_ref=len(ref_labels),
        n_hyp=len(hyp_labels),
        mapping=mapping,
    )
