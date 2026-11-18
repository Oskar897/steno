"""Long-form ASR in `steno finalize`: word assignment, echo rule, modes, prompt pass-through.

Everything runs on fakes: no model download, no audio device, synthetic tones only.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import numpy as np
import pytest

from steno import asr as asr_mod
from steno import finalize as fz
from steno.asr import LongSegment, WhisperTranscriber
from steno.config import (
    DEFAULT_INITIAL_PROMPT,
    AsrConfig,
    Config,
    ConfigError,
    config_from_dict,
)
from steno.profiles import ProfileStore
from steno.transcript import SessionWriter
from steno.windows import Word

from .synth import FakeEmbedder, silence, timeline
from .test_diarize import FakeSegmenter
from .test_finalize import SEL, A, B, FakeTranscriber, deps, make_session


def W(*items):
    return [Word(a, b, t) for a, b, t in items]


def seg(start, end, *tokens, lp=-0.2):
    """A long-form segment whose words are spread evenly over ``[start, end]``."""
    step = (end - start) / len(tokens)
    words = [Word(start + i * step, start + (i + 0.9) * step, t) for i, t in enumerate(tokens)]
    return LongSegment(start, end, " ".join(tokens), words, lp)


class FakeLongform:
    """Backend with a long-form mode: replays scripted segments per stream."""

    name = "fake-longform"

    def __init__(self, script: dict[int, list[LongSegment]] | list[LongSegment],
                 lang: str = "sv"):
        self.script = script
        self.lang = lang
        self.calls: list[dict] = []
        self.loaded = 0

    def load(self):
        self.loaded += 1

    def transcribe_longform(self, audio, *, batch_size=4, beam_size=None, progress=None):
        self.calls.append({"seconds": len(audio) / 16000, "batch_size": batch_size,
                           "beam_size": beam_size})
        segments = self.script
        if isinstance(segments, dict):  # keyed by the order of the call
            segments = segments[len(self.calls) - 1]
        total = len(audio) / 16000
        for s in segments:
            if progress:
                progress(s.end, total)
        return list(segments), self.lang


# --------------------------------------------------------------------------- pure helpers


def test_split_long_words_cuts_at_the_longest_pauses():
    ws = W((0, 1, "a"), (1, 2, "b"), (4, 5, "c"), (5, 6, "d"), (6.2, 7, "e"))
    assert [[w.text for w in g] for g in fz.split_long_words(ws, max_s=5.5)] == [
        ["a", "b"], ["c", "d", "e"]]
    assert fz.split_long_words(ws, max_s=60) == [ws]
    assert fz.split_long_words([], 5) == []


def test_segments_to_lines_shifts_by_the_stream_origin_and_drops_junk():
    loop = seg(5, 9, *(["tack", "så", "mycket"] * 8))
    empty = LongSegment(10, 11, "", [], -0.1)
    dots = LongSegment(12, 13, ".", W((12, 13, ".")), -0.1)
    good = seg(1, 3, "hej", "världen")
    lines = fz.segments_to_lines([good, loop, empty, dots], "mic", "sv", 0.5)
    assert len(lines) == 1
    ln = lines[0]
    assert ln.source == "mic" and ln.text == "hej världen" and ln.lang == "sv"
    assert ln.redone and ln.avg_logprob == -0.2
    assert ln.words[0].start == pytest.approx(1.5)  # words are in session time
    assert ln.t_start == pytest.approx(1.5) and ln.t_end == pytest.approx(ln.words[-1].end)


def test_segments_to_lines_cuts_segments_longer_than_the_line_cap():
    tokens = [f"w{i}" for i in range(60)]
    lines = fz.segments_to_lines([seg(0, 30, *tokens)], "mic", "sv", 0.0)
    assert len(lines) >= 2
    assert all(ln.t_end - ln.t_start <= fz.MAX_MERGED_S for ln in lines)
    assert " ".join(ln.text for ln in lines) == " ".join(tokens)


def test_restrict_line_cuts_where_words_were_removed():
    ws = W((0, 1, "a"), (1, 2, "b"), (2, 3, "c"), (3, 4, "d"))
    ln = fz.Line(0, "mic", 0, 4, "a b c d", "sv", ws, -0.3, True)
    assert fz.restrict_line(ln, ws) == [ln]
    parts = fz.restrict_line(ln, [ws[0], ws[2], ws[3]])
    assert [(p.text, p.t_start, p.t_end) for p in parts] == [("a", 0, 1), ("c d", 2, 4)]
    assert fz.restrict_line(ln, []) == []


def test_nearest_live_seq_prefers_overlap_then_proximity_and_same_stream():
    live = [fz.Line(1, "mic", 0, 4, "x", "sv"), fz.Line(2, "mic", 10, 14, "y", "sv"),
            fz.Line(3, "loopback", 5, 9, "z", "sv")]
    probe = fz.Line(0, "mic", 3, 5, "p", "sv")
    assert fz.nearest_live_seq(probe, live) == 1  # 1 s of overlap with line 1
    assert fz.nearest_live_seq(fz.Line(0, "mic", 8, 9, "p", "sv"), live) == 2  # 1 s away
    assert fz.nearest_live_seq(fz.Line(0, "mic", 30, 31, "p", "sv"), live) == 0  # too far
    assert fz.nearest_live_seq(fz.Line(0, "mic", 6, 7, "p", "sv"), live) == 1  # never loopback


# --------------------------------------------------------------------------- echo rule


def test_echo_rule_keeps_words_outside_loopback_speech_and_inside_live_mic_lines():
    loop = [(10.0, 20.0)]
    live_mic = [(30.0, 40.0), (14.0, 15.0)]  # the second one: loud interjection kept live
    ws = W((5, 6, "before"),  # no loopback speech: new speech the live VAD missed -> keep
           (11, 12, "echo"),  # inside loopback speech, no live mic line -> drop
           (14.2, 14.8, "interjection"),  # live judged it real -> keep
           (15.2, 15.6, "tail"),  # within the pad of that live line -> keep
           (17, 18, "echo2"),  # drop
           (25, 26, "after"),  # past the remote's speech -> keep
           (30.5, 31, "later"))
    kept, echo = fz.drop_echo_words(ws, live_mic, loop)
    assert [w.text for w in echo] == ["echo", "echo2"]
    assert [w.text for w in kept] == ["before", "interjection", "tail", "after", "later"]
    assert fz.drop_echo_words([], live_mic, loop) == ([], [])
    kept, echo = fz.drop_echo_words(ws, live_mic, [])  # nobody else talks: nothing is echo
    assert not echo and len(kept) == len(ws)


def lines_of(*items):
    return [fz.Line(i + 1, src, a, b, "x", "sv") for i, (src, a, b) in enumerate(items)]


def test_longform_lines_drops_mic_echo_but_keeps_real_mic_speech():
    live = lines_of(("loopback", 2.0, 6.0), ("mic", 10.0, 12.0))  # the live pass kept no echo
    audio = {"mic": silence(14), "loopback": silence(14)}
    fake = FakeLongform({
        0: [seg(2.0, 6.0, "hur", "går", "det", "med", "rapporten")],  # loopback goes first
        1: [seg(2.1, 5.9, "hur", "går", "det", "med", "rapporten"),  # leaked into the mic
            seg(10.0, 12.0, "bra", "tack")],
    })
    lines, report = fz.longform_lines(audio, {}, live, fake, batch_size=4, beam_size=1)
    texts = {(ln.source, ln.text) for ln in lines}
    assert texts == {("loopback", "hur går det med rapporten"), ("mic", "bra tack")}
    assert report.echo_dropped == 5 and report.fallback == []
    assert [c["seconds"] for c in fake.calls] == [14, 14]  # one pass per stream
    mic = next(ln for ln in lines if ln.source == "mic")
    assert mic.seq == 2  # label fallback = the overlapping live mic line


def test_a_partly_echoed_segment_is_cut_around_the_echo():
    live = lines_of(("loopback", 4.0, 6.0), ("mic", 2.0, 3.0))
    audio = {"mic": silence(10), "loopback": silence(10)}
    fake = FakeLongform({
        0: [seg(4.0, 6.0, "fjärran", "röst")],
        1: [LongSegment(2.0, 8.0, "a b c d", W((2.0, 2.5, "ja"), (4.2, 4.6, "fjärran"),
                                               (4.8, 5.2, "röst"), (7.0, 7.5, "nej")), -0.2)],
    })
    lines, report = fz.longform_lines(audio, {}, live, fake, batch_size=4, beam_size=1)
    assert [ln.text for ln in lines if ln.source == "mic"] == ["ja", "nej"]
    assert report.echo_dropped == 2


def test_echo_rule_is_off_without_loopback_or_when_disabled():
    live = lines_of(("mic", 10.0, 12.0))
    only_mic = FakeLongform([seg(2.0, 5.0, "ett", "två"), seg(10.0, 12.0, "tre", "fyra")])
    lines, report = fz.longform_lines({"mic": silence(14)}, {}, live, only_mic,
                                      batch_size=4, beam_size=1)
    assert len(lines) == 2 and report.echo_dropped == 0

    live2 = lines_of(("loopback", 2.0, 6.0))
    both = FakeLongform({0: [seg(2.0, 6.0, "ett", "två")], 1: [seg(2.2, 5.8, "ett", "två")]})
    lines, report = fz.longform_lines(
        {"mic": silence(8), "loopback": silence(8)}, {}, live2, both,
        batch_size=4, beam_size=1, echo_enabled=False)
    assert len(lines) == 2 and report.echo_dropped == 0


def test_longform_lines_uses_the_stream_origin_for_every_stream():
    live = lines_of(("mic", 10.5, 12.0))
    fake = FakeLongform([seg(10.0, 12.0, "hej", "du")])
    lines, _ = fz.longform_lines({"mic": silence(14)}, {"mic": 0.5}, live, fake,
                                 batch_size=4, beam_size=1)
    assert lines[0].t_start == pytest.approx(10.5)


def test_streams_without_audio_or_with_a_failed_pass_keep_their_live_lines():
    live = lines_of(("loopback", 2.0, 6.0), ("mic", 8.0, 9.0), ("mic", 10.0, 11.0))
    live[0] = replace(live[0], text="live remote")
    audio = {"mic": silence(14)}  # no kept audio for loopback

    class Boom(FakeLongform):
        def transcribe_longform(self, audio, **kw):
            raise RuntimeError("out of memory")

    lines, report = fz.longform_lines(audio, {}, live, Boom([]), batch_size=4, beam_size=1)
    assert [(ln.source, ln.text) for ln in lines] == [
        ("loopback", "live remote"), ("mic", "x"), ("mic", "x")]
    assert report.fallback == ["mic"]

    quiet = FakeLongform([])  # the model finds no speech although the live pass did
    lines, report = fz.longform_lines(audio, {}, live, quiet, batch_size=4, beam_size=1)
    assert len(lines) == 3 and report.fallback == ["mic"]

    lines, report = fz.longform_lines(audio, {}, [], quiet, batch_size=4, beam_size=1)
    assert lines == [] and report.fallback == []  # no live speech either: silence stays silent


def test_backend_without_longform_mode_is_a_clear_error():
    with pytest.raises(fz.FinalizeError, match="long-form"):
        fz.longform_lines({"mic": silence(3)}, {}, lines_of(("mic", 0, 1)), FakeTranscriber(),
                          batch_size=4, beam_size=1)


def test_longform_progress_counts_audio_seconds_per_stream():
    seen = []
    audio = {"mic": silence(10), "loopback": silence(20)}
    fake = FakeLongform([seg(1, 4, "a", "b"), seg(6, 9, "c", "d")])
    fz.longform_lines(audio, {}, lines_of(("mic", 1, 4)), fake, batch_size=4, beam_size=1,
                      progress=lambda *a: seen.append(a))
    stages = [s for s, *_ in seen]
    assert stages[0] == "transcribing loopback (s)"  # loopback first
    assert {"transcribing mic (s)", "transcribing loopback (s)"} <= set(stages)
    mic = [(d, t) for s, d, t in seen if s == "transcribing mic (s)"]
    assert mic[0] == (0, 10) and mic[-1] == (9, 10)
    assert all(d <= t for _, d, t in seen)


# --------------------------------------------------------------------------- finalize_session


def lf_script():
    """Long-form words for make_session's meeting (A talks 0-9 and 21-28, B 11-19)."""
    return [
        seg(0.6, 8.4, "alpha", "bravo", "charlie", "delta", "echo", "foxtrot"),
        seg(8.2, 13.0, "ten", "eleven", "twelve", "thirteen"),  # crosses A -> B at 9..11
        seg(14.0, 18.0, "india", "juliet", "kilo", "lima"),
        seg(21.6, 27.4, "mike", "november", "oscar"),
    ]


def test_longform_is_the_default_mode_and_a_new_model_is_configured():
    cfg = Config()
    assert cfg.post.asr_mode == "longform"
    assert cfg.post.asr_model == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert cfg.post.longform_beam_size == 1 and cfg.post.longform_batch_size == 4


def test_finalize_longform_replaces_live_text_and_assigns_words_to_turns(tmp_path):
    session = make_session(tmp_path)
    fake = FakeLongform(lf_script())
    seen = []
    before = (session / "transcript.jsonl").read_bytes()
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=deps(fake),
                              progress=lambda *a: seen.append(a))
    assert (session / "transcript.jsonl").read_bytes() == before
    assert res.asr_mode == "longform" and res.longform is not None
    assert len(fake.calls) == 1 and fake.loaded == 1  # one pass over the one kept stream
    assert fake.calls[0]["batch_size"] == 4 and fake.calls[0]["beam_size"] == 1
    assert any(s == "transcribing mic (s)" for s, *_ in seen)

    final = fz.read_final(session)
    text = " ".join(s.text for s in final)
    for old in ("garbled", "ghost", "foxtrot golf"):  # live-only text is gone
        assert old not in text
    assert text.split() == [w for sg in lf_script() for w in sg.text.split()]  # every word once
    by_word = {w[2]: s for s in final for w in (s.words or [])}
    assert by_word["alpha"].speaker == "Alice"  # locked name carried over
    assert by_word["india"].speaker == "Speaker 2"
    assert by_word["mike"].speaker == "Alice"
    # the segment 8.2-13.0 is cut at the speaker change
    assert by_word["ten"].speaker == "Alice" and by_word["thirteen"].speaker == "Speaker 2"
    assert by_word["ten"] is not by_word["thirteen"]
    assert all(s.final and s.words and s.lang == "sv" for s in final)

    meta = json.loads((session / "final.json").read_text(encoding="utf-8"))
    assert meta["asr_mode"] == "longform" and meta["asr_model"] == Config().post.asr_model
    assert meta["longform"]["words"] == 17 and meta["longform"]["echo_words_dropped"] == 0
    assert meta["full_asr"] is False


def test_full_asr_does_not_trigger_the_per_line_pass_in_longform(tmp_path):
    session = make_session(tmp_path)
    fake = FakeLongform(lf_script())
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=deps(fake), full_asr=True)
    assert res.flagged == 0 and len(fake.calls) == 1


def test_mode_switch_selective_off_and_override(tmp_path):
    # selective: the per-line backend runs, the long-form one is never asked
    session = make_session(tmp_path)
    per_line = FakeTranscriber()
    res = fz.finalize_session(session, SEL, ProfileStore(), deps=deps(per_line))
    assert res.asr_mode == "selective" and res.redone == 1 and len(per_line.calls) == 1

    # off: no transcriber is even created
    def forbidden():
        raise AssertionError("ASR must not run in off mode")

    d = deps()
    d.transcriber_factory = forbidden
    res = fz.finalize_session(session, SEL, ProfileStore(), deps=d, asr_mode="off")
    assert res.asr_mode == "off" and res.redone == res.flagged == 0
    final = fz.read_final(session)
    assert any("garbled" in s.text for s in final)  # live words kept
    meta = json.loads((session / "final.json").read_text(encoding="utf-8"))
    assert meta["asr_mode"] == "off" and meta["asr_model"] is None and meta["longform"] is None

    # explicit argument beats the config
    fake = FakeLongform(lf_script())
    res = fz.finalize_session(session, SEL, ProfileStore(), deps=deps(fake), asr_mode="longform")
    assert res.asr_mode == "longform" and len(fake.calls) == 1
    with pytest.raises(fz.FinalizeError, match="unknown ASR mode"):
        fz.finalize_session(session, SEL, ProfileStore(), deps=deps(fake), asr_mode="turbo")


def test_a_model_that_cannot_load_fails_cleanly_without_writing(tmp_path):
    session = make_session(tmp_path)

    class NoModel(FakeLongform):
        def load(self):
            raise OSError("download failed")

    with pytest.raises(fz.FinalizeError, match="could not load"):
        fz.finalize_session(session, Config(), ProfileStore(), deps=deps(NoModel([])))
    assert not fz.has_final(session)


def make_echo_session(tmp_path):
    """mic + loopback. Loopback: a remote sentence at 2-6 s. Mic: the live pass dropped the
    leaked copy of it (no line) and kept the user's own answer at 10-12 s."""
    w = SessionWriter.create(root=tmp_path / "sessions", model="fake",
                             sources=["mic", "loopback"], now=datetime(2026, 1, 1, 10, 0, 0))
    w.append(t_start=2.0, t_end=6.0, source="loopback", speaker="Remote",
             text="remote words here", lang="sv", avg_logprob=-0.2)
    w.append(t_start=10.0, t_end=12.0, source="mic", speaker="Me", text="my answer",
             lang="sv", avg_logprob=-0.2)
    w.close()
    (w.dir / "audio-mic.flac").write_bytes(b"x")
    (w.dir / "audio-loopback.flac").write_bytes(b"x")
    mic = timeline((2.2, 5.8, 0.03, B), (10.0, 12.0, 0.3, A))  # quiet leak + own voice
    loop = timeline((2.0, 6.0, 0.3, B))
    audio = {"mic": np.concatenate([mic, silence(2.0)]), "loopback": np.concatenate(
        [loop, silence(8.0)])}
    d = fz.Deps(
        embedder=FakeEmbedder(noise=0.03),
        segmenter=FakeSegmenter([(2.0, 6.0, 0), (10.0, 12.0, 1)]),
        audio_loader=lambda path: audio[path.stem.removeprefix("audio-")],
    )
    return w.dir, d


def test_finalize_does_not_bring_dropped_mic_echo_back(tmp_path):
    session, d = make_echo_session(tmp_path)
    d.transcriber_factory = lambda: FakeLongform({
        0: [seg(2.0, 6.0, "remote", "words", "here")],
        1: [seg(2.2, 5.8, "remote", "words", "here"), seg(10.0, 12.0, "my", "answer")],
    })
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=d)
    final = fz.read_final(session)
    assert [(s.source, s.text) for s in final] == [
        ("loopback", "remote words here"), ("mic", "my answer")]
    assert res.longform.echo_dropped == 3

    # with the rule off (or in a session without [echo]) the duplicate would be there
    cfg = replace(Config(), echo=replace(Config().echo, enabled=False))
    fz.finalize_session(session, cfg, ProfileStore(), deps=d)
    assert [s.text for s in fz.read_final(session) if s.source == "mic"][0].startswith("remote")


# --------------------------------------------------------------------------- prompt


class FakeModel:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, audio, **kw):
        self.kwargs = kw
        return iter([]), type("Info", (), {"language": "sv"})()


class FakePipeline(FakeModel):
    def transcribe(self, audio, **kw):
        self.kwargs = kw
        word = type("W", (), {"start": 0.0, "end": 0.4, "word": " hej"})()
        s = type("S", (), {"start": 0.0, "end": 1.0, "text": " hej", "words": [word],
                           "avg_logprob": -0.3})()
        return iter([s]), type("Info", (), {"language": "sv"})()


def loaded_whisper(prompt):
    t = WhisperTranscriber("m", initial_prompt=prompt)
    t._model, t._pipeline = FakeModel(), FakePipeline()  # load() is a no-op from here
    return t


def test_initial_prompt_reaches_every_faster_whisper_call():
    audio = np.zeros(16000, dtype=np.float32)
    t = loaded_whisper(DEFAULT_INITIAL_PROMPT)
    t.transcribe(audio)
    assert t._model.kwargs["initial_prompt"] == DEFAULT_INITIAL_PROMPT
    t.transcribe_detailed(audio)
    assert t._pipeline.kwargs["initial_prompt"] == DEFAULT_INITIAL_PROMPT
    t.transcribe_words(audio)
    assert t._pipeline.kwargs["initial_prompt"] == DEFAULT_INITIAL_PROMPT
    segs, lang = t.transcribe_longform(audio, batch_size=3, beam_size=2)
    kw = t._pipeline.kwargs
    assert kw["initial_prompt"] == DEFAULT_INITIAL_PROMPT
    assert kw["batch_size"] == 3 and kw["beam_size"] == 2 and kw["vad_filter"] is True
    assert kw["word_timestamps"] is True and kw["language"] == "sv"
    assert lang == "sv" and segs[0].text == "hej" and segs[0].words[0].text == "hej"
    # an empty prompt means none
    none = loaded_whisper("")
    none.transcribe(audio)
    assert none._model.kwargs["initial_prompt"] is None


def test_prompt_comes_from_the_asr_config_and_defaults_to_the_shared_constant():
    assert AsrConfig().initial_prompt == DEFAULT_INITIAL_PROMPT
    cfg = replace(AsrConfig(), initial_prompt="mitt prompt")
    assert asr_mod.create_transcriber(cfg)._prompt == "mitt prompt"
    assert asr_mod.create_transcriber(AsrConfig())._prompt == DEFAULT_INITIAL_PROMPT
    assert asr_mod.create_transcriber(replace(AsrConfig(), initial_prompt=""))._prompt is None
    cfg2 = config_from_dict({"asr": {"initial_prompt": "x y z"}})
    assert cfg2.asr.initial_prompt == "x y z"


def test_finalize_builds_its_backend_with_post_model_language_and_prompt(monkeypatch):
    made = []
    monkeypatch.setitem(asr_mod.BACKENDS, "faster-whisper", lambda c: made.append(c) or object())
    cfg = replace(Config(), asr=replace(Config().asr, language="sv", initial_prompt="p"))
    fz._default_transcriber(cfg, longform=True)()
    fz._default_transcriber(cfg)()
    long, sel = made
    assert long.model == sel.model == cfg.post.asr_model
    assert long.initial_prompt == sel.initial_prompt == "p" and long.language == "sv"
    assert long.beam_size == cfg.post.longform_beam_size
    assert sel.beam_size == cfg.post.asr_beam_size


def test_default_prompt_is_generic_not_an_eval_sentence():
    # the eval sentences live in scripts/codeswitch_sentences.txt (untracked on purpose)
    from pathlib import Path

    f = Path(__file__).resolve().parents[1] / "scripts" / "codeswitch_sentences.txt"
    if not f.is_file():
        pytest.skip("eval sentences not present")
    lines = [ln.strip().lower() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    prompt = DEFAULT_INITIAL_PROMPT.lower()
    assert not any(ln in prompt for ln in lines)


def test_config_rejects_an_unknown_asr_mode_and_bad_longform_numbers():
    with pytest.raises(ConfigError, match="asr_mode"):
        config_from_dict({"post": {"asr_mode": "turbo"}})
    with pytest.raises(ConfigError):
        config_from_dict({"post": {"longform_batch_size": 0}})
    assert config_from_dict({"post": {"asr_mode": "off"}}).post.asr_mode == "off"
