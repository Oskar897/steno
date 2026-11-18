"""Rolling refinement: audio ring, window planning, word assignment, the worker, the engine."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import numpy as np
import pytest

from steno import transcript
from steno.asr import LongSegment
from steno.audio import ArraySource
from steno.config import Config, ConfigError, RefineConfig, config_from_dict
from steno.engine import Engine
from steno.refine import (
    AudioRing,
    LiveLine,
    Refiner,
    _Requeue,
    accept_revision,
    assign_words_to_lines,
    mute_outside,
    pick_window,
)
from steno.transcript import Segment
from steno.windows import Word

from .synth import EnergyVad, FakeAsr, silence, timeline

SR = 16000


def line(seq, t0, t1, text="x y z", source="mic"):
    return LiveLine(seq, source, t0, t1, text)


def seg(seq, t0, t1, text="draft words here", source="mic"):
    return Segment(seq, t0, t1, "2026-01-01T10:00:00", source, "Me", text, "sv")


# --------------------------------------------------------------------------- audio ring


def test_ring_returns_the_requested_stream_time_range():
    ring = AudioRing(capacity_s=60)
    t = np.arange(SR * 4) / SR
    wave = (0.5 * np.sin(2 * np.pi * 5 * t)).astype(np.float32)
    for i in range(0, len(wave), 1600):  # 0.1 s blocks, as the engine feeds them
        ring.append(wave[i : i + 1600], origin=10.0)
    audio, start = ring.get(11.0, 13.0)
    assert abs(start - 11.0) < 1e-3 and len(audio) == 2 * SR
    assert np.allclose(audio, wave[SR : 3 * SR], atol=1e-3)  # int16 quantisation only
    assert ring.start_s == 10.0 and ring.end_s == pytest.approx(14.0)
    # ranges outside what is held are clipped, not an error
    audio, start = ring.get(13.5, 20.0)
    assert len(audio) == int(0.5 * SR) and start == pytest.approx(13.5)
    assert len(ring.get(20.0, 30.0)[0]) == 0


def test_ring_is_bounded_and_drops_the_oldest_audio():
    ring = AudioRing(capacity_s=5)
    block = np.full(SR, 0.1, dtype=np.float32)
    for _ in range(30):  # 30 s in, 1 s at a time
        ring.append(block, origin=0.0)
    assert ring.nbytes <= 6 * SR * 2  # 16 bit, about the capacity
    assert 24.0 <= ring.start_s <= 26.0 and ring.end_s == pytest.approx(30.0)
    assert len(ring.get(0.0, 10.0)[0]) == 0  # long gone
    assert len(ring.get(27.0, 29.0)[0]) == 2 * SR


def test_mute_outside_keeps_only_the_padded_spans():
    audio = np.ones(10 * SR, dtype=np.float32)
    out = mute_outside(audio, 100.0, [(101.0, 102.0), (106.0, 107.0)], pad=0.5)
    kept = out != 0
    assert kept[int(0.5 * SR) + 10] and kept[int(2.4 * SR)]  # inside the first span + pad
    assert not kept[int(3.0 * SR)] and not kept[int(9.0 * SR)]  # between / after
    assert audio.min() == 1.0  # input untouched


# --------------------------------------------------------------------------- planning


def lines_of(*spans):
    return [line(i + 1, a, b) for i, (a, b) in enumerate(spans)]


def test_window_is_not_due_before_enough_speech():
    ls = lines_of((0, 5), (6, 11))
    assert pick_window(ls, speech_s=25, now=12.0, max_wait_s=60) is None


def test_window_is_due_by_speech_by_age_and_when_flushing():
    ls = lines_of((0, 10), (11, 21), (22, 32))
    assert pick_window(ls, speech_s=25, now=33.0, max_wait_s=60) == 3  # 30 s of speech
    short = lines_of((0, 5), (6, 8))
    assert pick_window(short, speech_s=25, now=61.0, max_wait_s=60) == 2  # oldest is 61 s old
    assert pick_window(short, speech_s=25, now=None, max_wait_s=60, flush=True) == 2
    assert pick_window([], speech_s=25, now=None, max_wait_s=60, flush=True) is None


def test_window_is_cut_at_the_longest_pause_near_the_target():
    # 8 lines of 4 s; the target of 25 s is reached after the 7th, the longest pause
    # (3 s) sits after the 5th line (20 s of speech >= 60 % of 25 s)
    spans, t = [], 0.0
    for i in range(8):
        spans.append((t, t + 4))
        t += 4 + (3.0 if i == 4 else 0.5)
    ls = lines_of(*spans)
    assert pick_window(ls, speech_s=25, now=t + 5, max_wait_s=60) == 5


def test_a_very_long_pending_list_is_taken_one_window_at_a_time():
    ls = lines_of(*[(i * 6.0, i * 6.0 + 5.0) for i in range(30)])  # 150 s of speech
    n = pick_window(ls, speech_s=25, now=500.0, max_wait_s=60, flush=True)
    assert n is not None and 3 <= n <= 5  # 5 s lines: 15-25 s of speech
    assert sum(x.duration for x in ls[:n]) < 25 * 1.6


def test_words_go_to_the_line_that_contains_them_or_the_nearest_within_the_pad():
    ls = lines_of((0.0, 2.0), (2.5, 5.0))
    words = [
        Word(0.1, 0.5, "a"), Word(1.6, 2.1, "b"),  # inside line 1 (midpoint 1.85)
        Word(2.2, 2.4, "gap"),  # in the gap: nearest line within the pad
        Word(2.6, 3.0, "c"), Word(4.0, 4.9, "d"),
        Word(7.0, 7.5, "late"),  # no line near: dropped (echo, noise, speech the VAD missed)
    ]
    first, second = assign_words_to_lines(words, ls, pad=0.3)
    assert [w.text for w in first] == ["a", "b"]
    assert [w.text for w in second] == ["gap", "c", "d"]
    assert assign_words_to_lines([Word(9, 9.5, "z")], ls) == [[], []]


def test_revision_sanity_checks():
    assert accept_revision("det här är en mening", "det här är en bättre mening")
    assert not accept_revision("det här är en mening", " . ")
    assert not accept_revision("one two three four five six seven eight", "one")  # lost most
    assert not accept_revision("hej", "ja " * 30)  # far more than the draft
    assert not accept_revision("hej", "ha " * 40)  # looping
    assert accept_revision("ja", "ja precis")  # short drafts are not held to the ratio


# --------------------------------------------------------------------------- fake model


class FakeLongform:
    """Long-form ASR fake: every speech burst of the audio becomes one segment of two words."""

    name = "fake-longform"

    def __init__(self, fail_first: int = 0, delay: float = 0.0):
        self.calls: list[dict] = []
        self.audio: list[np.ndarray] = []
        self.loaded = False
        self.fail_first = fail_first
        self.delay = delay

    def load(self):
        self.loaded = True

    def transcribe_longform(self, audio, *, batch_size=4, beam_size=1, progress=None,
                            detect_english=False):
        self.calls.append({"batch_size": batch_size, "beam_size": beam_size, "len": len(audio),
                           "detect_english": detect_english})
        self.audio.append(np.asarray(audio))
        if self.fail_first:
            self.fail_first -= 1
            raise RuntimeError("boom")
        if self.delay:
            time.sleep(self.delay)
        n = len(self.calls)
        frame = SR // 10
        loud = [
            float(np.sqrt(np.mean(np.square(audio[i : i + frame], dtype=np.float64)))) > 0.02
            for i in range(0, len(audio) - frame + 1, frame)
        ]
        runs, start = [], None
        for i, on in enumerate(loud + [False]):
            if on and start is None:
                start = i
            elif not on and start is not None:
                runs.append((start / 10, i / 10))
                start = None
        segments = []
        for k, (a, b) in enumerate(runs):
            mid = (a + b) / 2
            words = [Word(a + 0.05, mid, f"c{n}r{k}"), Word(mid, b - 0.05, "refined")]
            segments.append(LongSegment(a, b, " ".join(w.text for w in words), words, -0.2))
            if progress is not None:
                progress(b, len(audio) / SR)
        return segments, "sv"


def cfg_refine(**kw):
    base = replace(RefineConfig(), window_speech_s=3.0, max_wait_s=1000.0, pad_s=0.2)
    return replace(base, **kw)


class Collector:
    def __init__(self):
        self.items: list[tuple[int, str, dict]] = []
        self.models: list[str] = []

    def __call__(self, items, model):
        self.items.extend(items)
        self.models.append(model)


def feed(refiner, audio, origin=0.0, block=1600):
    for i in range(0, len(audio), block):
        refiner.feed("mic", audio[i : i + block], origin)


# --------------------------------------------------------------------------- refiner


def burst_audio():
    # three bursts; the one at 5-6.5 s is "echo" that has no live line
    return timeline((1.0, 2.5, 0.3), (5.0, 6.5, 0.3), (8.0, 9.5, 0.3), (12.0, 13.0, 0.3)) \
        if False else timeline((1.0, 2.5, 0.3), (5.0, 6.5, 0.3), (8.0, 9.5, 0.3))


def test_refiner_revises_the_lines_of_a_window_and_leaves_echo_out():
    tr, out = FakeLongform(), Collector()
    r = Refiner(cfg_refine(window_speech_s=3.0), tr, out)
    r.load()
    r.start()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5))  # the burst at 5-6.5 s was dropped as echo: no line
    r.note_line(seg(2, 8.0, 9.5))
    assert r.finish(timeout=20)
    assert tr.loaded and len(tr.calls) == 1  # both lines in one window
    seqs = [i[0] for i in out.items]
    assert seqs == [1, 2]
    assert all(t.startswith("c1r") for _, t, _ in out.items)  # two runs only: echo was muted
    assert out.models == ["fake-longform"]
    s, text, extra = out.items[0]
    assert extra["words"][0][0] >= 1.0 and extra["words"][-1][1] <= 2.5  # session seconds
    assert extra["avg_logprob"] == pytest.approx(-0.2) and extra["lang"] == "sv"
    # the muted gap really is silent in what the model saw (window starts at 0.8 s)
    audio = tr.audio[0]
    a0 = int((4.0 - 0.8) * SR)
    assert float(np.abs(audio[a0 : a0 + int(2.7 * SR)]).max()) == 0.0
    assert r.stats.windows == 1 and r.stats.revisions == 2


def test_refiner_waits_for_enough_speech_then_flushes_the_tail_at_finish():
    tr, out = FakeLongform(), Collector()
    r = Refiner(cfg_refine(window_speech_s=2.5), tr, out)
    r.load()
    r.start()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5))
    time.sleep(0.4)
    assert out.items == [] and r.backlog_s == pytest.approx(1.5)  # 1.5 s < 2.5 s: not due yet
    r.note_line(seg(2, 5.0, 6.5))  # now 3 s: due
    deadline = time.time() + 10
    while not out.items and time.time() < deadline:
        time.sleep(0.05)
    assert [i[0] for i in out.items] == [1, 2]
    r.note_line(seg(3, 8.0, 9.5))
    assert out.items[-1][0] == 2 and r.backlog_s == pytest.approx(1.5)
    assert r.finish(timeout=20)  # the tail: one more, smaller window
    assert [i[0] for i in out.items] == [1, 2, 3]
    assert r.stats.windows == 2 and r.backlog_s == 0.0


def test_refiner_survives_a_failing_window_and_keeps_going():
    tr, out = FakeLongform(fail_first=1), Collector()
    r = Refiner(cfg_refine(window_speech_s=1.0), tr, out)
    r.load()
    r.start()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5))
    r.note_line(seg(2, 5.0, 6.5))
    r.note_line(seg(3, 8.0, 9.5))
    assert r.finish(timeout=20)
    assert r.stats.failed >= 1
    assert out.items  # later windows still produced revisions
    assert 1 not in [i[0] for i in out.items]  # the failed window's line keeps its draft


def test_refiner_skips_revisions_that_say_the_same_or_look_wrong():
    class Same(FakeLongform):
        def transcribe_longform(self, audio, **kw):
            segs, lang = super().transcribe_longform(audio, **kw)
            first = segs[0]
            words = [Word(first.start, first.end, "Draft"), Word(first.start, first.end, "Words,")]
            same = LongSegment(first.start, first.end, "Draft Words,", words, -0.1)
            return [same, *segs[1:]], lang

    tr, out = Same(), Collector()
    r = Refiner(cfg_refine(window_speech_s=1.0), tr, out)
    r.load()
    r.start()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5, text="draft words"))  # same words apart from case/punctuation
    r.note_line(seg(2, 5.0, 6.5, text="something else entirely"))
    assert r.finish(timeout=20)
    assert [i[0] for i in out.items] == [2]


def test_refiner_degrades_to_the_fallback_model_and_skips_the_oldest_speech():
    slow, fast = FakeLongform(), FakeLongform()
    fast.name = "fake-fallback"
    out = Collector()
    cfg = cfg_refine(window_speech_s=2.0, max_backlog_windows=2, skip_after_windows=4)
    r = Refiner(cfg, slow, out, fallback_factory=lambda: fast)
    r.load()
    # no thread: drive the scheduler by hand so nothing races
    audio = np.concatenate([timeline(*[(i * 3.0 + 0.5, i * 3.0 + 2.0, 0.3) for i in range(12)]),
                            silence(1.0)])
    feed(r, audio)
    for i in range(12):  # 12 lines x 1.5 s = 18 s of speech = 9 windows of 2 s
        r.note_line(seg(i + 1, i * 3.0 + 0.5, i * 3.0 + 2.0))
    job = r._next_job()
    assert job is not None
    assert r.stats.skipped_s > 0  # more than skip_after_windows behind: the oldest was dropped
    assert r.backlog_s <= cfg.max_backlog_windows * cfg.window_speech_s + 1.5
    assert job.lines[0].seq > 1  # the oldest lines are the ones that were skipped
    assert r._using_fallback and r.model_name == "fake-fallback"
    r._run(job)
    assert out.models == ["fake-fallback"] and fast.calls and not slow.calls
    assert r.stats.fallback_windows == 1
    while (job := r._next_job()) is not None:  # catching up switches back to the main model
        r._run(job)
    assert not r._using_fallback


def stop_budget_refiner(rate: float, budget: float = 5.0):
    main, fallback = FakeLongform(), FakeLongform()
    fallback.name = "fake-fallback"
    out = Collector()
    r = Refiner(cfg_refine(window_speech_s=100.0, stop_budget_s=budget), main, out,
                fallback_factory=lambda: fallback)
    r.load()
    assert fallback.loaded  # loaded up front, there is no time for it at stop
    audio = np.concatenate([timeline(*[(i * 3.0 + 0.5, i * 3.0 + 2.5, 0.3) for i in range(3)]),
                            silence(1.0)])
    feed(r, audio)
    for i in range(3):  # 6 s of speech, below the window size: all of it is the stop tail
        r.note_line(seg(i + 1, i * 3.0 + 0.5, i * 3.0 + 2.5))
    r._rate = rate
    return r, main, fallback, out


def test_stop_tail_goes_to_the_fallback_when_the_main_model_would_exceed_the_budget():
    r, main, fallback, out = stop_budget_refiner(rate=2.0)  # 6 s x 2.0 = 12 s > 5 s
    r.start()
    assert r.finish(timeout=10)
    assert fallback.calls and not main.calls and set(out.models) == {"fake-fallback"}
    assert all(c["detect_english"] for c in fallback.calls)


def test_stop_tail_stays_on_the_main_model_within_the_budget():
    r, main, fallback, out = stop_budget_refiner(rate=0.5)  # 6 s x 0.5 = 3 s <= 5 s
    r.start()
    assert r.finish(timeout=10)
    assert main.calls and not fallback.calls and set(out.models) == {"fake-longform"}


def test_stop_gives_up_a_running_main_model_window_for_the_fallback():
    r, main, fallback, out = stop_budget_refiner(rate=2.0)
    r._flushing = True
    job = r._next_job()
    assert job is not None and not r._using_fallback
    r._current = (job, time.perf_counter(), True)  # the main model is decoding this window
    r._plan_tail()
    with pytest.raises(_Requeue):
        r._tick(1.0, 6.0)
    r._current = None
    r._pending.setdefault(job.source, [])[:0] = job.lines  # what the worker loop does
    nxt = r._next_job()
    assert r._using_fallback and [ln.seq for ln in nxt.lines] == [ln.seq for ln in job.lines]


def test_the_measured_speed_ignores_time_spent_waiting_for_the_live_draft():
    busy = {"on": True}
    tr, out = FakeLongform(), Collector()
    r = Refiner(cfg_refine(window_speech_s=1.0, stop_budget_s=0), tr, out,
                live_busy=lambda: busy["on"])
    r.load()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5))
    job = r._next_job()
    threading.Timer(0.4, lambda: busy.update(on=False)).start()
    r._run(job)
    assert r._rate is not None and r._rate < 0.2  # the 0.4 s wait is not decoding time


def test_refiner_yields_to_the_live_draft_between_segments():
    busy = {"on": True}
    tr, out = FakeLongform(), Collector()
    r = Refiner(cfg_refine(window_speech_s=1.0), tr, out, live_busy=lambda: busy["on"])
    r.load()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5))
    job = r._next_job()
    assert job is not None
    started = time.perf_counter()
    threading.Timer(0.3, lambda: busy.update(on=False)).start()
    r._run(job)
    assert time.perf_counter() - started >= 0.25  # waited for the live queue to drain
    assert out.items


def test_refiner_abort_stops_a_running_window():
    tr, out = FakeLongform(), Collector()
    r = Refiner(cfg_refine(window_speech_s=1.0), tr, out, live_busy=lambda: True)
    r.load()
    r.start()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))
    r.note_line(seg(1, 1.0, 2.5))
    time.sleep(0.2)
    r.abort()
    r._thread.join(timeout=5)
    assert not r._thread.is_alive() and out.items == []


def test_a_window_that_left_the_audio_buffer_is_skipped():
    tr, out = FakeLongform(), Collector()
    r = Refiner(cfg_refine(window_speech_s=1.0, buffer_s=2.0), tr, out)
    r.load()
    feed(r, np.concatenate([burst_audio(), silence(1.0)]))  # 10 s in, 2 s kept
    r.note_line(seg(1, 1.0, 2.5))
    job = r._next_job()
    r._run(job)
    assert tr.calls == [] and out.items == []


# --------------------------------------------------------------------------- engine


def engine_session(tmp_path, refine_tr, audio, config=None, **kw):
    cfg = config or replace(Config(), refine=cfg_refine())
    asr = FakeAsr()
    engine = Engine(cfg, asr, [ArraySource("mic", audio)], vad_factory=EnergyVad,
                    sessions_root=tmp_path, refine_transcriber=refine_tr, **kw)
    drafts, revised = [], []
    engine.subscribe(drafts.append)
    engine.subscribe_revisions(revised.append)
    engine.start()
    assert engine.wait(timeout=30)
    return engine, drafts, revised


def test_engine_writes_revisions_next_to_an_untouched_live_transcript(tmp_path):
    audio = np.concatenate([timeline((1, 2.5, 0.3), (4, 5.5, 0.3), (7, 8.5, 0.3), (10, 11.5, 0.3)),
                            silence(1.5)])
    engine, drafts, revised = engine_session(tmp_path, FakeLongform(), audio)
    assert engine.refiner is not None
    engine.stop()
    d = engine.session_dir
    segs = transcript.read_segments(d)
    assert [s.text for s in segs] == [s.text for s in drafts]
    assert all(s.text.startswith("amp") for s in segs)  # the JSONL still has the drafts
    best = transcript.read_best(d)
    assert [s.seq for s in best] == [s.seq for s in segs]
    assert all(s.text.startswith("c") and s.text.endswith("refined") for s in best)
    assert [s.seq for s in revised] == sorted(s.seq for s in revised)  # callback per line
    assert {s.seq for s in revised} == {1, 2, 3, 4}
    md = (d / "transcript.md").read_text(encoding="utf-8")
    assert "amp" not in md and md.count("refined") == 4  # the mirror shows the best text
    meta = transcript.json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert meta["refine"]["revisions"] == 4 and meta["refine"]["model"] == "fake-longform"
    assert all(r.model == "fake-longform" for r in transcript.read_revisions(d))


def test_engine_stop_refines_only_the_tail(tmp_path):
    tr = FakeLongform()
    audio = np.concatenate([timeline((1, 2.5, 0.3), (4, 5.5, 0.3), (7, 8.5, 0.3)), silence(1.5)])
    cfg = replace(Config(), refine=cfg_refine(window_speech_s=100.0))  # never due while running
    engine, _, revised = engine_session(tmp_path, tr, audio, cfg)
    assert tr.calls == [] and revised == []  # nothing refined during the "meeting"
    engine.stop()  # the tail is everything: one window at stop
    assert len(tr.calls) == 1
    assert {s.seq for s in transcript.read_best(engine.session_dir) if s.rev} == {1, 2, 3}


def test_engine_without_a_refine_model_has_no_revisions(tmp_path):
    audio = np.concatenate([timeline((1, 2.5, 0.3)), silence(1.5)])
    engine, _, revised = engine_session(tmp_path, None, audio)
    engine.stop()
    assert engine.refiner is None and revised == []
    assert not (engine.session_dir / "revisions.jsonl").exists()


def test_engine_with_refine_disabled_in_config_ignores_the_model(tmp_path):
    tr = FakeLongform()
    cfg = replace(Config(), refine=replace(cfg_refine(), enabled=False))
    audio = np.concatenate([timeline((1, 2.5, 0.3)), silence(1.5)])
    engine, _, _ = engine_session(tmp_path, tr, audio, cfg)
    engine.stop()
    assert engine.refiner is None and tr.calls == [] and not tr.loaded


def test_engine_survives_a_refine_model_that_cannot_load(tmp_path):
    class Broken(FakeLongform):
        def load(self):
            raise OSError("no model")

    audio = np.concatenate([timeline((1, 2.5, 0.3)), silence(1.5)])
    engine, drafts, _ = engine_session(tmp_path, Broken(), audio)
    engine.stop()
    assert engine.refiner is None and len(drafts) == 1


def test_engine_refine_model_without_longform_mode_is_ignored(tmp_path):
    audio = np.concatenate([timeline((1, 2.5, 0.3)), silence(1.5)])
    engine, drafts, _ = engine_session(tmp_path, FakeAsr(), audio)  # no transcribe_longform
    engine.stop()
    assert engine.refiner is None and len(drafts) == 1


def test_engine_stop_without_drain_aborts_the_refiner(tmp_path):
    tr = FakeLongform(delay=0.5)
    audio = np.concatenate([timeline((1, 2.5, 0.3), (4, 5.5, 0.3)), silence(1.5)])
    cfg = replace(Config(), refine=cfg_refine(window_speech_s=100.0))
    engine, _, _ = engine_session(tmp_path, tr, audio, cfg)
    engine.stop(drain=False)
    assert tr.calls == []  # aborted: the tail was not refined
    meta = transcript.json.loads((engine.session_dir / "session.json").read_text(encoding="utf-8"))
    assert meta["ended_at"] is not None


# --------------------------------------------------------------------------- config


def test_refine_config_defaults_and_validation():
    cfg = Config()
    assert cfg.refine.enabled and cfg.refine.model == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert cfg.refine.window_speech_s == 25.0
    assert cfg.refine.fallback_model == "KBLab/kb-whisper-small"
    assert cfg.refine.stop_budget_s == 12.0 and cfg.refine.detect_english
    with pytest.raises(ConfigError):
        config_from_dict({"refine": {"window_speech_s": 0}})
    with pytest.raises(ConfigError):
        config_from_dict({"refine": {"max_backlog_windows": 5, "skip_after_windows": 2}})
    with pytest.raises(ConfigError):
        config_from_dict({"post": {"diarization": "slow"}})
    assert config_from_dict({"refine": {"enabled": False}}).refine.enabled is False


# --------------------------------------------------------------------------- live diarization


def diar_engine(tmp_path, *, refine_tr, enabled=True, diarize=True):
    from steno.diarize import DiarizeParams
    from steno.livediar import LiveDiarizer

    from .synth import FakeEmbedder
    from .test_livediar import ContentSegmenter

    dz = LiveDiarizer(ContentSegmenter(), FakeEmbedder(noise=0.03), DiarizeParams())
    cfg = replace(Config(), refine=replace(cfg_refine(window_speech_s=100.0), enabled=enabled,
                                           diarize=diarize))
    audio = np.concatenate([timeline((1, 9, 0.3), (12, 20, 0.3), (24, 30, 0.3)), silence(1.5)])
    engine = Engine(cfg, FakeAsr(), [ArraySource("mic", audio)], vad_factory=EnergyVad,
                    sessions_root=tmp_path, refine_transcriber=refine_tr, refine_diarizer=dz)
    engine.start()
    assert engine.wait(timeout=30)
    engine.stop()
    return engine, dz


def test_engine_diarizes_incrementally_and_saves_the_stats_at_stop(tmp_path):
    from steno.livediar import load_stats

    engine, dz = diar_engine(tmp_path, refine_tr=FakeLongform())
    stats = load_stats(engine.session_dir)
    assert stats and stats[0].stream == "mic" and stats[0].embs
    assert dz.windows > 0 and engine.refiner.stats.windows >= 1  # text and speakers refined
    meta = transcript.json.loads((engine.session_dir / "session.json").read_text(encoding="utf-8"))
    assert meta["refine"]["diar_windows"] == dz.windows


def test_diarization_runs_without_a_refine_model(tmp_path):
    from steno.livediar import load_stats

    engine, dz = diar_engine(tmp_path, refine_tr=None)
    assert engine.refiner is not None and load_stats(engine.session_dir) is not None
    assert not (engine.session_dir / "revisions.jsonl").exists()


def test_diarization_off_in_config_writes_no_stats(tmp_path):
    from steno.livediar import load_stats

    engine, dz = diar_engine(tmp_path, refine_tr=FakeLongform(), diarize=False)
    assert load_stats(engine.session_dir) is None and dz.windows == 0


def test_a_diarizer_that_cannot_load_is_dropped_but_recording_goes_on(tmp_path):
    from steno.diarize import DiarizeParams
    from steno.livediar import LiveDiarizer

    from .synth import FakeEmbedder
    from .test_livediar import ContentSegmenter

    class Broken(ContentSegmenter):
        def load(self):
            raise OSError("no model")

    dz = LiveDiarizer(Broken(), FakeEmbedder(), DiarizeParams())
    audio = np.concatenate([timeline((1, 2.5, 0.3)), silence(1.5)])
    engine = Engine(Config(), FakeAsr(), [ArraySource("mic", audio)], vad_factory=EnergyVad,
                    sessions_root=tmp_path, refine_transcriber=FakeLongform(), refine_diarizer=dz)
    seen = []
    engine.subscribe(seen.append)
    engine.start()
    assert engine.wait(timeout=30)
    engine.stop()
    assert len(seen) == 1 and not (engine.session_dir / "diar.bin").exists()
