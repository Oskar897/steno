from __future__ import annotations

import queue

import numpy as np
import pytest

from steno.audio import ArraySource, GapFiller

from .synth import SR, tone


def test_gap_filler_idle_fills_wall_clock_time():
    f = GapFiller(SR, start=10.0)
    assert f.on_idle(10.1) == 0  # below idle threshold
    assert f.on_idle(10.5) == round(0.5 * SR)
    assert f.on_idle(10.6) == 0  # only the new 0.1 s, still below threshold
    assert f.on_idle(11.0) == round(0.5 * SR)  # 10.5 -> 11.0


def test_gap_filler_before_chunk_after_silence():
    f = GapFiller(SR, start=0.0)
    # 30 ms chunk arriving at t=5 s: audio started at 4.97, previous end at 0 -> ~4.97 s gap
    zeros = f.on_chunk(int(0.03 * SR), now=5.0)
    assert zeros == pytest.approx(4.97 * SR, abs=2)
    # contiguous chunk: no padding
    assert f.on_chunk(int(0.03 * SR), now=5.03) == 0


def test_gap_filler_ignores_small_jitter():
    f = GapFiller(SR, start=0.0)
    f.on_chunk(480, now=0.03)
    assert f.on_chunk(480, now=0.09) == 0  # 30 ms late is within tolerance


def test_array_source_chunks_and_eof():
    audio = tone(1.0)
    q: queue.Queue = queue.Queue()
    src = ArraySource("mic", audio, chunk_s=0.1)
    src.start(q, 0.0)
    chunks = []
    while True:
        c = q.get(timeout=2)
        chunks.append(c)
        if c.eof:
            break
    src.stop()
    data = np.concatenate([c.samples for c in chunks])
    assert np.array_equal(data, audio)
    assert all(c.source == "mic" for c in chunks)
    assert chunks[1].t_start == pytest.approx(0.1)
    assert chunks[-1].samples.size == 0


def test_load_audio_file_resamples_to_16k_mono(tmp_path):
    import wave

    pytest.importorskip("av")
    from steno.audio import load_audio_file

    rate = 8000
    pcm = (tone(1.0, amp=0.5)[::2] * 32767).astype("<i2")  # crude 8 kHz sine
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    audio = load_audio_file(str(path))
    assert audio.dtype == np.float32
    assert len(audio) == pytest.approx(16000, abs=400)
    assert 0.2 < np.abs(audio).max() < 0.8
