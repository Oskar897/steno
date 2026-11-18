"""Incremental pyannote diarization while recording, so that stopping does not pay for it.

``steno.diarize`` (``steno finalize --thorough``) slides the pyannote segmentation model over
the whole kept audio after the meeting: accurate (AMI far-field DER 18 % against 31 % for the
live labels) but about 0.1 x the audio length. The expensive part does not depend on the
clustering, only on the audio, so it can run *during* the meeting in the refine worker:

* ``step`` runs the segmentation model over every 10 s window (a new one every ``step_s``)
  as soon as its audio, and the live lines that tell where speech is, are in. Windows
  without any live speech are skipped (the live VAD saw nothing there): in a meeting where
  the other side is quiet most of the time that is most of the audio.
* each window's local speakers get their CAM++ embedding exactly as ``extract_stream`` does
  (``steno.diarize.window_embeddings``), so ``steno.diarize.resolve`` (clustering, linking,
  stitching, naming) works on the result unchanged.
* ``finish`` (at stop) only processes what is left: the last, zero-padded windows.
* ``save`` writes ``diar.bin`` (encrypted, it holds voiceprints) next to the session so that
  ``steno finalize`` can resolve it later without audio: seconds, whatever the meeting length.

``read``-style access to the audio is by stream sample index (the same index the kept FLAC
uses), through the rings of ``steno.refine``. Models are injected, so tests use fakes.
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from steno.diarize import DiarizeParams, LocalEmb, StreamStats, window_embeddings
from steno.segmentation import (
    FRAME_SHIFT,
    NUM_FRAMES,
    POWERSET,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    powerset_hard,
    powerset_marginals,
    step_frames_for,
    total_frames,
    window_count,
)

log = logging.getLogger(__name__)

DIAR_NAME = "diar.bin"
MARGIN_S = 6.0  # a window is processed this long after its end (its live lines are in by then)
SILENT = np.zeros(len(POWERSET), dtype=np.float32)
SILENT[0] = 1.0  # powerset class 0 = nobody speaks


class AudioReader(Protocol):
    """What the diarizer needs of ``steno.refine.AudioRing``."""

    origin: float

    @property
    def n_samples(self) -> int: ...

    def read(self, first: int, last: int) -> np.ndarray | None: ...


class WindowSegmenter(Protocol):
    def load(self) -> None: ...

    def run_windows(self, windows: np.ndarray) -> np.ndarray:
        """``(n, 160000)`` audio -> ``(n, 589, 7)`` powerset probabilities."""
        ...


def build_stats(
    stream: str,
    n_samples: int,
    step_frames: int,
    probs: Mapping[int, np.ndarray],
    embs: list[LocalEmb],
) -> StreamStats:
    """Dense ``StreamStats`` from the windows that were processed (the others are silence)."""
    count = window_count(n_samples, step_frames)
    dense = np.broadcast_to(SILENT, (count, NUM_FRAMES, len(POWERSET))).copy()
    for k, p in probs.items():
        if k < count:
            dense[k] = p
    return StreamStats(
        stream, n_samples, step_frames, powerset_marginals(dense), powerset_hard(dense),
        list(embs), speech=(1.0 - dense[..., 0]).astype(np.float32),
    )


class _State:
    def __init__(self, origin: float):
        self.origin = origin
        self.next_k = 0
        self.probs: dict[int, np.ndarray] = {}
        self.embs: list[LocalEmb] = []
        self.n_samples = 0


class LiveDiarizer:
    """Runs segmentation + local-speaker embedding window by window (see the module doc)."""

    def __init__(
        self,
        segmenter: WindowSegmenter,
        embedder: Any,
        params: DiarizeParams | None = None,
        *,
        margin_s: float = MARGIN_S,
    ):
        self.segmenter = segmenter
        self.embedder = embedder
        self.params = params or DiarizeParams()
        self.step_frames = step_frames_for(self.params.step_s)
        self._step = self.step_frames * FRAME_SHIFT  # samples between windows
        self._margin = int(margin_s * SAMPLE_RATE)
        self._speech: dict[str, list[tuple[float, float]]] = {}
        self._state: dict[str, _State] = {}
        self.windows = 0  # windows run through the models
        self.skipped = 0  # windows without live speech
        self.compute_s = 0.0

    def load(self) -> None:
        self.segmenter.load()
        self.embedder.load()

    # -- input ------------------------------------------------------------------------

    def note_line(self, stream: str, t_start: float, t_end: float) -> None:
        """A live line: speech on ``stream`` between these stream times."""
        self._speech.setdefault(stream, []).append((t_start, t_end))

    def _has_speech(self, stream: str, t0: float, t1: float) -> bool:
        spans = self._speech.get(stream, [])
        # lines arrive in time order: look from the end, stop once they are too old
        for a, b in reversed(spans):
            if b < t0 - 30.0:
                return False
            if a < t1 and b > t0:
                return True
        return False

    # -- work -------------------------------------------------------------------------

    def step(self, rings: Mapping[str, AudioReader], limit: int = 4) -> int:
        """Process up to ``limit`` ready windows; returns how many were run or skipped."""
        done = 0
        for name, ring in rings.items():
            st = self._state.setdefault(name, _State(ring.origin))
            n = ring.n_samples
            while done < limit and st.next_k * self._step + WINDOW_SAMPLES + self._margin <= n:
                self._window(name, ring, st, st.next_k, n)
                st.next_k += 1
                done += 1
        return done

    def finish(self, rings: Mapping[str, AudioReader]) -> None:
        """Process everything that is left, including the zero-padded last windows."""
        for name, ring in rings.items():
            st = self._state.setdefault(name, _State(ring.origin))
            n = ring.n_samples
            st.n_samples = n
            for k in range(st.next_k, window_count(n, self.step_frames)):
                self._window(name, ring, st, k, n)
            st.next_k = window_count(n, self.step_frames)

    def _window(self, name: str, ring: AudioReader, st: _State, k: int, n: int) -> None:
        first = k * self._step
        t0 = ring.origin + first / SAMPLE_RATE
        if not self._has_speech(name, t0, t0 + WINDOW_SAMPLES / SAMPLE_RATE):
            self.skipped += 1
            return
        audio = ring.read(first, min(first + WINDOW_SAMPLES, n))
        if audio is None or not len(audio):  # trimmed from the buffer: treated as silence
            self.skipped += 1
            return
        started = time.perf_counter()
        window = np.zeros(WINDOW_SAMPLES, dtype=np.float32)
        window[: len(audio)] = audio
        probs = np.asarray(self.segmenter.run_windows(window[None])[0], dtype=np.float32)
        st.probs[k] = probs
        valid = min(NUM_FRAMES, total_frames(n) - k * self.step_frames)
        if valid > 0:
            st.embs.extend(window_embeddings(
                k, powerset_hard(probs), window, 0, valid, self.params, self.embedder))
        self.windows += 1
        self.compute_s += time.perf_counter() - started

    # -- result -----------------------------------------------------------------------

    def stats(self) -> list[StreamStats]:
        return [
            build_stats(name, st.n_samples, self.step_frames, st.probs, st.embs)
            for name, st in self._state.items() if st.n_samples
        ]

    def save(self, session_dir: Path) -> bool:
        """Write ``diar.bin`` (encrypted); False when there is nothing to save."""
        stats = self.stats()
        return save_stats(session_dir, self.step_frames, {
            s.stream: (s, self._state[s.stream].probs) for s in stats
        }) if stats else False


# --------------------------------------------------------------------------- persistence


def save_stats(
    session_dir: Path, step_frames: int,
    streams: Mapping[str, tuple[StreamStats, Mapping[int, np.ndarray]]],
) -> bool:
    from steno.secure import write_protected

    arrays: dict[str, np.ndarray] = {}
    meta: dict[str, Any] = {"v": 1, "step_frames": step_frames, "streams": {}}
    for i, (name, (stats, probs)) in enumerate(streams.items()):
        ks = sorted(probs)
        meta["streams"][str(i)] = {"name": name, "n_samples": int(stats.n_samples)}
        arrays[f"ks{i}"] = np.array(ks, dtype=np.int32)
        arrays[f"probs{i}"] = (
            np.stack([probs[k] for k in ks]).astype(np.float16) if ks
            else np.zeros((0, NUM_FRAMES, len(POWERSET)), dtype=np.float16)
        )
        e = stats.embs
        arrays[f"ek{i}"] = np.array([x.k for x in e], dtype=np.int32)
        arrays[f"es{i}"] = np.array([x.s for x in e], dtype=np.int8)
        arrays[f"edur{i}"] = np.array([x.dur for x in e], dtype=np.float32)
        arrays[f"eclean{i}"] = np.array([x.clean for x in e], dtype=bool)
        arrays[f"evec{i}"] = (
            np.vstack([x.vec for x in e]).astype(np.float32) if e
            else np.zeros((0, 0), dtype=np.float32)
        )
    buf = io.BytesIO()
    np.savez_compressed(buf, meta=np.array(json.dumps(meta)), **arrays)
    write_protected(Path(session_dir) / DIAR_NAME, buf.getvalue())
    return True


def load_stats(session_dir: Path) -> list[StreamStats] | None:
    """The ``StreamStats`` saved by ``LiveDiarizer.save`` (None without ``diar.bin``)."""
    from steno.secure import read_protected

    path = Path(session_dir) / DIAR_NAME
    if not path.is_file():
        return None
    with np.load(io.BytesIO(read_protected(path)), allow_pickle=False) as npz:
        meta = json.loads(str(npz["meta"]))
        step_frames = int(meta["step_frames"])
        out: list[StreamStats] = []
        for i, info in meta["streams"].items():
            ks, probs = npz[f"ks{i}"], npz[f"probs{i}"].astype(np.float32)
            embs = [
                LocalEmb(int(k), int(s), v, float(d), bool(c))
                for k, s, d, c, v in zip(
                    npz[f"ek{i}"], npz[f"es{i}"], npz[f"edur{i}"], npz[f"eclean{i}"],
                    npz[f"evec{i}"], strict=True,
                )
            ]
            out.append(build_stats(
                info["name"], int(info["n_samples"]), step_frames,
                {int(k): probs[j] for j, k in enumerate(ks)}, embs,
            ))
    return out
