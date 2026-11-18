from __future__ import annotations

import numpy as np
import pytest

from steno.vad import FRAME, AutoGain, SpeechSegmenter, VadParams

from .synth import SR, EnergyVad, silence, timeline, tone


def segment(audio, params=None, chunk=1234):
    """Feed audio in odd-sized chunks, then flush; return all utterances."""
    seg = SpeechSegmenter(EnergyVad(), params or VadParams())
    out = []
    for i in range(0, len(audio), chunk):
        out += seg.feed(audio[i : i + chunk])
    out += seg.flush()
    return out


def test_single_utterance_bounds():
    audio = timeline((1.0, 2.0, 0.3))
    audio = np.concatenate([audio, silence(1.5)])
    (u,) = segment(audio)
    # preroll 200 ms before onset, speech 1 s, tail 160 ms after
    assert u.t_start == pytest.approx(0.8, abs=0.05)
    assert u.t_end == pytest.approx(2.16, abs=0.08)
    assert len(u.audio) == pytest.approx((u.t_end - u.t_start) * SR, abs=FRAME)
    assert u.audio.dtype == np.float32


def test_short_pause_does_not_split_but_long_pause_does():
    short = np.concatenate([timeline((0.5, 1.5, 0.3), (1.8, 2.8, 0.3)), silence(1.5)])
    assert len(segment(short)) == 1
    long = np.concatenate([timeline((0.5, 1.5, 0.3), (2.5, 3.5, 0.3)), silence(1.5)])
    first, second = segment(long)
    assert first.t_end < second.t_start
    assert second.t_start == pytest.approx(2.3, abs=0.05)


def test_min_silence_is_configurable():
    audio = np.concatenate([timeline((0.5, 1.5, 0.3), (1.8, 2.8, 0.3)), silence(1.5)])
    assert len(segment(audio, VadParams(min_silence_ms=200))) == 2


def test_blip_shorter_than_min_speech_is_dropped():
    audio = np.concatenate([timeline((0.5, 0.6, 0.3)), silence(2.0)])
    assert segment(audio) == []


def test_flush_emits_open_utterance():
    audio = timeline((0.5, 1.5, 0.3))  # ends abruptly while "speaking"
    seg = SpeechSegmenter(EnergyVad())
    assert seg.feed(audio) == []
    assert seg.open_start_s is not None
    assert seg.open_rms() > 0.1
    (u,) = seg.flush()
    assert u.t_start == pytest.approx(0.3, abs=0.05)
    assert seg.open_start_s is None


def test_result_independent_of_chunk_size():
    audio = np.concatenate([timeline((0.5, 1.5, 0.3), (2.5, 3.5, 0.2)), silence(1.5)])
    a = segment(audio, chunk=100)
    b = segment(audio, chunk=7000)
    assert len(a) == len(b) == 2
    for x, y in zip(a, b, strict=True):
        assert (x.t_start, x.t_end) == (y.t_start, y.t_end)
        assert np.array_equal(x.audio, y.audio)


def test_max_segment_cuts_at_quiet_point():
    # 22 s of continuous speech with a 100 ms dip at 12 s; limit 15 s.
    audio = np.concatenate([tone(12.0), silence(0.1), tone(10.0), silence(1.0)])
    utts = segment(audio, VadParams(max_segment_s=15.0, min_silence_ms=500))
    assert len(utts) == 2
    assert utts[0].duration <= 15.0 + 2 * FRAME / SR
    assert utts[0].t_end == pytest.approx(12.1, abs=0.1)  # cut inside the dip
    assert utts[1].t_start == pytest.approx(utts[0].t_end, abs=1 / SR)  # contiguous


def test_max_segment_with_no_quiet_point_still_bounded():
    audio = np.concatenate([tone(40.0), silence(1.0)])
    utts = segment(audio, VadParams(max_segment_s=10.0))
    assert len(utts) >= 4
    assert all(u.duration <= 10.0 + 0.1 for u in utts)
    covered = sum(len(u.audio) for u in utts)
    assert covered >= 40.0 * SR - 2 * FRAME  # nothing lost between cuts


def test_position_tracks_complete_frames():
    seg = SpeechSegmenter(EnergyVad())
    seg.feed(silence(1.0))
    assert seg.position_s == pytest.approx(1.0, abs=FRAME / SR)


def test_silero_onnx_model_shapes_and_state():
    pytest.importorskip("faster_whisper")
    pytest.importorskip("onnxruntime")
    from steno.vad import SileroVad

    model = SileroVad()
    frames = silence(0.5)[: 10 * FRAME].reshape(10, FRAME)
    probs = model(frames)
    assert probs.shape == (10,)
    assert probs.max() < 0.5  # silence
    # streaming one frame at a time matches batch processing (state + context carried)
    noise = (np.random.default_rng(0).standard_normal((8, FRAME)) * 0.05).astype(np.float32)
    model.reset()
    batch = model(noise)
    model.reset()
    single = np.concatenate([model(noise[i : i + 1]) for i in range(8)])
    assert np.allclose(batch, single, atol=1e-4)


# --------------------------------------------------------------------------- input gain


def test_gain_lets_the_vad_hear_quiet_speech_without_changing_the_audio():
    quiet = np.concatenate([silence(0.5), tone(2.0, 0.01), silence(1.5)])  # RMS 0.007 < 0.02
    plain = SpeechSegmenter(EnergyVad())
    assert plain.feed(quiet) + plain.flush() == []
    loud_ears = SpeechSegmenter(EnergyVad())
    out = loud_ears.feed(quiet, gain=4.0) + loud_ears.flush()
    assert len(out) == 1
    assert float(np.max(np.abs(out[0].audio))) == pytest.approx(0.01, abs=1e-4)  # untouched


def test_auto_gain_lifts_quiet_audio_but_never_attenuates_or_overshoots():
    quiet = tone(5.0, 0.01)  # RMS ~0.007
    gain = AutoGain(target=0.05, max_gain=8.0)
    assert gain.update(quiet[: SR // 2]) == 1.0  # not enough history yet
    for i in range(0, len(quiet), SR // 2):
        g = gain.update(quiet[i : i + SR // 2])
    assert g == pytest.approx(0.05 / (0.01 / 2**0.5), rel=0.01)  # lifted to the target level
    capped = AutoGain(target=0.05, max_gain=3.0)
    for i in range(0, len(quiet), SR // 2):
        g = capped.update(quiet[i : i + SR // 2])
    assert g == 3.0
    medium = AutoGain(target=0.05, max_gain=20.0)
    for _ in range(10):
        g = medium.update(tone(0.5, 0.05))
    assert 1.0 < g < 20.0
    loud = AutoGain()
    for _ in range(10):
        g = loud.update(tone(0.5, 0.5))
    assert g == 1.0
