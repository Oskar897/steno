from __future__ import annotations

import json

import numpy as np

from steno import transcript
from steno.asr import AsrResult
from steno.audio import ArraySource
from steno.config import Config
from steno.engine import Engine
from steno.windows import Word

from .synth import EnergyVad, FakeEmbedder
from .test_engine_speakers import interjection_audio


class DetailedAsr:
    """Backend that offers words and a confidence (like WhisperTranscriber)."""

    name = "fake-detailed"

    def load(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        return "x", "sv"

    def transcribe_detailed(self, audio: np.ndarray) -> AsrResult:
        n = max(1, int(len(audio) / 16000 / 0.25))
        ws = [Word(i * 0.25, i * 0.25 + 0.2, f"w{i}") for i in range(n)]
        return AsrResult(" ".join(w.text for w in ws), "sv", ws, avg_logprob=-0.4321)


def run(asr, **kw):
    engine = Engine(Config(), asr, [ArraySource("mic", interjection_audio())],
                    vad_factory=EnergyVad, embedder=FakeEmbedder(), **kw)
    engine.start()
    assert engine.wait(timeout=20)
    engine.stop()
    return engine


def test_live_lines_store_their_own_words_and_the_utterance_confidence():
    engine = run(DetailedAsr())
    segs = transcript.read_segments(engine.session_dir)
    assert len(segs) == 3  # split at the interjection, as without the optional fields
    assert all(s.avg_logprob == -0.432 for s in segs)
    for s in segs:
        assert s.words
        assert [w[2] for w in s.words] == s.text.split()  # the line's words, no one else's
        assert s.t_start - 0.3 <= s.words[0][0] and s.words[-1][1] <= s.t_end + 0.3
        assert all(a[0] <= b[0] for a, b in zip(s.words, s.words[1:], strict=False))
    all_words = [w[2] for s in segs for w in s.words]
    assert all_words == [f"w{i}" for i in range(len(all_words))]  # every word exactly once
    first_line = json.loads((engine.session_dir / "transcript.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert first_line["words"][0][2] == "w0"


def test_word_times_are_session_seconds_and_origins_are_recorded():
    engine = run(DetailedAsr())
    segs = transcript.read_segments(engine.session_dir)
    assert segs[0].words[0][0] >= 0.5  # the audio starts with ~1 s of silence
    meta = json.loads((engine.session_dir / "session.json").read_text(encoding="utf-8"))
    assert set(meta["stream_origins"]) == {"mic"}


def test_backend_without_details_still_works_and_stores_no_confidence():
    from .synth import FakeWordAsr

    engine = run(FakeWordAsr())
    segs = transcript.read_segments(engine.session_dir)
    assert segs and all(s.avg_logprob is None for s in segs)
    assert all(s.words for s in segs)  # word timestamps are stored whenever the backend has them
