"""Speech recognition backends behind a minimal protocol.

The engine only needs ``transcribe(audio) -> (text, lang)``. To add another
backend (for example sherpa-onnx / Parakeet), implement ``Transcriber``, register
it in ``BACKENDS`` and select it with ``[asr] backend`` in the config.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import numpy as np

from steno import paths
from steno.config import AsrConfig

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


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
    ):
        self.name = model
        self._model_id = model
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._language = None if language == "auto" else language
        self._beam_size = beam_size
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel

        log.info("Loading ASR model %s", self._model_id)
        self._model = WhisperModel(
            self._model_id,
            device="cpu",
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
            download_root=str(paths.models_dir()),
        )

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        self.load()
        assert self._model is not None
        segments, info = self._model.transcribe(
            np.asarray(audio, dtype=np.float32),
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=False,  # utterances are already VAD-segmented
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language or (self._language or "")


def _make_faster_whisper(cfg: AsrConfig) -> Transcriber:
    return WhisperTranscriber(
        cfg.model,
        compute_type=cfg.compute_type,
        cpu_threads=cfg.cpu_threads,
        language=cfg.language,
        beam_size=cfg.beam_size,
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
