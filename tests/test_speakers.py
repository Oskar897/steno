from __future__ import annotations

import numpy as np

from steno.config import SpeakersConfig
from steno.labels import SessionLabels
from steno.profiles import Profile, ProfileStore
from steno.speakers import (
    ProfileMatcher,
    SessionClusterer,
    SpeakerTracker,
    agglomerative_average,
    cosine,
    l2_normalize,
    profile_score,
)

from .synth import SR, FakeEmbedder, tone, voice_vector


def unit(*values):
    return l2_normalize(np.array(values, dtype=np.float32))


def profile(name, *vectors, pid="00000001"):
    return Profile(pid, name, np.vstack([l2_normalize(v) for v in vectors]))


def at_angle(base, other, cos):
    """Unit vector with cosine ``cos`` to ``base`` (``other`` must be orthogonal to it)."""
    return l2_normalize(cos * base + np.sqrt(1 - cos**2) * other)


# --------------------------------------------------------------------------- matching


def test_profile_score_is_max_of_centroid_and_top3():
    a, b = unit(1, 0, 0), unit(0, 1, 0)
    p = profile("Alice", a, a, a, b, b, b, b, b)  # centroid leans to b, but top-3 are all a
    centroid_cos = float(p.centroid() @ a)
    assert centroid_cos < 0.6
    assert abs(profile_score(p, a) - 1.0) < 1e-6  # top-3 mean wins
    p2 = profile("Bob", unit(1, 0.2, 0), unit(1, -0.2, 0))
    assert profile_score(p2, a) >= float(p2.centroid() @ a) - 1e-6


def test_accept_threshold_and_margin():
    x, y = unit(1, 0, 0), unit(0, 1, 0)
    alice, bob = profile("Alice", x, pid="00000001"), profile("Bob", y, pid="00000002")
    m = ProfileMatcher([alice, bob], accept_threshold=0.55, margin=0.05)

    hit = m.match(x)
    assert hit.accepted and hit.profile.name == "Alice" and hit.score > 0.99

    z = unit(0, 0, 1)
    weak = m.match(at_angle(x, z, 0.4))  # best 0.40 < threshold
    assert not weak.accepted and weak.profile.name == "Alice"

    # Between two similar people: high score but margin too small -> rejected.
    twin = profile("Alice2", at_angle(x, y, 0.95), pid="00000003")
    m2 = ProfileMatcher([alice, twin], accept_threshold=0.55, margin=0.05)
    ambiguous = m2.match(at_angle(x, y, 0.975))
    assert ambiguous.score >= 0.55 and (ambiguous.score - ambiguous.second) < 0.05
    assert not ambiguous.accepted
    assert m2.match(x).accepted  # a clearly closer sample passes the margin


def test_matcher_without_profiles_or_with_single_profile():
    assert not ProfileMatcher([], 0.5, 0.05).match(unit(1, 0)).accepted
    only = ProfileMatcher([profile("Alice", unit(1, 0))], 0.5, 0.05)
    res = only.match(unit(1, 0))
    assert res.accepted and res.second == -1.0  # no runner-up: margin is not an obstacle


# --------------------------------------------------------------------------- leader-follower


def test_leader_follower_joins_or_opens_clusters():
    c = SessionClusterer(cluster_threshold=0.5, merge_threshold=0.7)
    assert c.assign(voice_vector(0))[0] == "S1"
    assert c.assign(voice_vector(1))[0] == "S2"
    cid, score = c.assign(voice_vector(0, noise=0.1, seed=3))
    assert cid == "S1" and score > 0.9
    assert c.count("S1") == 2 and c.count("S2") == 1
    assert c.cluster_ids == ["S1", "S2"]


def test_new_cluster_score_is_similarity_to_nearest_existing():
    c = SessionClusterer(cluster_threshold=0.5)
    a, b = voice_vector(0), voice_vector(1)
    assert c.assign(a) == ("S1", 0.0)
    cid, score = c.assign(at_angle(a, b, 0.3))
    assert cid == "S2" and abs(score - 0.3) < 1e-5


def test_clusters_merge_when_centroids_converge():
    c = SessionClusterer(cluster_threshold=0.5, merge_threshold=0.7)
    a, b = voice_vector(0), voice_vector(1)
    assert c.assign(a)[0] == "S1"
    assert c.assign(at_angle(a, b, 0.2))[0] == "S2"  # far from S1: own cluster
    # Samples in between pull both centroids together until they merge.
    last = None
    for _ in range(12):
        last = c.assign(at_angle(a, b, 0.75))[0]
        if c.cluster_ids == ["S1"]:
            break
    assert c.cluster_ids == ["S1"]
    assert last == "S1"
    assert c.resolve("S2") == "S1"
    assert ("S2", "S1") in c.pop_merges()
    assert c.pop_merges() == []
    assert c.count("S1") >= 3


# --------------------------------------------------------------------------- agglomerative


def test_agglomerative_average_groups_by_speaker():
    order = [0, 1, 0, 2, 1, 0, 2, 2, 1]
    emb = np.vstack([voice_vector(k, noise=0.1, seed=i) for i, k in enumerate(order)])
    labels = agglomerative_average(emb, threshold=0.45)
    assert labels.tolist() == [0, 1, 0, 2, 1, 0, 2, 2, 1]  # numbered by first appearance


def test_agglomerative_threshold_controls_granularity():
    emb = np.vstack([voice_vector(0, 0.1, 1), voice_vector(0, 0.1, 2), voice_vector(1, 0.1, 3)])
    assert agglomerative_average(emb, 0.999).tolist() == [0, 1, 2]  # nothing that similar
    assert agglomerative_average(emb, -1.0).tolist() == [0, 0, 0]  # everything merges
    assert agglomerative_average(np.zeros((0, 4)), 0.5).tolist() == []
    assert agglomerative_average(np.ones((1, 4)), 0.5).tolist() == [0]


def test_agglomerative_uses_average_not_single_linkage():
    # Chain at 0, 40 and 80 degrees: neighbours are similar (cos 0.82 and 0.71), the ends are not
    # (cos 0.17). Single linkage would chain all three; average linkage must not.
    pts = [np.array([np.cos(np.radians(d)), np.sin(np.radians(d))]) for d in (0, 35, 80)]
    labels = agglomerative_average(np.vstack(pts), threshold=0.6)
    assert labels.tolist() == [0, 0, 1]


# --------------------------------------------------------------------------- tracker


def cfg(**kw):
    return SpeakersConfig(**kw)


def speech(freq, seconds=1.5):
    return tone(seconds, 0.3, freq)


def test_tracker_short_segment_inherits_previous_speaker_with_zero_confidence():
    emb = FakeEmbedder()
    t = SpeakerTracker(emb, cfg())
    first = t.identify("mic", speech(220.0))
    assert (first.speaker_id, first.name, first.provisional) == ("S1", "Speaker 1", True)
    short = t.identify("mic", speech(330.0, 0.5))  # < 1.0 s: no embedding
    assert short.speaker_id == "S1" and short.conf == 0.0 and short.provisional
    assert short.embedding is None
    assert len(emb.calls) == 1
    other = t.identify("loopback", speech(330.0, 0.5))  # no history on that stream
    assert (other.speaker_id, other.name) == ("U", "Unknown")


def test_tracker_never_enrols():
    store = ProfileStore()
    t = SpeakerTracker(FakeEmbedder(), cfg(), store)
    for _ in range(5):
        t.identify("mic", speech(220.0))
    assert store.list() == []


def test_tracker_matches_profile_then_clusters_the_rest():
    store = ProfileStore()
    alice = store.create("Alice", np.vstack([voice_vector(0, 0.05, s) for s in range(4)]))
    t = SpeakerTracker(FakeEmbedder(), cfg(), store)
    a = t.identify("mic", speech(220.0))
    assert a.speaker_id == f"P:{alice.id}" and a.name == "Alice" and not a.provisional
    b = t.identify("mic", speech(330.0))
    assert b.speaker_id == "S1" and b.name == "Speaker 1" and b.provisional


def test_tracker_picks_up_profiles_created_during_the_session(tmp_path):
    now = [0.0]
    store = ProfileStore()
    labels = SessionLabels(tmp_path)
    t = SpeakerTracker(FakeEmbedder(), cfg(), store, labels, clock=lambda: now[0],
                       reload_interval_s=5.0)
    assert t.identify("mic", speech(330.0)).speaker_id == "S1"
    bob = store.create("Bob", np.vstack([voice_vector(1, 0.05, s) for s in range(3)]))
    now[0] = 1.0  # too early to look again
    assert t.identify("mic", speech(330.0)).speaker_id == "S1"
    now[0] = 6.0
    after = t.identify("mic", speech(330.0))
    assert after.speaker_id == f"P:{bob.id}" and after.name == "Bob"
    assert labels.load()["profiles"][bob.id] == "Bob"


def test_tracker_uses_cluster_names_set_during_session(tmp_path):
    labels = SessionLabels(tmp_path)
    t = SpeakerTracker(FakeEmbedder(), cfg(), None, labels)
    assert t.identify("mic", speech(220.0)).name == "Speaker 1"
    labels.set_cluster_name("S1", "Alice", None)
    assert t.identify("mic", speech(220.0)).name == "Alice"
    assert t.identify("mic", speech(220.0, 0.4)).name == "Alice"  # inherited


def test_embedding_failure_falls_back_to_inherit():
    class Broken(FakeEmbedder):
        def embed(self, audio):
            raise RuntimeError("model crashed")

    label = SpeakerTracker(Broken(), cfg()).identify("mic", speech(220.0))
    assert label.speaker_id == "U" and label.embedding is None


def test_cosine_helper():
    assert abs(cosine(np.array([2.0, 0]), np.array([5.0, 0])) - 1.0) < 1e-6
    assert SR == 16000


# ----------------------------------------------------------------------- anti-fragmentation gate


def gated(**kw):
    base = {"cluster_threshold": 0.5, "merge_threshold": 0.9, "assign_floor": 0.3,
            "confirm_s": 3.5, "confirm_segments": 2, "ttl_segments": 3}
    return SessionClusterer(**{**base, **kw})


def test_gate_label_only_between_floor_and_threshold():
    c = gated()
    a, b = voice_vector(0), voice_vector(1)
    assert c.observe(a, 4.0, 1)[0] == "S1"
    uncertain = at_angle(a, b, 0.4)  # above the floor, below the join threshold
    cid, score = c.observe(uncertain, 1.0, 2)
    assert cid == "S1" and abs(score - 0.4) < 1e-5
    assert c.count("S1") == 1 and c.speech_s("S1") == 4.0  # the centroid did not move
    assert c.observe(at_angle(a, b, 0.8), 1.0, 2)[0] == "S1"
    assert c.count("S1") == 2  # a confident window does join


def test_gate_noise_cluster_is_merged_away_at_the_end():
    c = gated()
    a, b = voice_vector(0), voice_vector(1)
    c.observe(a, 4.0, 1)
    c.observe(a, 4.0, 2)
    cid, _ = c.observe(b, 1.5, 3)  # a one-off noise: far from everybody
    assert cid == "S2" and not c.confirmed("S2") and c.confirmed("S1")
    c.settle()  # not expired yet
    assert c.cluster_ids == ["S1", "S2"]
    c.settle(final=True)
    assert c.cluster_ids == ["S1"] and c.resolve("S2") == "S1"
    assert c.pop_merges() == [("S2", "S1")]


def test_gate_new_speaker_is_confirmed_by_enough_speech_from_two_segments():
    c = gated()
    a, b = voice_vector(0), voice_vector(1)
    c.observe(a, 4.0, 1)
    c.observe(a, 4.0, 2)
    c.observe(b, 2.0, 3)
    assert not c.confirmed("S2")  # 2 s from one segment
    c.observe(voice_vector(1, 0.05, 4), 2.0, 4)
    assert c.confirmed("S2")
    c.settle(final=True)
    assert c.cluster_ids == ["S1", "S2"] and c.pop_merges() == []


def test_gate_one_long_monologue_alone_is_not_confirmed_but_never_lost():
    c = gated()
    c.observe(voice_vector(0), 10.0, 1)  # one segment only: below confirm_segments
    assert not c.confirmed("S1")
    c.settle(final=True)  # nothing confirmed: the best-supported cluster stays
    assert c.cluster_ids == ["S1"]


def test_gate_expired_tentative_cluster_merges_into_the_nearest_confirmed_one():
    c = gated(ttl_segments=2)
    a, b = voice_vector(0), voice_vector(1)
    for _ in (1, 2):
        c.begin_segment()
        c.observe(a, 4.0)
    c.begin_segment()
    assert c.observe(b, 1.0)[0] == "S2"
    c.begin_segment()  # one segment old
    assert c.cluster_ids == ["S1", "S2"]
    c.begin_segment()  # two segments untouched: expired
    assert c.cluster_ids == ["S1"] and c.resolve("S2") == "S1"


def test_gate_max_speakers_assigns_to_nearest_instead_of_opening_another():
    c = gated(max_speakers=2, confirm_s=0.0, confirm_segments=1)
    c.observe(voice_vector(0), 2.0, 1)
    c.observe(voice_vector(1), 2.0, 2)
    cid, _ = c.observe(voice_vector(2), 2.0, 3)
    assert cid in ("S1", "S2") and c.cluster_ids == ["S1", "S2"]


# --------------------------------------------------------------------------- turns inside a segment


def tones(*parts):
    """Concatenate tone bursts: (freq, seconds) pairs."""
    return np.concatenate([tone(sec, 0.3, freq) for freq, sec in parts])


def turn_cfg(**kw):
    return SpeakersConfig(**{"snap_boundaries": False, **kw})


def test_interjection_inside_a_long_segment_becomes_its_own_turn(tmp_path):
    labels = SessionLabels(tmp_path)
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), None, labels)
    audio = tones((220.0, 4.0), (330.0, 2.0), (220.0, 4.0))  # A, B interjects, A again
    turns = t.identify_turns("mic", audio)
    ids = [x.label.speaker_id for x in turns]
    assert ids == ["S1", "S2", "S1"]
    assert turns[0].t_start == 0.0 and turns[-1].t_end == 10.0
    assert all(a.t_end == b.t_start for a, b in zip(turns, turns[1:], strict=False))
    assert 3.0 <= turns[0].t_end <= 4.5 and 5.5 <= turns[1].t_end <= 7.0
    assert all(x.label.provisional and x.label.embedding is not None for x in turns)


def test_single_window_flip_is_smoothed_away():
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), None)
    audio = tones((220.0, 4.0), (330.0, 0.6), (220.0, 4.0))  # a blip shorter than a window
    assert [x.label.speaker_id for x in t.identify_turns("mic", audio)] == ["S1"]


def test_short_segment_is_one_turn_and_too_short_inherits():
    emb = FakeEmbedder()
    t = SpeakerTracker(emb, turn_cfg())
    one = t.identify_turns("mic", tones((220.0, 1.2)))
    assert len(one) == 1 and one[0].label.speaker_id == "S1"
    assert (one[0].t_start, one[0].t_end) == (0.0, 1.2)
    short = t.identify_turns("mic", tones((330.0, 0.5)))
    assert len(short) == 1 and short[0].label.speaker_id == "S1" and short[0].label.conf == 0.0
    assert short[0].label.embedding is None
    assert len(emb.calls) == 1


def test_tentative_speaker_confirmed_by_two_segments_survives_close(tmp_path):
    labels = SessionLabels(tmp_path)
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), None, labels)
    t.identify_turns("mic", tones((220.0, 5.0)))
    t.identify_turns("mic", tones((220.0, 4.0), (330.0, 2.5), (220.0, 3.0)))
    t.identify_turns("mic", tones((330.0, 2.5), (220.0, 3.0)))
    t.close()
    data = labels.load()
    assert set(data["clusters"]) == {"S1", "S2"} and data["merged"] == {}


def test_unconfirmed_noise_speaker_is_merged_at_close(tmp_path):
    labels = SessionLabels(tmp_path)
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), None, labels)
    t.identify_turns("mic", tones((220.0, 5.0)))
    t.identify_turns("mic", tones((220.0, 4.0), (440.0, 1.5), (220.0, 3.0)))  # one-off
    t.identify_turns("mic", tones((220.0, 5.0)))
    assert "S2" in labels.load()["clusters"]  # shown as its own speaker for now ...
    t.close()
    data = labels.load()
    assert data["merged"] == {"S2": "S1"}  # ... and folded into S1 once the session ends
    assert labels.display_name("S2") == "Speaker 1"


def test_window_embeddings_are_stored_with_session_times(tmp_path):
    from steno.profiles import SessionWindows

    windows = SessionWindows(tmp_path)
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), None, None, None, windows)
    t.identify_turns("mic", tones((220.0, 3.0)), t_start=100.0)
    t.close()
    ws = SessionWindows.load(tmp_path)
    assert len(ws) == 3 and ws.stream == ["mic"] * 3
    assert ws.t0[0] == 100.0 and ws.t1[-1] == 103.0
    assert ws.emb.shape == (3, 16) and abs(float(np.linalg.norm(ws.emb[0])) - 1.0) < 1e-5


def test_window_s_zero_falls_back_to_one_label_per_segment():
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(window_s=0.0))
    turns = t.identify_turns("mic", tones((220.0, 4.0), (330.0, 2.0)))
    assert len(turns) == 1 and (turns[0].t_start, turns[0].t_end) == (0.0, 6.0)


def test_profile_windows_get_the_profile_and_cluster_windows_the_rest():
    store = ProfileStore()
    alice = store.create("Alice", np.vstack([voice_vector(0, 0.05, s) for s in range(4)]))
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), store)
    turns = t.identify_turns("mic", tones((220.0, 4.0), (330.0, 2.0), (220.0, 4.0)))
    assert [x.label.speaker_id for x in turns] == [f"P:{alice.id}", "S1", f"P:{alice.id}"]
    assert [x.label.provisional for x in turns] == [False, True, False]
    assert turns[0].label.name == "Alice"


# --------------------------------------------------------------------------- participants


def three_people(store):
    return {n: store.create(n, np.vstack([voice_vector(i, 0.05, s) for s in range(4)]))
            for i, n in enumerate(["Alice", "Bob", "Carol"])}


def test_participant_list_narrows_live_matching_and_follows_changes(tmp_path):
    from steno import participants

    store = ProfileStore()
    people = three_people(store)
    now = [0.0]
    participants.write(tmp_path, participants.Participants((people["Bob"].id,)))
    t = SpeakerTracker(FakeEmbedder(), cfg(), store, SessionLabels(tmp_path),
                       clock=lambda: now[0], reload_interval_s=5.0, participants_dir=tmp_path)
    # Alice is enrolled but not in the meeting: her voice is only a session cluster
    assert t.identify("mic", speech(220.0)).speaker_id == "S1"
    assert t.identify("mic", speech(330.0)).speaker_id == f"P:{people['Bob'].id}"
    # the list changes during the meeting: picked up at the next reload, not before
    participants.update(tmp_path, add=people["Alice"].id)
    now[0] = 1.0
    assert t.identify("mic", speech(220.0)).speaker_id == "S1"
    now[0] = 6.0
    assert t.identify("mic", speech(220.0)).speaker_id == f"P:{people['Alice'].id}"
    # Carol is still outside the list
    assert not t.identify("mic", speech(440.0)).speaker_id.startswith("P:")


def test_missing_or_empty_participant_list_matches_everybody(tmp_path):
    from steno import participants

    store = ProfileStore()
    people = three_people(store)
    for write_empty in (False, True):  # no file at all, then an empty list
        if write_empty:
            participants.write(tmp_path, participants.Participants())
        t = SpeakerTracker(FakeEmbedder(), cfg(), store, participants_dir=tmp_path)
        assert t.identify("mic", speech(220.0)).speaker_id == f"P:{people['Alice'].id}"
        assert t.identify("mic", speech(440.0)).speaker_id == f"P:{people['Carol'].id}"


def test_participant_list_is_used_for_turns_too(tmp_path):
    from steno import participants

    store = ProfileStore()
    people = three_people(store)
    participants.write(tmp_path, participants.Participants((people["Alice"].id,)))
    t = SpeakerTracker(FakeEmbedder(), turn_cfg(), store, participants_dir=tmp_path)
    turns = t.identify_turns("mic", tones((220.0, 4.0), (330.0, 4.0)))
    assert [x.label.speaker_id for x in turns] == [f"P:{people['Alice'].id}", "S1"]


def test_expected_speaker_count_caps_the_session_clusters(tmp_path):
    from steno import participants

    now = [0.0]
    t = SpeakerTracker(FakeEmbedder(), cfg(max_speakers=7), None, clock=lambda: now[0],
                       reload_interval_s=5.0, participants_dir=tmp_path)
    assert t.clusterer.max_speakers == 7  # no file: the config's upper bound
    participants.write(tmp_path, participants.Participants((), 3))
    now[0] = 6.0
    t.identify("mic", speech(220.0))
    assert t.clusterer.max_speakers == 3
    participants.write(tmp_path, participants.Participants((), None))
    now[0] = 12.0
    t.identify("mic", speech(220.0))
    assert t.clusterer.max_speakers == 7


def test_damaged_participant_file_means_everybody(tmp_path):
    from steno import participants

    store = ProfileStore()
    people = three_people(store)
    (tmp_path / participants.NAME).write_text("{not json", encoding="utf-8")
    t = SpeakerTracker(FakeEmbedder(), cfg(), store, participants_dir=tmp_path)
    assert t.identify("mic", speech(220.0)).speaker_id == f"P:{people['Alice'].id}"
