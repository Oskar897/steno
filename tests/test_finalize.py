from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pytest

from steno import finalize as fz
from steno import labels, transcript
from steno.asr import AsrResult
from steno.config import Config
from steno.diarize import DiarizationResult, SpeakerTurn
from steno.profiles import ProfileStore
from steno.transcript import Segment, SessionWriter
from steno.windows import Word

from .synth import FakeEmbedder, silence, timeline
from .test_diarize import TRUTH, FakeSegmenter

A, B = 220.0, 330.0


def words(*items):
    return [Word(a, b, w) for a, b, w in items]


# --------------------------------------------------------------------------- ASR repair


def test_approximate_words_cover_the_line_in_proportion_to_length():
    ws = fz.approximate_words("a bbb cc", 10.0, 20.0)
    assert [w.text for w in ws] == ["a", "bbb", "cc"]
    assert ws[0].start == 10.0 and abs(ws[-1].end - 20.0) < 1e-9
    assert all(a.end == pytest.approx(b.start) for a, b in zip(ws, ws[1:], strict=False))
    assert ws[1].end - ws[1].start > ws[0].end - ws[0].start  # longer word, longer slot
    assert fz.approximate_words("", 0, 1) == []


def test_compression_ratio_flags_loops_but_not_prose():
    loop = "tack så mycket " * 12
    prose = "Vi bestämde att flytta mötet till fredag eftersom Anna är bortrest på torsdag."
    assert fz.compression_ratio(loop) > 2.4
    assert fz.compression_ratio(prose) < 2.4
    assert fz.compression_ratio("") == 0.0


def line(text="hej på dig", lp=None, t0=0.0, t1=3.0, source="mic", seq=1, ws=None):
    return fz.Line(seq, source, t0, t1, text, "sv", ws, lp)


def test_low_confidence_by_logprob_or_repetition():
    assert fz.is_low_confidence(line(lp=-1.2))
    assert not fz.is_low_confidence(line(lp=-0.3))
    assert not fz.is_low_confidence(line(lp=None))  # unknown: not flagged (use --full-asr)
    assert fz.is_low_confidence(line(text="tack så mycket " * 10, lp=None))
    assert not fz.is_low_confidence(line(text="ja", lp=None))  # short text is never "repetitive"


def result(text="ny text här", lp=-0.2, n_words=3):
    ws = [Word(i * 0.4, i * 0.4 + 0.3, f"w{i}") for i in range(n_words)]
    return AsrResult(text, "sv", ws, lp)


KW = dict(min_logprob=-0.7, max_compression=2.4)


def test_accept_reasr_only_when_not_worse():
    bad = line(lp=-1.2)
    assert fz.accept_reasr(bad, result(lp=-0.4), full=False, **KW)
    assert not fz.accept_reasr(bad, result(lp=-1.6), full=False, **KW)  # worse
    assert not fz.accept_reasr(bad, result(text="  . ", lp=-0.1), full=False, **KW)  # empty
    assert not fz.accept_reasr(bad, result(n_words=0), full=False, **KW)  # no words
    loop = AsrResult("tack så mycket " * 10, "sv", [Word(0, 1, "tack")], -0.1)
    assert not fz.accept_reasr(bad, loop, full=False, **KW)  # new text loops


def test_accept_reasr_without_stored_confidence():
    unknown = line(lp=None)
    assert fz.accept_reasr(unknown, result(lp=-0.5), full=True, **KW)  # --full-asr
    assert not fz.accept_reasr(unknown, result(lp=-0.5), full=False, **KW)
    assert fz.accept_reasr(line(lp=-1.0), result(lp=None), full=False, **KW)  # flagged line


def test_clip_words_drops_padding_that_belongs_to_a_neighbour():
    ws = words((9.9, 10.1, "edge"), (10.5, 11.0, "in"), (12.3, 12.6, "next"))
    assert [w.text for w in fz.clip_words(ws, 10.2, 12.0)] == ["in"]


# --------------------------------------------------------------------------- words -> turns


def test_assign_line_words_uses_the_turn_with_most_overlap():
    ws = words((0.0, 1.0, "a"), (1.0, 2.0, "b"), (2.0, 3.0, "c"), (3.0, 4.0, "d"))
    turns = [(0.0, 2.1, "S1"), (2.1, 5.0, "S2")]
    groups = fz.assign_line_words(ws, turns)
    assert [(sid, [w.text for w in g]) for sid, g in groups] == [
        ("S1", ["a", "b"]), ("S2", ["c", "d"])]


def test_word_straddling_a_change_goes_to_the_larger_overlap():
    ws = words((1.5, 2.5, "x"))
    first = fz.assign_line_words(ws, [(0.0, 2.2, "S1"), (2.2, 4.0, "S2")])  # 0.7 s vs 0.3 s
    second = fz.assign_line_words(ws, [(0.0, 1.8, "S1"), (1.8, 4.0, "S2")])
    assert first[0][0] == "S1" and second[0][0] == "S2"


def test_word_in_a_gap_takes_the_nearest_turn_within_reach_else_nothing():
    ws = words((5.0, 5.4, "gap"))
    turns = [(0.0, 4.0, "S1"), (8.0, 9.0, "S2")]
    assert fz.assign_line_words(ws, turns)[0][0] == "S1"  # 0.8 s away vs 2.8 s
    far = words((20.0, 20.4, "lost"))
    assert fz.assign_line_words(far, turns)[0][0] == ""
    assert fz.assign_line_words([], turns) == []


def test_overlapping_turns_each_keep_their_own_words():
    ws = words((0.0, 1.0, "a"), (1.0, 2.0, "b"), (2.0, 3.0, "c"))
    turns = [(0.0, 3.0, "S1"), (1.0, 2.0, "S2")]  # S2 talks inside S1's turn
    got = fz.assign_line_words(ws, turns)
    assert [sid for sid, _ in got][0] == "S1"
    assert "b" in [w.text for sid, g in got if sid in ("S1", "S2") for w in g]


def test_build_final_lines_splits_at_speaker_change_and_orders_by_time():
    live = [
        line("hej du", ws=words((0.0, 0.5, "hej"), (0.5, 1.0, "du")), t0=0.0, t1=1.0, seq=1),
        line("ja då", ws=words((6.0, 6.5, "ja"), (6.5, 7.0, "då")), t0=6.0, t1=7.0, seq=2),
    ]
    turns = {"mic": [(0.0, 0.6, "S1"), (0.6, 8.0, "S2")]}
    out = fz.build_final_lines(live, turns)
    assert [(f.speaker_id, f.text) for f in out] == [
        ("S1", "hej"), ("S2", "du"), ("S2", "ja då")]
    starts = [f.t_start for f in out]
    assert out[1].t_start == 0.5 and starts == sorted(starts)


def test_build_final_lines_for_legacy_lines_without_word_times():
    live = [line("ett två tre fyra", ws=None, t0=0.0, t1=4.0, seq=7)]
    turns = {"mic": [(0.0, 2.0, "S1"), (2.0, 4.0, "S2")]}
    out = fz.build_final_lines(live, turns)
    assert [f.speaker_id for f in out] == ["S1", "S2"]
    assert out[0].words == ()  # approximate times are not stored
    assert " ".join(f.text for f in out) == "ett två tre fyra"
    nothing = fz.build_final_lines(live, {"mic": []})
    assert [f.speaker_id for f in nothing] == ["live:7"]  # no turns: keep the live speaker


def fl(t0, t1, sid, text="x", source="mic"):
    return fz.FinalLine(source, t0, t1, sid, text, "sv")


def test_merge_lines_joins_same_speaker_across_short_pauses_only():
    lines = [fl(0, 1, "S1", "a"), fl(1.5, 2, "S1", "b"), fl(2.2, 3, "S2", "c"),
             fl(5.0, 6, "S2", "d")]
    out = fz.merge_lines(lines, gap_s=1.0)
    assert [(f.speaker_id, f.text) for f in out] == [("S1", "a b"), ("S2", "c"), ("S2", "d")]
    assert out[0].t_start == 0 and out[0].t_end == 2


def test_merge_lines_respects_stream_and_length_cap():
    lines = [fl(0, 1, "S1", "a", "mic"), fl(1.1, 2, "S1", "b", "loopback")]
    assert len(fz.merge_lines(lines, 1.0)) == 2
    long = [fl(i * 5.0, i * 5.0 + 4.9, "S1") for i in range(6)]
    merged = fz.merge_lines(long, gap_s=1.0, max_s=12.0)
    assert len(merged) > 1 and all(m.t_end - m.t_start <= 12.0 for m in merged)


# --------------------------------------------------------------------------- names


def live_name(t0, t1, name, pid=None, locked=True, source="mic"):
    return fz.LiveName(source, t0, t1, name, pid, locked)


def diar(turns, ids, profile_names=None):
    return DiarizationResult(
        turns, ids, profile_names or {}, np.zeros((len(ids), 2), dtype=np.float32), 0
    )


def test_locked_name_goes_to_the_cluster_that_covers_the_named_lines():
    turns = [SpeakerTurn(0, 10, 0, "mic"), SpeakerTurn(10, 20, 1, "mic")]
    voted = fz.vote_names(turns, [live_name(1, 9, "Alice"), live_name(12, 18, "Bob")])
    assert voted[0].name == "Alice" and voted[1].name == "Bob"


def test_a_name_is_used_once_and_short_overlaps_are_ignored():
    turns = [SpeakerTurn(0, 10, 0, "mic"), SpeakerTurn(10, 20, 1, "mic")]
    live = [live_name(0, 9, "Alice"), live_name(10, 11, "Alice")]  # 9 s vs 1 s
    voted = fz.vote_names(turns, live)
    assert list(voted) == [0]
    assert fz.vote_names(turns, [live_name(0, 1.0, "Alice")]) == {}  # under 2 s


def test_manual_names_beat_automatic_profile_matches():
    turns = [SpeakerTurn(0, 10, 0, "mic")]
    live = [live_name(0, 10, "Auto", "11112222", locked=False), live_name(0, 6, "Mine")]
    voted = fz.vote_names(turns, live)
    assert voted[0].name == "Mine" and voted[0].locked


def test_votes_only_count_turns_on_the_same_stream():
    turns = [SpeakerTurn(0, 10, 0, "loopback")]
    assert fz.vote_names(turns, [live_name(0, 10, "Alice", source="mic")]) == {}


def test_final_cluster_names():
    turns = [
        SpeakerTurn(5, 9, 0, "mic"),
        SpeakerTurn(0, 4, 1, "mic"),
        SpeakerTurn(10, 14, 2, "mic"),
        SpeakerTurn(15, 19, 3, "mic"),
    ]
    d = diar(turns, ["S2", "S1", "P:aaaa0000", "S3"], {"aaaa0000": "Carol"})
    live = [live_name(0, 4, "Alice", "bbbb1111")]  # a locked name with a profile id
    names = fz.name_final_clusters(d, live)
    assert names[1] == fz.ClusterName("P:bbbb1111", "Alice", True)
    assert names[2] == fz.ClusterName("P:aaaa0000", "Carol", False)
    assert names[0] == fz.ClusterName("S1", "Speaker 1", False)  # first anonymous speaker heard
    assert names[3] == fz.ClusterName("S2", "Speaker 2", False)


def test_locked_name_without_a_profile_keeps_an_anonymous_id_but_the_name():
    d = diar([SpeakerTurn(0, 10, 0, "mic")], ["S1"])
    names = fz.name_final_clusters(d, [live_name(0, 10, "Alice", None)])
    assert names == [fz.ClusterName("S1", "Alice", True)]


# --------------------------------------------------------------------------- end to end


class FakeTranscriber:
    name = "fake"

    def __init__(self):
        self.calls: list[float] = []

    def load(self):
        pass

    def transcribe_detailed(self, audio, beam_size=None):
        self.calls.append(len(audio) / 16000)
        n = max(1, int(len(audio) / 16000 / 0.5))
        ws = [Word(i * 0.5, i * 0.5 + 0.4, f"redo{i}") for i in range(n)]
        return AsrResult(" ".join(w.text for w in ws), "sv", ws, -0.2)


def make_session(tmp_path):
    root = tmp_path / "sessions"
    w = SessionWriter.create(root=root, model="fake", sources=["mic"],
                             now=datetime(2026, 1, 1, 10, 0, 0))

    def put(t0, t1, text, sid, lp=-0.3, with_words=True, speaker="Speaker 9"):
        n = len(text.split())
        ws = [[t0 + i * (t1 - t0) / n, t0 + (i + 0.9) * (t1 - t0) / n, t] for i, t in
              enumerate(text.split())] if with_words else None
        w.append(t_start=t0, t_end=t1, source="mic", speaker=speaker, text=text, lang="sv",
                 speaker_id=sid, avg_logprob=lp, words=ws)

    put(0.5, 8.5, "alfa bravo charlie delta echo foxtrot golf hotel", "S1")
    put(11.5, 18.5, "india juliet kilo lima mike november oscar", "S2")
    put(21.5, 27.5, "garbled garbled text", "S1", lp=-1.5)  # low confidence -> redone
    put(30.0, 31.0, "ghost words", None, with_words=False, speaker="Me")  # no turn near
    w.close()
    flac = w.dir / "audio-mic.flac"
    flac.write_bytes(b"x")  # presence only; the audio comes from the injected loader
    session_labels = labels.SessionLabels(w.dir)
    session_labels.init()
    session_labels.ensure_cluster("S1")
    session_labels.ensure_cluster("S2")
    session_labels.set_cluster_name("S1", "Alice", None)  # the user named S1
    return w.dir


def deps(transcriber=None):
    audio = np.concatenate([timeline((0.0, 9.0, 0.3, A), (11.0, 19.0, 0.3, B),
                                     (21.0, 28.0, 0.3, A)), silence(4.0)])
    return fz.Deps(
        embedder=FakeEmbedder(noise=0.03),
        segmenter=FakeSegmenter(TRUTH),
        audio_loader=lambda path: audio,
        transcriber_factory=lambda: transcriber or FakeTranscriber(),  # never load a real model
    )


def test_finalize_session_end_to_end(tmp_path):
    session = make_session(tmp_path)
    before = (session / "transcript.jsonl").read_bytes()
    fake = FakeTranscriber()
    progress = []
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=deps(fake),
                              progress=lambda *a: progress.append(a))
    assert (session / "transcript.jsonl").read_bytes() == before  # live JSONL never touched
    final = fz.read_final(session)
    assert final and all(s.final is True for s in final)
    assert [s.seq for s in final] == list(range(1, len(final) + 1))
    assert (session / "final.md").read_text(encoding="utf-8").count("**[") == len(final)
    meta = json.loads((session / "final.json").read_text(encoding="utf-8"))
    assert meta["segments"] == len(final) and meta["asr_redone"] == 1 == res.redone

    by_name = {}
    for s in final:
        by_name.setdefault(s.speaker, []).append(s)
    assert "Alice" in by_name  # the locked name survived the new clustering
    assert any(s.text.startswith("alfa bravo") for s in by_name["Alice"])
    assert any("india juliet" in s.text for s in by_name["Speaker 2"])
    assert not any(s.speaker_id == "S1" and s.speaker == "Speaker 1" for s in final)
    # the low-confidence line was transcribed again and now belongs to Alice
    assert any("redo0" in s.text for s in by_name["Alice"])
    assert not any("garbled" in s.text for s in final)
    assert len(fake.calls) == 1
    # words without any final turn nearby keep their live label
    ghost = next(s for s in final if "ghost" in s.text)
    assert ghost.speaker == "Me" and ghost.speaker_id == "U"
    assert [stage for stage, *_ in progress if stage.startswith("embedding")]
    assert {name for _, name, _ in res.speakers} >= {"Alice", "Speaker 2"}


def test_finalize_full_asr_redoes_every_line_with_audio(tmp_path):
    session = make_session(tmp_path)
    fake = FakeTranscriber()
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=deps(fake), full_asr=True)
    assert res.flagged == 4 and len(fake.calls) == 4  # every live line has audio
    assert all(c >= fz.MIN_REASR_S for c in fake.calls)


def test_finalize_without_kept_audio_fails_cleanly(tmp_path):
    session = make_session(tmp_path)
    (session / "audio-mic.flac").unlink()
    with pytest.raises(fz.FinalizeError, match="keep-audio"):
        fz.finalize_session(session, Config(), ProfileStore(), deps=deps())
    assert not fz.has_final(session)


def test_finalize_post_edit_hook_runs_on_the_final_lines(tmp_path):
    session = make_session(tmp_path)

    def edit(segments):
        out = [Segment(**{**s.__dict__, "text": s.text.replace("alfa", "ALFA")}) for s in segments]
        return out, {"applied": 1, "proposed": 1, "rejected": []}

    res = fz.finalize_session(session, Config(), ProfileStore(), deps=deps(), post_edit=edit)
    assert res.llm_fix == {"applied": 1, "proposed": 1, "rejected": []}
    assert any("ALFA" in s.text for s in fz.read_final(session))
    assert "alfa" in (session / "transcript.jsonl").read_text(encoding="utf-8")  # live untouched


def test_finalize_rejects_a_bad_speaker_count(tmp_path):
    session = make_session(tmp_path)
    with pytest.raises(fz.FinalizeError):
        fz.finalize_session(session, Config(), ProfileStore(), deps=deps(), num_speakers=0)


def test_read_final_roundtrips_words_and_flags(tmp_path):
    session = make_session(tmp_path)
    fz.finalize_session(session, Config(), ProfileStore(), deps=deps(FakeTranscriber()))
    final = fz.read_final(session)
    with_words = [s for s in final if s.words]
    assert with_words and all(len(w) == 3 for s in with_words for w in s.words)
    assert transcript.read_segments(session)[0].final is None  # the live file has no flag
