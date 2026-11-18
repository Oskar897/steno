"""Synthetic audio and fakes for tests. No real recordings, ever."""

from __future__ import annotations

import numpy as np

SR = 16000


def tone(seconds: float, amp: float = 0.3, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def timeline(*parts: tuple[float, ...]) -> np.ndarray:
    """Build audio from (start_s, end_s, amplitude[, freq_hz]) tone bursts on silence."""
    total = max(p[1] for p in parts)
    out = silence(total + 0.01)
    for start, end, amp, *rest in parts:
        a, b = int(start * SR), int(end * SR)
        out[a:b] += tone((b - a) / SR, amp, *(rest or [220.0]))
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


#: Fake voices: the tone frequency of a burst identifies the "speaker" for FakeEmbedder.
VOICE_FREQS = {"Alice": 220.0, "Bob": 330.0, "Carol": 440.0}
_VOICE_INDEX = {freq: i for i, freq in enumerate(VOICE_FREQS.values())}
EMB_DIM = 16


def voice_vector(index: int, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """Unit vector for fake voice ``index``; voices are (nearly) orthogonal to each other."""
    vec = np.zeros(EMB_DIM, dtype=np.float32)
    vec[index] = 1.0
    if noise:
        vec += np.random.default_rng(seed).normal(0, noise, EMB_DIM).astype(np.float32)
    return (vec / np.linalg.norm(vec)).astype(np.float32)


class FakeEmbedder:
    """Fake speaker model: the dominant tone frequency selects one of the VOICE_FREQS vectors."""

    name = "fake-embedder"

    def __init__(self, noise: float = 0.05):
        self.noise = noise
        self.calls: list[float] = []  # durations of embedded audio
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def embed(self, audio: np.ndarray) -> np.ndarray:
        self.calls.append(len(audio) / SR)
        spectrum = np.abs(np.fft.rfft(audio))
        peak = float(np.argmax(spectrum)) * SR / len(audio)
        freq = min(_VOICE_INDEX, key=lambda f: abs(f - peak))
        return voice_vector(_VOICE_INDEX[freq], self.noise, seed=len(self.calls))


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
