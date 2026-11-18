from __future__ import annotations

import json
from dataclasses import replace

import numpy as np

from steno import transcript
from steno.audio import ArraySource
from steno.config import Config, EchoConfig
from steno.engine import EchoGate, Engine

from .synth import EnergyVad, FakeAsr, silence, timeline


def run(sources, config=None, **kw):
    asr = FakeAsr()
    engine = Engine(config or Config(), asr, sources, vad_factory=EnergyVad, **kw)
    seen = []
    engine.subscribe(seen.append)
    engine.start()
    assert engine.wait(timeout=20)
    engine.stop()
    return engine, asr, seen


def pad(audio, seconds=1.5):
    return np.concatenate([audio, silence(seconds)])


def test_single_mic_stream_end_to_end():
    audio = pad(timeline((1.0, 2.0, 0.3), (4.0, 5.5, 0.2)))
    engine, asr, seen = run([ArraySource("mic", audio)])

    assert asr.loaded
    segs = transcript.read_segments(engine.session_dir)
    assert len(segs) == 2
    assert int(segs[0].text[3:]) > int(segs[1].text[3:])  # 0.3 burst is louder than 0.2
    assert [s.seq for s in segs] == [1, 2]
    assert all(s.speaker == "Me" and s.source == "mic" and s.lang == "sv" for s in segs)
    assert segs[0].t_start < segs[0].t_end <= segs[1].t_start
    assert abs(segs[0].t_start - 0.8) < 0.1
    assert seen == segs  # subscribers got the same final segments

    meta = json.loads((engine.session_dir / "session.json").read_text(encoding="utf-8"))
    assert meta["ended_at"] is not None
    assert meta["model"] == "fake-asr"
    assert meta["sources"] == ["mic"]
    assert meta["segments"] == 2
    md = (engine.session_dir / "transcript.md").read_text(encoding="utf-8")
    assert f"**[00:00] Me:** {segs[0].text}" in md


def test_speaker_label_override_and_loopback_label():
    audio = pad(timeline((0.5, 1.5, 0.3)))
    engine, _, _ = run([ArraySource("loopback", audio)])
    assert transcript.read_segments(engine.session_dir)[0].speaker == "Remote"

    engine, _, _ = run([ArraySource("loopback", audio)], speaker_labels={"loopback": "Anna"})
    assert transcript.read_segments(engine.session_dir)[0].speaker == "Anna"


def test_both_streams_interleaved_by_time():
    mic = pad(timeline((1.0, 2.0, 0.3), (9.0, 10.0, 0.3)))
    loop = pad(timeline((4.0, 5.0, 0.4)))
    engine, _, _ = run([ArraySource("mic", mic), ArraySource("loopback", loop)])
    segs = transcript.read_segments(engine.session_dir)
    assert [s.source for s in segs] == ["mic", "loopback", "mic"]
    assert [s.speaker for s in segs] == ["Me", "Remote", "Me"]
    assert [s.t_start for s in segs] == sorted(s.t_start for s in segs)


def echo_scenario():
    # loopback speaks 1-3 s and 9-10 s; mic has a quiet echo of the first one,
    # speaks alone 5-6 s, and talks loudly over the second one.
    loop = pad(timeline((1.0, 3.0, 0.5), (9.0, 10.0, 0.5)))
    mic = pad(timeline((1.1, 3.1, 0.05), (5.0, 6.0, 0.4), (9.0, 10.0, 0.5)))
    return [ArraySource("mic", mic), ArraySource("loopback", loop)]


def test_quiet_mic_overlapping_loopback_is_dropped_as_echo():
    engine, _, _ = run(echo_scenario())
    segs = transcript.read_segments(engine.session_dir)
    mic_starts = [round(s.t_start) for s in segs if s.source == "mic"]
    assert mic_starts == [5, 9]  # the echo at ~1 s is gone, real speech kept
    assert sum(s.source == "loopback" for s in segs) == 2


def test_echo_suppression_can_be_disabled():
    cfg = replace(Config(), echo=EchoConfig(enabled=False))
    engine, _, _ = run(echo_scenario(), cfg)
    segs = transcript.read_segments(engine.session_dir)
    assert sum(s.source == "mic" for s in segs) == 3


def test_empty_asr_text_is_not_written():
    class Blank(FakeAsr):
        def transcribe(self, audio):
            return " . ", "sv"

    audio = pad(timeline((0.5, 1.5, 0.3)))
    engine = Engine(Config(), Blank(), [ArraySource("mic", audio)], vad_factory=EnergyVad)
    engine.start()
    engine.wait(20)
    engine.stop()
    assert transcript.read_segments(engine.session_dir) == []


def test_asr_error_does_not_kill_the_worker():
    class Flaky(FakeAsr):
        def transcribe(self, audio):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("boom")
            return super().transcribe(audio)

    audio = pad(timeline((0.5, 1.5, 0.3), (4.0, 5.0, 0.3)))
    engine = Engine(Config(), Flaky(), [ArraySource("mic", audio)], vad_factory=EnergyVad)
    engine.start()
    engine.wait(20)
    engine.stop()
    assert len(transcript.read_segments(engine.session_dir)) == 1


def test_stop_without_drain_still_closes_session():
    audio = pad(timeline((0.5, 1.5, 0.3)))
    engine = Engine(Config(), FakeAsr(), [ArraySource("mic", audio)], vad_factory=EnergyVad)
    engine.start()
    engine.stop(drain=False)
    meta = json.loads((engine.session_dir / "session.json").read_text(encoding="utf-8"))
    assert meta["ended_at"] is not None


def test_echo_gate_logic():
    gate = EchoGate(ratio=0.35)
    gate.note_loopback(10.0, 12.0, 0.2)
    assert gate.is_echo(10.1, 12.1, 0.03)  # quiet and overlapping
    assert not gate.is_echo(10.1, 12.1, 0.15)  # loud enough to be real speech
    assert not gate.is_echo(13.0, 14.0, 0.03)  # no overlap
    assert not gate.is_echo(11.9, 15.0, 0.03)  # overlap < 30 % of its duration
    # a loopback utterance still in progress counts too
    assert gate.is_echo(20.0, 21.0, 0.02, open_loopback=(19.5, 0.3))
    assert not EchoGate(enabled=False).is_echo(10.1, 12.1, 0.0)
