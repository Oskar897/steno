"""Incremental diarization during recording: windows, skipping silence, save/load, resolve."""

from __future__ import annotations

import numpy as np
import pytest

from steno import segmentation as seg
from steno.config import SpeakersConfig
from steno.diarize import DiarizeParams, resolve
from steno.livediar import DIAR_NAME, LiveDiarizer, build_stats, load_stats
from steno.segmentation import FRAME_SHIFT, NUM_FRAMES, SAMPLE_RATE, WINDOW_SAMPLES

from .synth import FakeEmbedder, silence, timeline
from .test_diarize import A, B

SR = SAMPLE_RATE


class FakeRing:
    """The reader the diarizer needs: a growing stream of samples addressed by index."""

    def __init__(self, audio, origin=0.0):
        self._audio = audio
        self.origin = origin
        self.available = 0  # samples "recorded" so far

    @property
    def n_samples(self):
        return self.available

    def grow(self, seconds):
        self.available = min(len(self._audio), self.available + int(seconds * SR))

    def read(self, first, last):
        if last > self.available:
            last = self.available
        return self._audio[first:last] if last > first else None


class ContentSegmenter:
    """Segmentation stand-in that looks at the audio: a loud 220 Hz frame is local speaker 0,
    a loud 330 Hz frame local speaker 1, the rest silence."""

    def __init__(self):
        self.loaded = False
        self.calls = 0

    def load(self):
        self.loaded = True

    def run_windows(self, windows):
        self.calls += 1
        out = np.full((len(windows), NUM_FRAMES, 7), 0.001, dtype=np.float32)
        for w, audio in enumerate(windows):
            for f in range(NUM_FRAMES):
                centre = f * FRAME_SHIFT + 495
                chunk = audio[max(centre - 800, 0) : centre + 800]
                if len(chunk) < 400 or float(np.sqrt(np.mean(np.square(chunk)))) < 0.05:
                    out[w, f, 0] = 0.994
                    continue
                peak = np.argmax(np.abs(np.fft.rfft(chunk))) * SR / len(chunk)
                out[w, f, 1 if abs(peak - A) < abs(peak - B) else 2] = 0.994
        return out


def meeting():
    # A talks 0.2-9, B 11-19, A again 21-28; then 25 s of quiet and B for 5 s
    audio = timeline((0.2, 9.0, 0.3, A), (11.0, 19.0, 0.3, B), (21.0, 28.0, 0.3, A),
                     (53.0, 58.0, 0.3, B))
    return np.concatenate([audio, silence(4.0)])


LINES = [(0.5, 8.5), (11.5, 18.5), (21.5, 27.5), (53.5, 57.5)]


def make(audio=None, **kw):
    audio = meeting() if audio is None else audio
    ring = FakeRing(audio)
    dz = LiveDiarizer(ContentSegmenter(), FakeEmbedder(noise=0.03), DiarizeParams(), **kw)
    return dz, ring


def run_live(dz, ring, lines=LINES):
    """Play the meeting second by second: lines are noted when they are over."""
    noted = 0
    for _ in range(int(len(ring._audio) / SR) + 1):
        ring.grow(1.0)
        now = ring.available / SR
        while noted < len(lines) and lines[noted][1] <= now:
            dz.note_line("mic", *lines[noted])
            noted += 1
        dz.step({"mic": ring}, limit=4)
    dz.finish({"mic": ring})


def test_windows_follow_the_audio_and_silence_is_skipped():
    dz, ring = make()
    run_live(dz, ring)
    total = seg.window_count(len(ring._audio), dz.step_frames)
    assert dz.windows + dz.skipped == total
    assert dz.skipped > 0 and dz.windows < total  # the quiet middle needs no model
    assert dz.segmenter.calls == dz.windows
    (stats,) = dz.stats()
    assert stats.n_samples == len(ring._audio) and stats.n_windows == total
    assert stats.soft.shape == (total, NUM_FRAMES, 3)
    quiet = stats.speech[int(35 / 2.5)]  # a window deep in the quiet stretch
    assert float(quiet.max()) == 0.0


def test_windows_wait_for_their_audio_and_the_margin():
    dz, ring = make(margin_s=6.0)
    dz.note_line("mic", 0.5, 8.5)
    ring.grow(15.0)  # first window needs 10 s + 6 s of margin
    assert dz.step({"mic": ring}) == 0
    ring.grow(2.0)
    assert dz.step({"mic": ring}) >= 1


def test_resolve_on_incremental_stats_finds_the_speakers_like_the_offline_pass():
    dz, ring = make()
    run_live(dz, ring)
    params = DiarizeParams()
    res = resolve(dz.stats(), params, SpeakersConfig())
    assert len(res.speaker_ids) == 2

    def cluster_at(sec):
        return next(t.cluster for t in res.turns if t.t_start <= sec <= t.t_end)

    a, b = cluster_at(2.0), cluster_at(13.0)
    assert a != b and cluster_at(24.0) == a and cluster_at(55.0) == b
    assert not any(t.t_start < 40 and t.t_end > 33 for t in res.turns)  # nothing in the quiet


def test_save_and_load_round_trip(tmp_path):
    dz, ring = make()
    run_live(dz, ring)
    assert dz.save(tmp_path)
    raw = (tmp_path / DIAR_NAME).read_bytes()
    assert raw.startswith(b"STENO-")  # protected, never plain npz: it holds voiceprints
    (loaded,) = load_stats(tmp_path)
    (orig,) = dz.stats()
    assert loaded.stream == "mic" and loaded.n_samples == orig.n_samples
    assert loaded.step_frames == orig.step_frames and loaded.n_windows == orig.n_windows
    assert np.allclose(loaded.soft, orig.soft, atol=2e-3)
    assert np.array_equal(loaded.hard, orig.hard)
    assert [(e.k, e.s, e.clean) for e in loaded.embs] == [(e.k, e.s, e.clean) for e in orig.embs]
    assert np.allclose([e.vec for e in loaded.embs], [e.vec for e in orig.embs])
    a = resolve([orig], DiarizeParams(), SpeakersConfig())
    b = resolve([loaded], DiarizeParams(), SpeakersConfig())
    assert [(round(t.t_start, 1), round(t.t_end, 1), t.cluster) for t in a.turns] == [
        (round(t.t_start, 1), round(t.t_end, 1), t.cluster) for t in b.turns]


def test_nothing_to_save_without_audio(tmp_path):
    dz, _ = make()
    assert not dz.save(tmp_path) and load_stats(tmp_path) is None


def test_a_window_that_left_the_buffer_counts_as_silence():
    class Trimmed(FakeRing):
        def read(self, first, last):
            return None

    dz = LiveDiarizer(ContentSegmenter(), FakeEmbedder(), DiarizeParams())
    ring = Trimmed(meeting())
    dz.note_line("mic", 0.5, 8.5)
    ring.grow(20)
    assert dz.step({"mic": ring}) >= 1
    assert dz.windows == 0 and dz.skipped >= 1


def test_streams_are_kept_apart():
    loop = np.concatenate([silence(10.0), timeline((0.0, 6.0, 0.3, B)), silence(20)])
    mic = meeting()
    ring_m, ring_l = FakeRing(mic), FakeRing(loop)
    dz = LiveDiarizer(ContentSegmenter(), FakeEmbedder(noise=0.03), DiarizeParams())
    for r in (ring_m, ring_l):
        r.grow(60)
    for ln in LINES:
        dz.note_line("mic", *ln)
    dz.note_line("loopback", 10.2, 15.8)
    dz.finish({"mic": ring_m, "loopback": ring_l})
    names = {s.stream: s for s in dz.stats()}
    assert set(names) == {"mic", "loopback"}
    assert float(names["loopback"].speech.max()) > 0.5
    res = resolve(list(names.values()), DiarizeParams(), SpeakersConfig())
    assert {t.stream for t in res.turns} == {"mic", "loopback"}


def test_build_stats_fills_unprocessed_windows_with_silence():
    probs = {1: np.full((NUM_FRAMES, 7), 0.0, dtype=np.float32)}
    probs[1][:, 1] = 1.0  # window 1: local speaker 0 talks throughout
    st = build_stats("mic", WINDOW_SAMPLES * 3, 100, probs, [])
    assert st.n_windows == seg.window_count(WINDOW_SAMPLES * 3, 100)
    assert st.speech[0].max() == 0.0 and st.speech[1].min() == pytest.approx(1.0)
    assert st.hard[1][:, 0].all() and not st.hard[0].any()
