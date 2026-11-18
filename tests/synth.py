"""Synthetic audio and fakes for tests. No real recordings, ever."""

from __future__ import annotations

import numpy as np

SR = 16000


def tone(seconds: float, amp: float = 0.3, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def timeline(*parts: tuple[float, float, float]) -> np.ndarray:
    """Build audio from (start_s, end_s, amplitude) tone bursts on a silent background."""
    total = max(end for _, end, _ in parts)
    out = silence(total + 0.01)
    for start, end, amp in parts:
        a, b = int(start * SR), int(end * SR)
        out[a:b] += tone((b - a) / SR, amp)
    return out


class EnergyVad:
    """Fake speech model: a frame is speech when its RMS exceeds a threshold."""

    def __init__(self, level: float = 0.02):
        self.level = level

    def reset(self) -> None:
        pass

    def __call__(self, frames: np.ndarray) -> np.ndarray:
        level = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
        return (level > self.level).astype(np.float32)


class FakeAsr:
    """Fake transcriber: text encodes the utterance loudness, e.g. 'amp21'."""

    name = "fake-asr"

    def __init__(self, delay: float = 0.0):
        self.calls = 0
        self.loaded = False
        self.delay = delay

    def load(self) -> None:
        self.loaded = True

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        self.calls += 1
        level = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        return f"amp{round(level * 100)}", "sv"
