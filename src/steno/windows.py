"""Sub-segment speaker turns: sliding windows, label smoothing, turn cutting, word assignment.

A VAD segment can hold several people (a short "mm-hm" inside someone else's long
turn), so one embedding per segment hides the second voice. Following 3D-Speaker,
the segment is cut into overlapping windows (default 1.5 s every 0.75 s); each window
gets its own speaker label, the label sequence is median-filtered, and the segment is
cut into turns wherever the label changes. ASR words (with timestamps) are then
assigned to the turn they overlap most (the WhisperX rule).

Everything here is pure numpy/python, with no models, so it is tested with synthetic
labels and embeddings. Times are seconds relative to the start of the segment unless
stated otherwise.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Turn:
    t_start: float
    t_end: float
    label: str  # any hashable speaker key chosen by the caller

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


# --------------------------------------------------------------------------- windows


def window_spans(
    duration: float, win: float = 1.5, hop: float = 0.75, min_win: float = 1.0
) -> list[tuple[float, float]]:
    """Windows of ``win`` s every ``hop`` s covering ``[0, duration]``.

    The last window is moved back so that it ends exactly at ``duration`` (the tail is
    never dropped). A segment shorter than ``win`` yields one window spanning all of it,
    or none when it is shorter than ``min_win`` (too short to embed reliably).
    """
    if duration < min_win:
        return []
    if duration <= win + 1e-9:
        return [(0.0, duration)]
    spans = []
    start = 0.0
    while start + win <= duration + 1e-9:
        spans.append((start, start + win))
        start += hop
    if spans[-1][1] < duration - 0.25 * hop:
        spans.append((duration - win, duration))
    return spans


def window_centers(spans: Sequence[tuple[float, float]]) -> list[float]:
    return [(a + b) / 2 for a, b in spans]


# --------------------------------------------------------------------------- smoothing


def median_filter_labels(labels: Sequence[str], size: int = 3) -> list[str]:
    """Majority label in a sliding window of ``size`` (odd); ties keep the centre label.

    Removes isolated single-window flips. ``size <= 1`` is a no-op.
    """
    n = len(labels)
    if size <= 1 or n < 3:
        return list(labels)
    half = size // 2
    out: list[str] = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        counts = Counter(labels[lo:hi])
        top = max(counts.values())
        if counts[labels[i]] == top:
            out.append(labels[i])
            continue
        # tie between others: nearest to the centre wins
        best = min(
            (j for j in range(lo, hi) if counts[labels[j]] == top), key=lambda j: abs(j - i)
        )
        out.append(labels[best])
    return out


# --------------------------------------------------------------------------- turns


def cut_turns(
    spans: Sequence[tuple[float, float]],
    labels: Sequence[str],
    duration: float,
    min_turn_s: float = 0.0,
) -> list[Turn]:
    """Turns covering ``[0, duration]`` from per-window labels.

    The boundary between two windows with different labels lies halfway between their
    centres. Adjacent equal labels merge. A turn shorter than ``min_turn_s`` is absorbed
    by the longer neighbour (never the only turn).
    """
    if not spans:
        return []
    centers = window_centers(spans)
    cuts = [0.0] + [(centers[i] + centers[i + 1]) / 2 for i in range(len(spans) - 1)] + [duration]
    turns: list[Turn] = []
    for i, label in enumerate(labels):
        a, b = cuts[i], cuts[i + 1]
        if turns and turns[-1].label == label:
            turns[-1] = Turn(turns[-1].t_start, b, label)
        elif b > a:
            turns.append(Turn(a, b, label))
    return absorb_short_turns(turns, min_turn_s)


def absorb_short_turns(turns: list[Turn], min_turn_s: float) -> list[Turn]:
    """Merge turns shorter than ``min_turn_s`` into their longer neighbour, shortest first."""
    turns = list(turns)
    while len(turns) > 1:
        short = [i for i, t in enumerate(turns) if t.duration < min_turn_s]
        if not short:
            break
        i = min(short, key=lambda k: turns[k].duration)
        left = turns[i - 1] if i > 0 else None
        right = turns[i + 1] if i + 1 < len(turns) else None
        if right is None or (left is not None and left.duration >= right.duration):
            label = left.label  # type: ignore[union-attr]
        else:
            label = right.label
        turns[i] = Turn(turns[i].t_start, turns[i].t_end, label)
        merged: list[Turn] = []
        for t in turns:
            if merged and merged[-1].label == t.label:
                merged[-1] = Turn(merged[-1].t_start, t.t_end, t.label)
            else:
                merged.append(t)
        turns = merged
    return turns


def snap_boundaries(
    turns: list[Turn], audio: np.ndarray, radius_s: float = 0.4, frame_s: float = 0.05
) -> list[Turn]:
    """Move each turn boundary to the quietest ``frame_s`` frame within ``radius_s``.

    Window centres give boundaries only to about +-0.4 s; people mostly change at a
    pause or a dip in energy, so the cut is snapped there. Boundaries never cross a
    neighbouring boundary (kept at least ``frame_s`` apart).
    """
    if len(turns) < 2 or audio.size == 0:
        return turns
    frame = max(1, int(frame_s * SAMPLE_RATE))
    n = len(audio) // frame
    if n < 2:
        return turns
    energy = np.sqrt(np.mean(np.square(audio[: n * frame], dtype=np.float64).reshape(n, frame), 1))
    bounds = [t.t_end for t in turns[:-1]]
    snapped: list[float] = []
    lo_limit = 0.0
    for k, b in enumerate(bounds):
        hi_limit = bounds[k + 1] if k + 1 < len(bounds) else turns[-1].t_end
        lo = max(lo_limit + frame_s, b - radius_s)
        hi = min(hi_limit - frame_s, b + radius_s)
        if hi <= lo:
            snapped.append(b)
            lo_limit = b
            continue
        i0, i1 = int(lo / frame_s), max(int(lo / frame_s) + 1, int(hi / frame_s))
        i1 = min(i1, n)
        if i0 >= i1:
            snapped.append(b)
            lo_limit = b
            continue
        pick = i0 + int(np.argmin(energy[i0:i1]))
        new_b = (pick + 0.5) * frame_s
        snapped.append(new_b)
        lo_limit = new_b
    out = []
    start = turns[0].t_start
    for t, end in zip(turns, [*snapped, turns[-1].t_end], strict=True):
        out.append(Turn(start, end, t.label))
        start = end
    return out


# --------------------------------------------------------------------------- words


def assign_words(words: Sequence[Word], turns: Sequence[Turn]) -> list[list[Word]]:
    """Assign each word to the turn with the largest time overlap (nearest turn if none).

    Returns one list of words per turn (same order as ``turns``), words in time order.
    """
    out: list[list[Word]] = [[] for _ in turns]
    if not turns:
        return out
    for w in words:
        best, best_ov = -1, 0.0
        for i, t in enumerate(turns):
            ov = min(w.end, t.t_end) - max(w.start, t.t_start)
            if ov > best_ov:
                best, best_ov = i, ov
        if best < 0:  # zero-length word or outside every turn: nearest turn by distance
            mid = (w.start + w.end) / 2
            best = min(
                range(len(turns)),
                key=lambda i: 0.0
                if turns[i].t_start <= mid <= turns[i].t_end
                else min(abs(mid - turns[i].t_start), abs(mid - turns[i].t_end)),
            )
        out[best].append(w)
    return out


def join_words(words: Sequence[Word]) -> str:
    return " ".join(w.text for w in words if w.text).strip()


def split_by_turns(
    text: str, words: Sequence[Word] | None, turns: Sequence[Turn]
) -> list[tuple[int, str]]:
    """Distribute a segment's text over its turns: ``[(turn index, text), ...]`` in time order.

    One turn, or no word timestamps: all text goes to the longest turn. Otherwise every
    word follows ``assign_words``; turns that received no word produce no entry.
    """
    if not turns:
        return []
    if len(turns) == 1 or not words:
        longest = max(range(len(turns)), key=lambda i: turns[i].duration)
        return [(longest, text)]
    parts = assign_words(words, turns)
    return [(i, join_words(ws)) for i, ws in enumerate(parts) if ws and join_words(ws)]
