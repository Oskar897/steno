"""Voice-activity based segmentation of a continuous 16 kHz mono stream.

``SpeechSegmenter`` is pure logic around a ``SpeechProbModel`` (anything that maps
512-sample frames to speech probabilities), so it is testable with synthetic
models. ``SileroVad`` is the production model: the Silero VAD ONNX file that ships
inside faster-whisper, run with onnxruntime (no torch).

An utterance is emitted when speech has been followed by ``min_silence_ms`` of
silence, or when it reaches ``max_segment_s`` (then it is cut at the quietest
frame near the end so words are rarely split).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

FRAME = 512  # Silero frame size at 16 kHz (32 ms)
CONTEXT = 64  # samples of left context the ONNX model expects per frame
SAMPLE_RATE = 16000


class SpeechProbModel(Protocol):
    def reset(self) -> None: ...

    def __call__(self, frames: np.ndarray) -> np.ndarray:
        """``frames``: float32 (n, 512) consecutive frames -> speech probabilities (n,)."""
        ...


@dataclass(frozen=True)
class VadParams:
    threshold: float = 0.5
    neg_threshold: float | None = None  # default: threshold - 0.15
    min_silence_ms: int = 500
    min_speech_ms: int = 250
    max_segment_s: float = 15.0
    preroll_ms: int = 200  # audio kept before detected speech onset
    tail_ms: int = 160  # trailing silence kept after speech

    @property
    def neg(self) -> float:
        return self.neg_threshold if self.neg_threshold is not None else max(
            self.threshold - 0.15, 0.01
        )


@dataclass(frozen=True)
class Utterance:
    audio: np.ndarray  # float32 mono
    t_start: float  # seconds since the stream's first sample
    t_end: float

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


class SpeechSegmenter:
    """Feeds audio in, yields ``Utterance`` objects. One instance per stream."""

    def __init__(
        self,
        model: SpeechProbModel,
        params: VadParams | None = None,
        sample_rate: int = SAMPLE_RATE,
    ):
        if sample_rate != SAMPLE_RATE:
            raise ValueError("SpeechSegmenter works at 16 kHz only")
        self.model = model
        self.params = params or VadParams()
        self._ms_frames = lambda ms: max(1, round(ms / 1000 * SAMPLE_RATE / FRAME))
        self._silence_frames = self._ms_frames(self.params.min_silence_ms)
        self._min_speech_frames = self._ms_frames(self.params.min_speech_ms)
        self._tail_frames = self._ms_frames(self.params.tail_ms)
        self._max_frames = max(2, int(self.params.max_segment_s * SAMPLE_RATE / FRAME))
        self._reset_state()
        self._pos = 0  # frames consumed so far, across the whole stream

    # -- state ----------------------------------------------------------------

    def _reset_state(self) -> None:
        self._carry = np.zeros(0, dtype=np.float32)
        self._preroll: deque[np.ndarray] = deque(maxlen=self._ms_frames(self.params.preroll_ms))
        self._in_speech = False
        self._buf: list[np.ndarray] = []
        self._probs: list[float] = []
        self._buf_start = 0  # frame index of _buf[0]
        self._silence = 0  # trailing frames below the negative threshold

    @property
    def position_s(self) -> float:
        """Stream time consumed by complete frames (the watermark)."""
        return self._pos * FRAME / SAMPLE_RATE

    @property
    def open_start_s(self) -> float | None:
        """Start time of the utterance in progress, if any."""
        return self._buf_start * FRAME / SAMPLE_RATE if self._in_speech else None

    def open_rms(self) -> float:
        return rms(np.concatenate(self._buf)) if self._in_speech and self._buf else 0.0

    # -- processing -------------------------------------------------------------

    def feed(self, samples: np.ndarray) -> list[Utterance]:
        data = np.concatenate([self._carry, np.asarray(samples, dtype=np.float32)])
        n = len(data) // FRAME
        self._carry = data[n * FRAME :].copy()
        if n == 0:
            return []
        frames = data[: n * FRAME].reshape(n, FRAME)
        probs = np.asarray(self.model(frames), dtype=np.float32)
        out: list[Utterance] = []
        for frame, p in zip(frames, probs, strict=True):
            self._step(frame.copy(), float(p), out)
            self._pos += 1
        return out

    def flush(self) -> list[Utterance]:
        """End of stream: emit any open utterance and reset model state."""
        out: list[Utterance] = []
        if self._in_speech:
            self._emit(len(self._buf), out, trim_silence=True)
        self.model.reset()
        self._reset_state()
        return out

    def _step(self, frame: np.ndarray, p: float, out: list[Utterance]) -> None:
        prm = self.params
        if not self._in_speech:
            self._preroll.append(frame)
            if p >= prm.threshold:
                self._in_speech = True
                self._buf = list(self._preroll)
                self._probs = [0.0] * (len(self._buf) - 1) + [p]
                self._buf_start = self._pos - len(self._buf) + 1
                self._preroll.clear()
                self._silence = 0
            return

        self._buf.append(frame)
        self._probs.append(p)
        self._silence = self._silence + 1 if p < prm.neg else 0

        if self._silence >= self._silence_frames:
            self._emit(len(self._buf), out, trim_silence=True)
            self._finish_utterance()
        elif len(self._buf) >= self._max_frames:
            self._cut_at_quiet_point(out)

    def _finish_utterance(self) -> None:
        self._in_speech = False
        self._buf = []
        self._probs = []
        self._silence = 0
        self._preroll.clear()

    def _cut_at_quiet_point(self, out: list[Utterance]) -> None:
        n = len(self._buf)
        window = max(1, n * 3 // 10)
        tail = self._probs[n - window :]
        cut = n - window + int(np.argmin(tail)) + 1  # frames [0, cut) are emitted
        cut = max(1, cut)
        self._emit(cut, out, trim_silence=False)
        self._buf = self._buf[cut:]
        self._probs = self._probs[cut:]
        self._buf_start += cut
        self._silence = 0
        for p in reversed(self._probs):
            if p >= self.params.neg:
                break
            self._silence += 1

    def _emit(self, count: int, out: list[Utterance], *, trim_silence: bool) -> None:
        frames = self._buf[:count]
        probs = self._probs[:count]
        if trim_silence:
            trailing = 0
            for p in reversed(probs):
                if p >= self.params.neg:
                    break
                trailing += 1
            drop = max(0, trailing - self._tail_frames)
            if drop:
                frames = frames[: len(frames) - drop]
                probs = probs[: len(probs) - drop]
        voiced = sum(1 for p in probs if p >= self.params.threshold)
        if voiced < self._min_speech_frames or not frames:
            return
        audio = np.concatenate(frames)
        t0 = self._buf_start * FRAME / SAMPLE_RATE
        out.append(Utterance(audio=audio, t_start=t0, t_end=t0 + len(audio) / SAMPLE_RATE))


# ------------------------------------------------------------------------ Silero model


def silero_model_path() -> Path:
    """Locate the Silero VAD ONNX file bundled with faster-whisper."""
    try:
        from faster_whisper.vad import get_assets_path
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("Silero VAD needs the 'asr' extra (faster-whisper)") from exc
    assets = Path(get_assets_path())
    candidates = sorted(assets.glob("silero_vad*.onnx"))
    if not candidates:
        raise RuntimeError("Silero VAD model not found in faster-whisper assets")
    return candidates[-1]


class SileroVad:
    """Stateful streaming Silero VAD (ONNX, onnxruntime, single thread)."""

    def __init__(self, model_path: Path | None = None):
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            str(model_path or silero_model_path()),
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, frames: np.ndarray) -> np.ndarray:
        n = frames.shape[0]
        # Row k = last CONTEXT samples of the previous frame + frame k.
        prev_tails = np.empty((n, CONTEXT), dtype=np.float32)
        prev_tails[0] = self._ctx
        if n > 1:
            prev_tails[1:] = frames[:-1, -CONTEXT:]
        self._ctx = frames[-1, -CONTEXT:].copy()
        batch = np.concatenate([prev_tails, frames], axis=1).astype(np.float32, copy=False)
        probs, self._h, self._c = self._session.run(
            None, {"input": batch, "h": self._h, "c": self._c}
        )
        return np.asarray(probs, dtype=np.float32).reshape(-1)
