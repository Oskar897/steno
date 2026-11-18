"""Optional tests against the real speaker model. Skipped unless the model is on disk.

Point ``STENO_TEST_SPEAKER_MODEL`` at the .onnx file, or let Steno download it once
(``steno enroll`` / a recording) into the real data dir. Nothing is downloaded here.
To run the length check on real speech instead of the synthetic voice, set
``STENO_TEST_SPEECH_WAV`` to a 16 kHz mono WAV of continuous speech (kept outside the repo).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from platformdirs import user_data_dir

from steno.config import SpeakersConfig
from steno.speakers import SherpaEmbedder, cosine

from .synth import SR, silence

MODEL_NAME = SpeakersConfig().model


def _find_model() -> Path | None:
    override = os.environ.get("STENO_TEST_SPEAKER_MODEL")
    candidates = [Path(override)] if override else []
    candidates.append(Path(user_data_dir("Steno", appauthor=False)) / "models" / MODEL_NAME)
    return next((c for c in candidates if c.is_file()), None)


def _synthetic_voice(seconds: float = 4.0, f0: float = 120.0, seed: int = 0) -> np.ndarray:
    """Crude vowel-like signal: gliding glottal pulse train through three formant resonators."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f = f0 * (1 + 0.08 * np.sin(2 * np.pi * 0.7 * t))
    phase = np.cumsum(f) / SR
    source = np.zeros(n)
    source[np.diff(np.floor(phase), prepend=0) > 0] = 1.0
    source += 0.01 * rng.standard_normal(n)
    out = np.zeros(n)
    for centre, bw in ((700, 130), (1200, 70), (2600, 160)):
        r = np.exp(-np.pi * bw / SR)
        a1, a2 = -2 * r * np.cos(2 * np.pi * centre / SR), r * r
        y = np.zeros(n)
        for i in range(2, n):
            y[i] = source[i] - a1 * y[i - 1] - a2 * y[i - 2]
        out += y
    return (0.3 * out / np.max(np.abs(out))).astype(np.float32)


def _speech() -> np.ndarray:
    wav = os.environ.get("STENO_TEST_SPEECH_WAV")
    if wav:
        import soundfile

        data, rate = soundfile.read(wav, dtype="float32")
        assert rate == SR, "STENO_TEST_SPEECH_WAV must be 16 kHz"
        return data if data.ndim == 1 else data.mean(axis=1)
    return _synthetic_voice()


@pytest.fixture(scope="module")
def embedder():
    model = _find_model()
    if model is None:
        pytest.skip("speaker model not downloaded")
    pytest.importorskip("sherpa_onnx")
    emb = SherpaEmbedder(model.name)
    # Use the file where it is: skip Steno's models dir lookup (and any download).
    import sherpa_onnx

    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2)
    emb._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    return emb


def test_embedding_is_normalised(embedder):
    vec = embedder.embed(_speech())
    assert vec.ndim == 1 and vec.shape[0] > 64
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-4


def test_trailing_silence_padding_does_not_change_embedding_much(embedder):
    """Guards the known length-sensitivity problem of sherpa-onnx speaker embeddings."""
    segment = _speech()
    padded = np.concatenate([segment, silence(0.5)])
    assert cosine(embedder.embed(segment), embedder.embed(padded)) > 0.9


def test_different_voices_are_less_similar_than_the_same_voice(embedder):
    a1 = embedder.embed(_synthetic_voice(f0=110.0, seed=1))
    a2 = embedder.embed(_synthetic_voice(f0=110.0, seed=2))
    b = embedder.embed(_synthetic_voice(f0=210.0, seed=3))
    assert cosine(a1, a2) > cosine(a1, b)
