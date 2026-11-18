from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pytest

from steno import cli, labels, ops, transcript
from steno.config import SpeakersConfig
from steno.profiles import ProfileStore, SessionEmbeddings
from steno.transcript import SessionWriter

from .synth import EnergyVad, FakeEmbedder, silence, timeline, voice_vector

ALICE, BOB = 0, 1
# (voice, duration_s, source, live speaker id)
PLAN = [
    (ALICE, 3.0, "mic", "S1"),
    (BOB, 3.0, "mic", "S2"),
    (ALICE, 3.0, "mic", "S3"),  # live clustering split Alice in two
    (BOB, 2.5, "loopback", "S2"),
    (None, 0.5, "mic", "S3"),  # too short for an embedding
    (ALICE, 1.5, "loopback", "S1"),  # embedded but shorter than 2 s
]


def make_session(plan=PLAN, minute=0):
    w = SessionWriter.create(model="fake", sources=["mic", "loopback"],
                             now=datetime(2026, 1, 1, 10, minute, 0))
    emb = SessionEmbeddings(w.dir)
    t = 0.0
    for i, (voice, dur, source, sid) in enumerate(plan):
        seg = w.append(
            t_start=t, t_end=t + dur, source=source, speaker=f"Speaker {sid[1:]}",
            text=f"text{i + 1}", lang="sv", speaker_id=sid, speaker_conf=0.8, provisional=True,
        )
        if voice is not None:
            emb.add(seg.seq, voice_vector(voice, 0.05, seed=i))
        t += dur + 1.0
    emb.close()
    w.close()
    return w.dir


def speakers_by_seq(session_dir):
    segs = labels.resolve_segments(session_dir, transcript.read_segments(session_dir))
    return {s.seq: s.speaker for s in segs}


# --------------------------------------------------------------------------- labels


def test_resolution_prefers_overlay_then_live_id_then_written_label(tmp_path):
    session = make_session()
    sl = labels.SessionLabels(session)
    assert speakers_by_seq(session) == {
        1: "Speaker 1", 2: "Speaker 2", 3: "Speaker 3", 4: "Speaker 2", 5: "Speaker 3",
        6: "Speaker 1",
    }
    sl.set_cluster_name("S2", "Bob", None)
    sl.merge("S3", "S1")  # live clusters S3 -> S1
    names = speakers_by_seq(session)
    assert names[2] == names[4] == "Bob"
    assert names[3] == names[5] == names[1] == "Speaker 1"

    labels.write_overlay(session, {1: {"speaker_id": "S2", "name": "x"}}, created="t")
    assert speakers_by_seq(session)[1] == "Bob"  # overlay wins over the live S1

    # Segments without ids (layer-1 files) keep their written label.
    legacy = SessionWriter.create(model="f", now=datetime(2026, 2, 1))
    legacy.append(t_start=0, t_end=1, source="mic", speaker="Me", text="hej", lang="sv")
    legacy.close()
    assert speakers_by_seq(legacy.dir) == {1: "Me"}


def test_display_rules():
    data = {"clusters": {"S1": {"name": "Alice"}, "S2": {}}, "merged": {"S3": "S1"},
            "profiles": {"abcd1234": "Carol"}}
    d = labels.SessionLabels.display
    assert d(data, "S1") == "Alice" and d(data, "S3") == "Alice"
    assert d(data, "S2") == "Speaker 2"
    assert d(data, "P:abcd1234") == "Carol"
    assert d(data, "P:ffffffff", fallback="Dave") == "Dave"
    assert d(data, "U") == "Unknown"
    assert labels.parse_cluster_spec("Speaker 2") == "S2"
    assert labels.parse_cluster_spec("s12") == "S12"
    assert labels.parse_cluster_spec("Alice") is None


def test_merge_carries_name_over_to_survivor(tmp_path):
    sl = labels.SessionLabels(tmp_path)
    sl.set_cluster_name("S2", "Bob", "11111111")
    sl.merge("S2", "S1")
    data = sl.load()
    assert data["clusters"]["S1"]["name"] == "Bob" and "S2" not in data["clusters"]
    assert data["merged"] == {"S2": "S1"}


# --------------------------------------------------------------------------- name


def test_name_creates_profile_from_long_segments_only():
    session = make_session()
    store = ProfileStore()
    res = ops.name_cluster(session, "Speaker 2", "Bob", store)
    assert res.created and res.cluster_id == "S2"
    assert res.used == 2 and res.skipped == 0  # seq 2 (3 s) and seq 4 (2.5 s)
    bob = store.find_by_name("Bob")
    assert len(bob.embeddings) == 2 and bob.tags == [session.name] * 2

    names = speakers_by_seq(session)
    assert names[2] == names[4] == "Bob" and names[1] == "Speaker 1"
    data = labels.SessionLabels(session).load()
    assert data["clusters"]["S2"] == {"name": "Bob", "profile_id": bob.id}
    assert data["profiles"][bob.id] == "Bob"
    md = (session / "transcript.md").read_text(encoding="utf-8")
    assert "Bob:** text2" in md and "Speaker 1:** text1" in md
    # The append-only JSONL still holds the live label.
    first = json.loads((session / "transcript.jsonl").read_text("utf-8").splitlines()[1])
    assert first["speaker"] == "Speaker 2" and first["speaker_id"] == "S2"


def test_name_extends_existing_profile_from_another_session():
    store = ProfileStore()
    ops.name_cluster(make_session(minute=0), "S2", "Bob", store)
    second = make_session(minute=5)
    res = ops.name_cluster(second, "S2", "bob", store)  # case-insensitive match
    assert not res.created
    bob = store.find_by_name("Bob")
    assert len(bob.embeddings) == 4
    assert len(store.list()) == 1


def test_name_follows_merged_clusters_and_reports_problems():
    session = make_session()
    labels.SessionLabels(session).merge("S3", "S1")
    res = ops.name_cluster(session, "S3", "Alice", ProfileStore())  # S3 now means S1
    # usable: seq 1 and 3 (the merged S3); skipped: seq 5 (no embedding), seq 6 (1.5 s)
    assert res.cluster_id == "S1" and res.used == 2 and res.skipped == 2
    with pytest.raises(ops.OpsError, match="no segments for S9"):
        ops.name_cluster(session, "S9", "X", ProfileStore())
    with pytest.raises(ops.OpsError, match="not a speaker cluster"):
        ops.name_cluster(session, "Alice", "X", ProfileStore())
    with pytest.raises(ops.OpsError, match="empty"):
        ops.name_cluster(session, "S2", "  ", ProfileStore())


def test_name_requires_a_long_embedded_segment():
    session = make_session([(ALICE, 1.5, "mic", "S1"), (None, 0.5, "mic", "S1")])
    with pytest.raises(ops.OpsError, match="at least 2 s"):
        ops.name_cluster(session, "S1", "Alice", ProfileStore())
    assert ProfileStore().list() == []


# --------------------------------------------------------------------------- refine


def test_refine_merges_split_clusters_and_matches_profiles():
    session = make_session()
    jsonl_before = (session / "transcript.jsonl").read_bytes()
    store = ProfileStore()
    alice = store.create("Alice", np.vstack([voice_vector(ALICE, 0.05, 90 + s) for s in range(4)]))

    res = ops.refine_session(session, SpeakersConfig(), store)
    assert res.segments == 6 and res.embedded == 5
    names = speakers_by_seq(session)
    # Alice: seq 1, 3 (one cluster now, matched to the profile) and 6.
    assert names[1] == names[3] == names[6] == "Alice"
    assert names[2] == names[4] == "Speaker 1"  # Bob is unknown: first unmatched cluster
    # seq 5 (short, mic) inherits the previous mic segment (seq 3, Alice).
    assert names[5] == "Alice"
    overlay = labels.read_overlay(session)
    assert overlay[1]["speaker_id"] == f"P:{alice.id}" and overlay[2]["speaker_id"] == "S1"
    assert overlay[5]["conf"] == 0.0 and 0.5 < overlay[1]["conf"] <= 1.0
    assert (session / "transcript.jsonl").read_bytes() == jsonl_before  # untouched
    md = (session / "transcript.md").read_text(encoding="utf-8")
    assert md.count("Alice:**") == 4 and md.count("Speaker 1:**") == 2
    assert sorted(n for _, n, _ in res.speakers) == ["Alice", "Speaker 1"]


def test_name_after_refine_addresses_refined_clusters_and_survives_rerefine():
    session = make_session()
    store = ProfileStore()
    ops.refine_session(session, SpeakersConfig(), store)
    # Without profiles: Alice -> S1, Bob -> S2 (order of first appearance).
    assert speakers_by_seq(session)[1] == "Speaker 1" and speakers_by_seq(session)[2] == "Speaker 2"
    ops.name_cluster(session, "S2", "Bob", store)
    assert speakers_by_seq(session)[4] == "Bob"
    ops.refine_session(session, SpeakersConfig(), store)  # re-run: Bob is now an enrolled profile
    names = speakers_by_seq(session)
    assert names[2] == names[4] == "Bob" and names[1] == "Speaker 1"
    overlay = labels.read_overlay(session)
    assert overlay[2]["speaker_id"].startswith("P:")


def test_refine_threshold_controls_cluster_count():
    session = make_session()
    strict = SpeakersConfig(refine_threshold=0.9999)  # nothing is that similar
    res = ops.refine_session(session, strict, ProfileStore())
    assert len({sid for sid, _, _ in res.speakers}) >= 5


def test_refine_needs_embeddings_and_segments():
    empty = SessionWriter.create(model="f", now=datetime(2026, 3, 1))
    empty.close()
    with pytest.raises(ops.OpsError, match="no segments"):
        ops.refine_session(empty.dir, SpeakersConfig(), ProfileStore())
    plain = SessionWriter.create(model="f", now=datetime(2026, 3, 2))
    plain.append(t_start=0, t_end=1, source="mic", speaker="Me", text="hej", lang="sv")
    plain.close()
    with pytest.raises(ops.OpsError, match="no speaker embeddings"):
        ops.refine_session(plain.dir, SpeakersConfig(), ProfileStore())


def test_refine_leading_short_segment_takes_next_segment_of_its_stream():
    session = make_session([(None, 0.5, "mic", "S1"), (ALICE, 3.0, "mic", "S1")])
    ops.refine_session(session, SpeakersConfig(), ProfileStore())
    names = speakers_by_seq(session)
    assert names[1] == names[2] == "Speaker 1"


# --------------------------------------------------------------------------- CLI


def test_new_resolves_names_assigned_after_writing(capsys):
    session = make_session()
    ops.name_cluster(session, "S2", "Bob", ProfileStore())
    assert cli.main(["new", "--session", session.name, "--peek"]) == 0
    out = capsys.readouterr().out
    assert "**[00:04] Bob:** text2" in out
    assert "**[00:00] Speaker 1:** text1" in out


def test_cli_name_refine_and_people(capsys):
    session = make_session()
    assert cli.main(["name", "latest", "S2", "Bob"]) == 0
    assert "Bob" in capsys.readouterr().out
    assert cli.main(["name", "latest", "S42", "Zed"]) == 1
    assert "no segments" in capsys.readouterr().err
    assert cli.main(["name", "nope", "S1", "Zed"]) == 1

    assert cli.main(["people", "list"]) == 0
    assert "Bob" in capsys.readouterr().out

    assert cli.main(["people", "rename", "Bob", "Robert"]) == 0
    assert "1 session(s)" in capsys.readouterr().out
    assert speakers_by_seq(session)[2] == "Robert"
    assert "Robert:** text2" in (session / "transcript.md").read_text(encoding="utf-8")
    assert cli.main(["people", "rename", "Nobody", "X"]) == 1
    capsys.readouterr()

    assert cli.main(["refine"]) == 0
    assert "Robert" in capsys.readouterr().out
    assert cli.main(["people", "delete", "Robert"]) == 0
    assert cli.main(["people", "delete", "Robert"]) == 1
    capsys.readouterr()
    assert ProfileStore().list() == []


def test_cli_refine_without_embeddings_reports_error(capsys):
    w = SessionWriter.create(model="f", now=datetime(2026, 4, 1))
    w.append(t_start=0, t_end=1, source="mic", speaker="Me", text="hej", lang="sv")
    w.close()
    assert cli.main(["refine"]) == 1
    assert "no speaker embeddings" in capsys.readouterr().err


# --------------------------------------------------------------------------- enroll


def test_enroll_audio_creates_then_extends_profile():
    store = ProfileStore()
    audio = np.concatenate([timeline((0.5, 3.0, 0.3, 220.0)), silence(1.0),
                            timeline((0.2, 2.5, 0.3, 220.0)), silence(1.0)])
    first = ops.enroll_audio(audio, "Alice", store, FakeEmbedder(), vad_factory=EnergyVad)
    assert first.created and first.used == first.total >= 2  # windows, not whole chunks
    assert first.speech_s > 4.0 and first.dropped == 0
    again = ops.enroll_audio(audio, "alice", store, FakeEmbedder(), vad_factory=EnergyVad)
    assert not again.created and again.total == 2 * first.total
    assert len(store.list()) == 1

    with pytest.raises(ops.OpsError, match="no speech"):
        ops.enroll_audio(silence(5.0), "Bob", store, FakeEmbedder(), vad_factory=EnergyVad)
    assert store.find_by_name("Bob") is None


# --------------------------------------------------------------------------- refine on windows


def make_window_session(voices, seg_s=5.0, cough_seq=None, minute=30):
    """One mic stream, one segment per entry of ``voices``; windows every 0.75 s inside each.

    Live labels are deliberately fragmented (every segment its own cluster).
    A voice of ``None`` is a 3-window noise burst (``cough``).
    """
    from steno.profiles import SessionWindows

    w = SessionWriter.create(model="fake", sources=["mic"],
                             now=datetime(2026, 1, 1, 11, minute, 0))
    wins = SessionWindows(w.dir)
    t = 0.0
    for i, voice in enumerate(voices):
        dur = 2.5 if voice is None else seg_s
        w.append(t_start=t, t_end=t + dur, source="mic", speaker=f"Speaker {i + 1}",
                 text=f"t{i + 1}", lang="sv", speaker_id=f"S{i + 1}", speaker_conf=0.5,
                 provisional=True)
        n = 3 if voice is None else int((dur - 1.5) / 0.75) + 1
        for k in range(n):
            vec = voice_vector(2 if voice is None else voice, 0.15, seed=1000 * i + k)
            wins.add("mic", t + 0.75 * k, t + 0.75 * k + 1.5, vec)
        t += dur + 0.5
    wins.close()
    w.close()
    return w.dir


def test_refine_clusters_windows_with_spectral_and_votes_per_segment():
    voices = [ALICE, BOB, ALICE, ALICE, BOB, ALICE, BOB, BOB, ALICE, BOB, ALICE, BOB]
    session = make_window_session(voices)
    res = ops.refine_session(session, SpeakersConfig(), ProfileStore())
    names = speakers_by_seq(session)
    first = {v: names[voices.index(v) + 1] for v in (ALICE, BOB)}
    assert first[ALICE] != first[BOB]
    assert all(names[i + 1] == first[v] for i, v in enumerate(voices))
    assert len(res.speakers) == 2 and res.embedded == len(voices)


def test_refine_reassigns_a_minor_cluster_and_honours_a_forced_count():
    voices = [ALICE, BOB, ALICE, BOB, None, ALICE, BOB, ALICE, BOB, ALICE, BOB]
    session = make_window_session(voices, minute=31)
    res = ops.refine_session(session, SpeakersConfig(), ProfileStore())
    assert len(res.speakers) == 2  # the 3-window burst is not a speaker
    one = ops.refine_session(session, SpeakersConfig(), ProfileStore(), num_speakers=1)
    assert len(one.speakers) == 1
    with pytest.raises(ops.OpsError, match="at least 1"):
        ops.refine_session(session, SpeakersConfig(), ProfileStore(), num_speakers=0)


def test_refine_with_windows_matches_profiles_by_cluster_centroid():
    voices = [ALICE, BOB] * 8
    session = make_window_session(voices, minute=32)
    store = ProfileStore()
    bob = store.create("Bob", np.vstack([voice_vector(BOB, 0.05, 90 + s) for s in range(4)]))
    ops.refine_session(session, SpeakersConfig(), store)
    names = speakers_by_seq(session)
    assert names[2] == names[16] == "Bob" and names[1] == "Speaker 1"
    assert labels.read_overlay(session)[2]["speaker_id"] == f"P:{bob.id}"


def test_refine_cli_speakers_option(capsys):
    session = make_window_session([ALICE, BOB] * 8, minute=33)
    assert cli.main(["refine", session.name, "--speakers", "1"]) == 0
    out = capsys.readouterr().out
    assert "1 speaker(s)" in out


# --------------------------------------------------------------------------- windows store, enrol


def test_session_windows_roundtrip_and_append_after_reopen(tmp_path):
    from steno.profiles import SessionWindows

    assert SessionWindows.load(tmp_path) is None
    sw = SessionWindows(tmp_path, flush_every=2)
    sw.add("mic", 0.0, 1.5, voice_vector(0))
    sw.add("loopback", 1.0, 2.5, voice_vector(1))
    sw.add("mic", 2.0, 3.5, voice_vector(2))
    sw.close()
    again = SessionWindows(tmp_path)  # reopening continues the same file
    again.add("mic", 4.0, 5.5, voice_vector(3))
    again.close()
    ws = SessionWindows.load(tmp_path)
    assert len(ws) == 4 and ws.stream == ["mic", "loopback", "mic", "mic"]
    assert ws.t0.tolist() == [0.0, 1.0, 2.0, 4.0] and ws.t1[-1] == 5.5
    assert np.allclose(ws.emb[1], voice_vector(1))
    raw = (tmp_path / "windows.bin").read_bytes()
    assert raw.startswith((b"STENO-DPAPI1", b"STENO-PLAIN1"))  # same protected container


def test_drop_outliers_removes_the_far_embeddings_only():
    good = np.vstack([voice_vector(0, 0.05, s) for s in range(12)])
    cough = voice_vector(3)[None, :]
    keep = ops.drop_outliers(np.vstack([good, cough]), sigma=2.0)
    assert keep[:-1].all() and not keep[-1]
    assert ops.drop_outliers(np.vstack([good[:3], cough]), 2.0).all()  # too few to judge
    assert ops.drop_outliers(np.vstack([good, cough]), 0.0).all()  # disabled


def test_enroll_uses_overlapping_windows_over_long_speech():
    store = ProfileStore()
    audio = np.concatenate([timeline((0.5, 8.5, 0.3, 220.0)), silence(1.0)])
    res = ops.enroll_audio(audio, "Alice", store, FakeEmbedder(), vad_factory=EnergyVad,
                           window_s=2.5, hop_s=1.0)
    assert res.used >= 6  # one 8 s utterance -> many 2.5 s windows
    assert 7.5 < res.speech_s < 9.0
    assert len(store.find_by_name("Alice").embeddings) == res.total
