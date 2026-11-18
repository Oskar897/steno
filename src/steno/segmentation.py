"""pyannote segmentation-3.0 (sherpa-onnx ONNX export), run directly with onnxruntime.

The model sees a 10 s window (160 000 samples at 16 kHz) and returns, for each of its 589
output frames, scores over the 7 *powerset* classes of at most two simultaneously active
"local" speakers out of three::

    class   0    1    2    3    4      5      6
    active  {}   {0}  {1}  {2}  {0,1}  {0,2}  {1,2}

Local speaker ids are only meaningful inside one window, so a long recording is covered by
overlapping windows (``step`` apart) that ``steno.diarize`` links with speaker embeddings.
This module holds the model wrapper and the pure numpy helpers around it: powerset
decoding, the frame grid and the weighted stitching of window outputs.

Frame grid: output frame ``i`` of a window covers ``[i * 270 + 360, i * 270 + 630)``
samples (the model's receptive field of 991 samples is centred on ``i * 270 + 495``), so
frames are 16.875 ms long and contiguous. A window ``k`` that starts ``k * step_frames``
frames into the recording lines up exactly with the global frame grid.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np

from steno import paths

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 160_000  # 10 s, the model's fixed chunk size
FRAME_SHIFT = 270  # samples between output frames (16.875 ms)
FRAME_SIZE = 991  # receptive field of one output frame, in samples
NUM_FRAMES = 589  # output frames per window
NUM_LOCAL = 3
FRAME_S = FRAME_SHIFT / SAMPLE_RATE
#: Local speakers that are active in each powerset class.
POWERSET: tuple[tuple[int, ...], ...] = ((), (0,), (1,), (2,), (0, 1), (0, 2), (1, 2))

SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
SEG_DIRNAME = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_FILES = {"model.onnx", "model.int8.onnx", "LICENSE", "README.md"}


class SegmentationError(RuntimeError):
    """The segmentation model is unavailable (missing package or download failed)."""


# --------------------------------------------------------------------------- powerset


def powerset_matrix() -> np.ndarray:
    """``(7, 3)`` 0/1 matrix: row = powerset class, column = local speaker."""
    mat = np.zeros((len(POWERSET), NUM_LOCAL), dtype=np.float32)
    for c, spk in enumerate(POWERSET):
        mat[c, list(spk)] = 1.0
    return mat


_POWERSET_MAT = powerset_matrix()


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return (e / e.sum(axis=axis, keepdims=True)).astype(np.float32)


def powerset_marginals(probs: np.ndarray) -> np.ndarray:
    """Per-speaker activity probability ``(..., 3)`` from powerset class probabilities.

    A local speaker is active with the summed probability of every class that contains it.
    """
    return np.asarray(probs, dtype=np.float32) @ _POWERSET_MAT


def powerset_hard(probs: np.ndarray) -> np.ndarray:
    """Boolean ``(..., 3)``: the speakers of the most likely powerset class."""
    return _POWERSET_MAT[np.argmax(probs, axis=-1)] > 0.5


# --------------------------------------------------------------------------- frame grid


def frame_start_sample(index: int | np.ndarray) -> int | np.ndarray:
    """First sample of global frame ``index``: its receptive field centre minus half a hop."""
    return index * FRAME_SHIFT + FRAME_SIZE // 2 - FRAME_SHIFT // 2


def frame_time(index: int | np.ndarray) -> float | np.ndarray:
    """Start time (s) of global frame ``index``."""
    return frame_start_sample(index) / SAMPLE_RATE


def total_frames(n_samples: int) -> int:
    """Number of global frames the model can emit for ``n_samples`` samples (at least 1)."""
    return max(1, (n_samples - FRAME_SIZE) // FRAME_SHIFT + 1)


def step_frames_for(step_s: float) -> int:
    """Window step in whole frames (so windows stay on the frame grid); at least 1 s."""
    frames = int(round(step_s * SAMPLE_RATE / FRAME_SHIFT))
    return int(np.clip(frames, int(SAMPLE_RATE / FRAME_SHIFT), NUM_FRAMES))


def window_count(n_samples: int, step_frames: int) -> int:
    """Windows needed to cover ``n_samples``; the last one is zero-padded."""
    if n_samples <= WINDOW_SAMPLES:
        return 1
    step = step_frames * FRAME_SHIFT
    return 1 + -(-(n_samples - WINDOW_SAMPLES) // step)


def window_weights(n: int = NUM_FRAMES) -> np.ndarray:
    """Hamming weights: frames near the window edge have less context and count less."""
    return np.hamming(n).astype(np.float32)


def aggregate_windows(
    values: np.ndarray, step_frames: int, n_frames: int, weights: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted sum of per-window ``values`` ``(K, F, D)`` on the global frame grid.

    Returns ``(num, den)`` with shapes ``(n_frames, D)`` and ``(n_frames, 1)``; the
    weighted mean is ``num / den``. Frames past ``n_frames`` (zero padding) are dropped.
    """
    k_win, f_len, dim = values.shape
    w = window_weights(f_len) if weights is None else weights
    num = np.zeros((n_frames, dim), dtype=np.float64)
    den = np.zeros((n_frames, 1), dtype=np.float64)
    for k in range(k_win):
        a = k * step_frames
        if a >= n_frames:
            break
        b = min(a + f_len, n_frames)
        n = b - a
        num[a:b] += values[k, :n] * w[:n, None]
        den[a:b] += w[:n, None]
    return num, den


# --------------------------------------------------------------------------- model files


def model_dir() -> Path:
    return paths.models_dir() / SEG_DIRNAME


def ensure_model(dest_dir: Path | None = None) -> Path:
    """Path of ``model.onnx`` in the models dir, downloaded (~6 MB) on first use.

    Members are extracted by basename: ``tarfile.extractall(filter="data")`` fails with
    OutsideDestinationError when the data dir sits under an MSIX-virtualised path.
    """
    dest = dest_dir or model_dir()
    model = dest / "model.onnx"
    if model.is_file() and model.stat().st_size > 0:
        return model
    dest.mkdir(parents=True, exist_ok=True)
    log.info("Downloading pyannote segmentation-3.0 model")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "seg.tar.bz2"
            req = urllib.request.Request(SEG_URL, headers={"User-Agent": "steno/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                archive.write_bytes(resp.read())
            with tarfile.open(archive, "r:bz2") as tar:
                for member in tar:
                    name = PurePosixPath(member.name).name
                    if not member.isfile() or name not in SEG_FILES:
                        continue
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    part = dest / (name + ".part")
                    with part.open("wb") as fh:
                        shutil.copyfileobj(src, fh)
                    os.replace(part, dest / name)
    except (OSError, tarfile.TarError) as exc:
        raise SegmentationError(
            f"Could not download the segmentation model ({exc}). Place model.onnx from "
            f"{SEG_DIRNAME} into {dest.parent.name}/{dest.name}/ manually."
        ) from exc
    if not model.is_file():
        raise SegmentationError("segmentation archive did not contain model.onnx")
    return model


# --------------------------------------------------------------------------- model wrapper


@dataclass(frozen=True)
class SegmentationResult:
    """Powerset class probabilities of every window of one recording."""

    probs: np.ndarray  # (K, 589, 7) float32, rows sum to 1
    step_frames: int
    n_samples: int

    @property
    def n_windows(self) -> int:
        return len(self.probs)

    @property
    def n_frames(self) -> int:
        return total_frames(self.n_samples)

    def window_start_sample(self, k: int) -> int:
        return k * self.step_frames * FRAME_SHIFT

    def soft(self) -> np.ndarray:
        return powerset_marginals(self.probs)

    def hard(self) -> np.ndarray:
        return powerset_hard(self.probs)

    def speech(self) -> np.ndarray:
        """``(K, 589)`` probability that at least one speaker is active."""
        return (1.0 - self.probs[..., 0]).astype(np.float32)


class PyannoteSegmenter:
    """onnxruntime session for pyannote segmentation-3.0 (CPU)."""

    def __init__(self, model: Path | None = None, num_threads: int = 4, batch_size: int = 8):
        self._path = model
        self._threads = num_threads
        self._batch = max(1, batch_size)
        self._session = None
        self._input = "x"

    def load(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise SegmentationError(
                "Finalize needs onnxruntime (installed with the 'asr' extra: "
                "uv sync --extra asr --extra speakers)"
            ) from exc
        path = self._path or ensure_model()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self._threads
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def run(
        self,
        audio: np.ndarray,
        step_frames: int,
        progress: Callable[[int, int], None] | None = None,
    ) -> SegmentationResult:
        """Slide the model over ``audio`` (16 kHz mono float32), one window every step."""
        self.load()
        assert self._session is not None
        samples = np.asarray(audio, dtype=np.float32)
        n = len(samples)
        count = window_count(n, step_frames)
        step = step_frames * FRAME_SHIFT
        out = np.zeros((count, NUM_FRAMES, len(POWERSET)), dtype=np.float32)
        for lo in range(0, count, self._batch):
            hi = min(count, lo + self._batch)
            batch = np.zeros((hi - lo, 1, WINDOW_SAMPLES), dtype=np.float32)
            for j, k in enumerate(range(lo, hi)):
                chunk = samples[k * step : k * step + WINDOW_SAMPLES]
                batch[j, 0, : len(chunk)] = chunk
            scores = self._session.run(None, {self._input: batch})[0]
            if scores.shape[1] != NUM_FRAMES or scores.shape[2] != len(POWERSET):
                raise SegmentationError(f"unexpected segmentation output shape {scores.shape}")
            out[lo:hi] = softmax(scores)
            if progress is not None:
                progress(hi, count)
        return SegmentationResult(out, step_frames, n)
