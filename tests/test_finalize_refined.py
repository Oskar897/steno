"""finalize with the default settings: refined text and fast speakers, no audio, no models."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from steno import cli, transcript
from steno import finalize as fz
from steno.config import Config
from steno.diarize import diarize_windows
from steno.profiles import ProfileStore, SessionWindows

from .synth import voice_vector
from .test_finalize import deps, make_session
from .test_finalize_longform import FakeLongform, lf_script

FAST = replace(Config(), post=replace(Config().post, asr_mode="refined", diarization="fast"))


def add_windows(session, spans, noise=0.04):
    """Window embeddings (1.5 s every 0.75 s) of fake voices: ``(t0, t1, voice index)`` spans."""
    sw = SessionWindows(session)
    seed = 0
    for a, b, voice in spans:
        t = a
        while t + 1.5 <= b + 1e-9:
            seed += 1
            sw.add("mic", t, t + 1.5, voice_vector(voice, noise, seed))
            t += 0.75
    sw.close()


def revise_lines(session, items):
    """Append revisions the way the refine worker does (a writer is reopened for the test)."""
    path = session / "revisions.jsonl"
    with path.open("ab") as fh:
        for rev, (seq, text, words) in enumerate(items, start=1):
            rec = transcript.Revision(rev=rev, seq=seq, text=text, words=words, model="m",
                                      avg_logprob=-0.2)
            fh.write((rec.to_json() + "\n").encode("utf-8"))


def spoken(t0, t1, *texts):
    n = len(texts)
    step = (t1 - t0) / n
    return [[t0 + i * step, t0 + (i + 0.9) * step, t] for i, t in enumerate(texts)]


def refined_session(tmp_path, *, with_audio=False):
    session = make_session(tmp_path)
    if not with_audio:
        (session / "audio-mic.flac").unlink()
    add_windows(session, [(0.5, 8.5, 0), (11.5, 18.5, 1), (21.5, 27.5, 0)])
    first_text = "ny första raden med bättre ord"
    revise_lines(session, [
        (1, first_text, spoken(0.5, 8.5, *first_text.split())),
        (3, "tredje raden rättad", spoken(21.5, 27.5, "tredje", "raden", "rättad")),
    ])
    return session


def no_audio_deps():
    def loader(path):
        raise AssertionError("the fast path must not read audio")

    return fz.Deps(audio_loader=loader)


def test_defaults_are_refined_text_and_fast_speakers(tmp_path):
    session = refined_session(tmp_path)
    before = (session / "transcript.jsonl").read_bytes()
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps())
    assert res.asr_mode == "refined" and res.diarization == "fast" and not res.fell_back
    assert res.revised == 2
    assert (session / "transcript.jsonl").read_bytes() == before
    final = fz.read_final(session)
    text = " ".join(s.text for s in final)
    assert "ny första raden med bättre ord" in text and "tredje raden rättad" in text
    assert "garbled" not in text and "alfa" not in text  # drafts of revised lines are gone
    assert "india juliet" in text  # an unrevised line keeps its draft
    by_name = {}
    for s in final:
        by_name.setdefault(s.speaker, []).append(s)
    assert "Alice" in by_name  # the locked name followed the clustering (voice 0 = S1)
    assert any("ny första" in s.text for s in by_name["Alice"])
    assert any("india" in s.text for s in by_name["Speaker 2"])
    assert all(s.final is True for s in final)
    meta = json.loads((session / "final.json").read_text(encoding="utf-8"))
    assert meta["asr_mode"] == "refined" and meta["diarization_mode"] == "fast"
    assert meta["asr_model"] is None and meta["revised_lines"] == 2
    assert meta["asr_fell_back_to_longform"] is False


def test_words_of_a_revision_follow_the_speaker_turns(tmp_path):
    session = refined_session(tmp_path)
    fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps())
    final = fz.read_final(session)
    first = next(s for s in final if "ny" in s.text.split())
    assert first.words and first.words[0][2] == "ny"  # word times of the revision are kept


def test_a_line_without_revision_and_without_windows_keeps_the_live_label(tmp_path):
    session = make_session(tmp_path)
    (session / "audio-mic.flac").unlink()
    (session / "windows.bin").unlink(missing_ok=True)
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps())
    assert res.diarization == "none" and res.revised == 0
    final = fz.read_final(session)
    assert len(final) == 4 and {s.speaker_id for s in final} == {"U"}
    assert res.speakers  # the speakers table is built from the live labels


def test_session_without_revisions_but_with_audio_falls_back_to_longform(tmp_path):
    session = make_session(tmp_path)  # a kept (placeholder) FLAC and no revisions
    fake = FakeLongform(lf_script())
    d = deps()
    d.transcriber_factory = lambda: fake
    cfg = replace(FAST, post=replace(FAST.post, diarization="thorough"))
    res = fz.finalize_session(session, cfg, ProfileStore(), deps=d)
    assert res.fell_back and res.asr_mode == "longform" and len(fake.calls) == 1
    assert json.loads((session / "final.json").read_text())["asr_fell_back_to_longform"] is True


def test_off_mode_ignores_revisions(tmp_path):
    session = refined_session(tmp_path)
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps(),
                              asr_mode="off")
    text = " ".join(s.text for s in fz.read_final(session))
    assert res.asr_mode == "off" and "alfa bravo" in text and "ny första" not in text


def test_thorough_diarization_still_needs_kept_audio(tmp_path):
    session = refined_session(tmp_path)
    with pytest.raises(fz.FinalizeError, match="keep-audio"):
        fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps(),
                            thorough=True)
    assert not fz.has_final(session)


def test_thorough_with_refined_text_uses_the_audio_only_for_speakers(tmp_path):
    session = refined_session(tmp_path, with_audio=True)
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=deps(), thorough=True)
    assert res.diarization == "thorough" and res.asr_mode == "refined"
    text = " ".join(s.text for s in fz.read_final(session))
    assert "ny första raden" in text


def test_cli_finalize_default_runs_without_audio_and_thorough_needs_it(steno_data_dir, capsys):
    session = refined_session(steno_data_dir)
    assert cli.main(["finalize", session.name]) == 0
    out = capsys.readouterr().out
    assert "[fast]" in out and "refined text (2 of 4 live line(s) revised)" in out
    assert (session / "final.jsonl").is_file()
    assert cli.main(["finalize", session.name, "--thorough"]) == 1


# --------------------------------------------------------------------------- diarize_windows


def stream_windows(plan):
    """``[(stream, t0, t1, voice)]`` -> arrays for ``diarize_windows``."""
    streams, t0, t1, emb = [], [], [], []
    for i, (name, a, b, voice) in enumerate(plan):
        streams.append(name)
        t0.append(a)
        t1.append(b)
        emb.append(voice_vector(voice, 0.04, seed=i))
    return streams, np.array(t0), np.array(t1), np.vstack(emb)


def run_plan(spans, **kw):
    plan = []
    for name, a, b, voice in spans:
        t = a
        while t + 1.5 <= b + 1e-9:
            plan.append((name, t, t + 1.5, voice))
            t += 0.75
    cfg = Config().speakers
    return diarize_windows(*stream_windows(plan), cfg, **kw)


def test_diarize_windows_finds_two_speakers_and_cuts_turns_at_the_change():
    res = run_plan([("mic", 0, 12, 0), ("mic", 12, 24, 1), ("mic", 24, 36, 0)])
    assert len(res.speaker_ids) == 2 and res.n_points > 40
    turns = [t for t in res.turns if t.stream == "mic"]
    assert [t.cluster for t in turns] == [turns[0].cluster, turns[1].cluster, turns[0].cluster]
    assert abs(turns[0].t_end - 12.0) < 1.0 and abs(turns[1].t_end - 24.0) < 1.0
    assert turns[0].t_start == pytest.approx(0.0)  # not pulled back to the start of the stream


def test_diarize_windows_keeps_streams_apart_and_shares_clusters_across_them():
    res = run_plan([("mic", 0, 12, 0), ("loopback", 0, 12, 1), ("mic", 14, 26, 0),
                    ("loopback", 14, 26, 1)])
    by_stream = {s: {t.cluster for t in res.turns if t.stream == s} for s in ("mic", "loopback")}
    assert len(by_stream["mic"]) == 1 and len(by_stream["loopback"]) == 1
    assert by_stream["mic"] != by_stream["loopback"]


def test_diarize_windows_with_a_fixed_speaker_count_and_degenerate_input():
    res = run_plan([("mic", 0, 20, 0), ("mic", 20, 40, 1)], num_speakers=1)
    assert len(res.speaker_ids) == 1
    empty = diarize_windows([], np.zeros(0), np.zeros(0), np.zeros((0, 4)), Config().speakers)
    assert empty.turns == [] and empty.speaker_ids == []
    few = run_plan([("mic", 0, 3, 0)])  # a handful of windows: agglomerative path
    assert len(few.speaker_ids) == 1 and few.turns


# --------------------------------------------------------------------------- incremental stats


def incremental_session(tmp_path):
    """make_session's meeting with a ``diar.bin`` made the way the live engine makes it."""
    from steno.diarize import DiarizeParams
    from steno.livediar import LiveDiarizer

    from .synth import FakeEmbedder, silence, timeline
    from .test_livediar import ContentSegmenter, FakeRing

    session = make_session(tmp_path)
    (session / "audio-mic.flac").unlink()
    audio = np.concatenate([timeline((0.0, 9.0, 0.3, 220.0), (11.0, 19.0, 0.3, 330.0),
                                     (21.0, 28.0, 0.3, 220.0)), silence(4.0)])
    ring = FakeRing(audio)
    ring.grow(60)
    dz = LiveDiarizer(ContentSegmenter(), FakeEmbedder(noise=0.03), DiarizeParams())
    for s in transcript.read_segments(session):
        dz.note_line(s.source, s.t_start, s.t_end)
    dz.finish({"mic": ring})
    assert dz.save(session)
    return session


def test_fast_diarization_uses_the_incremental_stats_when_the_session_has_them(tmp_path):
    session = incremental_session(tmp_path)
    (session / "windows.bin").unlink(missing_ok=True)  # prove it is not the window path
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps())
    assert res.diarization == "fast" and res.diar_source == "incremental"
    final = fz.read_final(session)
    by_name = {}
    for s in final:
        by_name.setdefault(s.speaker, []).append(s)
    assert "Alice" in by_name and len(by_name) >= 2
    assert any(s.text.startswith("alfa bravo") for s in by_name["Alice"])
    other = next(n for n in by_name if n not in ("Alice", "Me"))
    assert any("india juliet" in s.text for s in by_name[other])
    meta = json.loads((session / "final.json").read_text(encoding="utf-8"))
    assert meta["diarization_source"] == "incremental"


def test_the_window_path_is_used_without_incremental_stats(tmp_path):
    session = refined_session(tmp_path)
    res = fz.finalize_session(session, Config(), ProfileStore(), deps=no_audio_deps())
    assert res.diarization == "fast" and res.diar_source == "windows"
