"""Speech recognition backends behind a minimal protocol.

The engine only needs ``transcribe(audio) -> (text, lang)``. A backend may also offer
``transcribe_words(audio) -> (text, lang, words)`` with per-word timestamps; the engine
uses it to split one segment's text between speakers when a segment holds several turns
(see ``steno.windows``). To add another backend (for example sherpa-onnx / Parakeet),
implement ``Transcriber``, register it in ``BACKENDS`` and select it with
``[asr] backend`` in the config.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from steno import paths
from steno.config import AsrConfig
from steno.windows import Word

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class AsrResult:
    """Text, language, word timestamps and confidence of one transcribed clip."""

    text: str
    lang: str
    words: list[Word]
    avg_logprob: float | None = None  # duration-weighted mean over the model's segments


@dataclass(frozen=True)
class LongSegment:
    """One model segment of a long-form transcription (times: seconds in the audio)."""

    start: float
    end: float
    text: str
    words: list[Word]
    avg_logprob: float | None = None


def weighted_logprob(items: list[tuple[float, float]]) -> float | None:
    """Mean of ``(avg_logprob, duration)`` pairs weighted by duration; None without items."""
    if not items:
        return None
    total = sum(max(d, 1e-3) for _, d in items)
    return sum(lp * max(d, 1e-3) for lp, d in items) / total


@runtime_checkable
class Transcriber(Protocol):
    """Anything that turns a 16 kHz mono float32 array into text."""

    #: Human-readable model identifier, stored in session.json.
    name: str

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        """Return ``(text, language_code)``. Empty text means nothing intelligible."""
        ...

    def load(self) -> None:
        """Load weights eagerly (optional to do work; called before capture starts)."""
        ...


class WhisperTranscriber:
    """faster-whisper (CTranslate2) on CPU."""

    def __init__(
        self,
        model: str,
        *,
        compute_type: str = "int8",
        cpu_threads: int = 6,
        language: str = "sv",
        beam_size: int = 1,
        initial_prompt: str | None = None,
    ):
        self.name = model
        self._model_id = model
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._language = None if language == "auto" else language
        self._beam_size = beam_size
        self._prompt = initial_prompt or None
        self._model = None
        self._pipeline = None

    def load(self) -> None:
        if self._model is not None:
            return
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        log.info("Loading ASR model %s", self._model_id)
        self._model = WhisperModel(
            self._model_id,
            device="cpu",
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
            download_root=str(paths.models_dir()),
        )
        self._pipeline = BatchedInferencePipeline(self._model)

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        self.load()
        assert self._model is not None
        segments, info = self._model.transcribe(
            np.asarray(audio, dtype=np.float32),
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=False,  # utterances are already VAD-segmented
            condition_on_previous_text=False,
            initial_prompt=self._prompt,
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language or (self._language or "")

    def transcribe_words(self, audio: np.ndarray) -> tuple[str, str, list[Word]]:
        """Like ``transcribe`` plus word timestamps (seconds from the start of ``audio``)."""
        res = self.transcribe_detailed(audio)
        return res.text, res.lang, res.words

    def transcribe_detailed(self, audio: np.ndarray, beam_size: int | None = None) -> AsrResult:
        """``transcribe_words`` plus the model's average log-probability.

        Goes through faster-whisper's ``BatchedInferencePipeline`` with a single clip
        covering the whole utterance. The plain ``WhisperModel.transcribe`` with
        ``word_timestamps=True`` re-seeks to the last word and encodes the remaining tail
        again, which made it 3-8x slower on CPU; the batched path costs about 15 % more
        than plain transcription.
        """
        self.load()
        assert self._pipeline is not None
        samples = np.asarray(audio, dtype=np.float32)
        segments, info = self._pipeline.transcribe(
            samples,
            language=self._language,
            beam_size=beam_size or self._beam_size,
            word_timestamps=True,
            clip_timestamps=[{"start": 0.0, "end": len(samples) / 16000}],
            batch_size=1,
            condition_on_previous_text=False,
            initial_prompt=self._prompt,
        )
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        words = [
            Word(float(w.start), float(w.end), w.word.strip())
            for s in segments
            for w in (s.words or [])
            if w.word.strip()
        ]
        logprob = weighted_logprob(
            [
                (float(s.avg_logprob), float(s.end - s.start))
                for s in segments
                if getattr(s, "avg_logprob", None) is not None
            ]
        )
        return AsrResult(text, info.language or (self._language or ""), words, logprob)


    def transcribe_longform(
        self,
        audio: np.ndarray,
        *,
        batch_size: int = 4,
        beam_size: int | None = None,
        progress: Callable[[float, float], None] | None = None,
    ) -> tuple[list[LongSegment], str]:
        """Transcribe a whole recording in one go: ``(segments, language)``.

        Uses faster-whisper's batched pipeline with its Silero VAD, so silence is skipped and
        30 s windows are decoded ``batch_size`` at a time (much faster per audio second than
        clip-by-clip decoding, and the model sees whole sentences instead of cut ones).
        ``progress(seconds_done, seconds_total)`` is called after every segment.
        """
        self.load()
        assert self._pipeline is not None
        samples = np.asarray(audio, dtype=np.float32)
        total = len(samples) / SAMPLE_RATE
        segments, info = self._pipeline.transcribe(
            samples,
            language=self._language,
            beam_size=beam_size or self._beam_size,
            word_timestamps=True,
            vad_filter=True,
            batch_size=batch_size,
            condition_on_previous_text=False,
            initial_prompt=self._prompt,
        )
        out: list[LongSegment] = []
        for s in segments:
            words = [
                Word(float(w.start), float(w.end), w.word.strip())
                for w in (s.words or [])
                if w.word.strip()
            ]
            lp = getattr(s, "avg_logprob", None)
            out.append(
                LongSegment(float(s.start), float(s.end), s.text.strip(), words,
                            None if lp is None else float(lp))
            )
            if progress is not None:
                progress(min(float(s.end), total), total)
        if progress is not None:
            progress(total, total)
        return out, info.language or (self._language or "")


def _make_faster_whisper(cfg: AsrConfig) -> Transcriber:
    return WhisperTranscriber(
        cfg.model,
        compute_type=cfg.compute_type,
        cpu_threads=cfg.cpu_threads,
        language=cfg.language,
        beam_size=cfg.beam_size,
        initial_prompt=cfg.initial_prompt,
    )


#: backend name -> factory. Add e.g. "sherpa-onnx" here later.
BACKENDS: dict[str, Callable[[AsrConfig], Transcriber]] = {
    "faster-whisper": _make_faster_whisper,
}


def create_transcriber(cfg: AsrConfig) -> Transcriber:
    try:
        factory = BACKENDS[cfg.backend]
    except KeyError:
        raise ValueError(
            f"Unknown ASR backend {cfg.backend!r}; available: {', '.join(sorted(BACKENDS))}"
        ) from None
    return factory(cfg)
