"""Live recognition of an enrolled person: cluster -> profile binding and profile auto-grow.

All embeddings are synthetic (random unit vectors with a controlled cosine to a base
direction) and all names are made up.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

import numpy as np

from steno import labels, ops, transcript
from steno.config import ProfilesConfig, SpeakersConfig
from steno.labels import PROFILE_PREFIX, SessionLabels
from steno.profiles import ProfileStore, SessionEmbeddings, select_growth
from steno.speakers import SpeakerTracker, l2_normalize
from steno.transcript import SessionWriter

from .synth import EMB_DIM, SR

SEG_S = 3.0  # 3 s of audio = three 1.5 s windows (hop 0.75)
WINDOWS_PER_SEG = 3


def basis(i):
    v = np.zeros(EMB_DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def noisy(direction, noise, rng):
    """Unit vector around ``direction``: signal plus ``noise`` x a random orthogonal part."""
    n = rng.normal(size=EMB_DIM).astype(np.float32)
    n -= (n @ direction) * direction
    return l2_normalize(direction + noise * l2_normalize(n))


def mix(a, b, cos_to_a):
    """Unit vector with the given cosine to ``a`` (``b`` orthogonal to ``a``)."""
    return l2_normalize(cos_to_a * a + np.sqrt(1 - cos_to_a**2) * b)


class ScriptedEmbedder:
    """Returns queued embeddings, one per window, in order."""

    name = "scripted"

    def __init__(self):
        self.queue = deque()

    def load(self):
        pass

    def embed(self, audio):
        return self.queue.popleft()


def say(tracker, embedder, windows, stream="mic"):
    """One 3 s segment whose three windows have the given embeddings."""
    assert len(windows) == WINDOWS_PER_SEG
    embedder.queue.extend(windows)
    return tracker.identify_turns(stream, np.zeros(int(SEG_S * SR), dtype=np.float32))


def alice_profile(store, rng, name="Alice"):
    a = basis(0)
    return store.create(name, np.vstack([noisy(a, 0.1, rng) for _ in range(3)]))


def tracker_for(tmp_path, store, **cfg):
    embedder = ScriptedEmbedder()
    session_labels = SessionLabels(tmp_path)
    session_labels.init()
    base = dict(accept_threshold=0.85, margin=0.05, cluster_match_s=4.0, tentative_ttl=50)
    tracker = SpeakerTracker(embedder, SpeakersConfig(**{**base, **cfg}), store, session_labels)
    return tracker, embedder, session_labels


def drifted_voice(rng, n, cos=0.7):
    """Windows of Alice recorded under other conditions: individually only ~``cos`` to her
    profile (below the window accept threshold of these tests), tightly clustered."""
    a, d = basis(0), basis(1)
    centre = mix(a, d, cos)
    return [noisy(centre, 0.25, rng) for _ in range(n)]


# --------------------------------------------------------------------------- cluster -> profile


def test_cluster_matching_a_profile_is_bound_and_earlier_lines_follow(tmp_path):
    rng = np.random.default_rng(1)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    tracker, emb, sl = tracker_for(tmp_path, store)
    pid = PROFILE_PREFIX + alice.id

    windows = drifted_voice(rng, 4 * WINDOWS_PER_SEG)
    first = say(tracker, emb, windows[:3])
    assert first[0].label.speaker_id == "S1"  # no window reaches the accept threshold
    second = say(tracker, emb, windows[3:6])
    # ~4.5 s of speech: the cluster centroid now clearly matches the profile.
    assert second[0].label.speaker_id == pid and second[0].label.name == "Alice"
    assert not second[0].label.provisional

    # The earlier label S1 resolves to the person through the label layer.
    assert sl.resolve_root("S1") == pid
    assert sl.display_name("S1") == "Alice"
    assert sl.load()["profiles"][alice.id] == "Alice"
    assert "S1" not in sl.load()["clusters"]

    # From now on windows of that cluster come out as the profile directly.
    third = say(tracker, emb, windows[6:9])
    assert third[0].label.speaker_id == pid


def test_unmatched_voice_stays_a_separate_cluster(tmp_path):
    rng = np.random.default_rng(2)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    tracker, emb, sl = tracker_for(tmp_path, store)
    pid = PROFILE_PREFIX + alice.id

    mine = drifted_voice(rng, 3 * WINDOWS_PER_SEG)
    stranger = [noisy(basis(5), 0.2, rng) for _ in range(3 * WINDOWS_PER_SEG)]
    ids = []
    for k in range(3):
        ids.append(say(tracker, emb, mine[k * 3:k * 3 + 3])[0].label.speaker_id)
        ids.append(say(tracker, emb, stranger[k * 3:k * 3 + 3])[0].label.speaker_id)
    assert ids[0] == "S1" and ids[1] == "S2"
    assert ids[-2] == pid  # Alice, once her cluster is bound
    assert ids[-1] == "S2"  # the stranger never becomes Alice
    assert sl.resolve_root("S2") == "S2"


def test_cluster_needs_enough_speech_before_it_is_matched(tmp_path):
    rng = np.random.default_rng(3)
    store = ProfileStore()
    alice_profile(store, rng)
    tracker, emb, sl = tracker_for(tmp_path, store, cluster_match_s=60.0)
    for _ in range(4):
        out = say(tracker, emb, drifted_voice(rng, 3))
        assert out[0].label.speaker_id == "S1"
    assert sl.resolve_root("S1") == "S1"


def test_cluster_matching_can_be_switched_off(tmp_path):
    rng = np.random.default_rng(4)
    store = ProfileStore()
    alice_profile(store, rng)
    tracker, emb, sl = tracker_for(tmp_path, store, cluster_match_threshold=0.0)
    for _ in range(4):
        assert say(tracker, emb, drifted_voice(rng, 3))[0].label.speaker_id == "S1"
    assert sl.resolve_root("S1") == "S1"


def test_lookalike_that_does_not_sound_like_the_session_voice_is_not_bound(tmp_path):
    """The profile says 0.65 for the lookalike's cluster (above the bar), but Alice's own
    windows in this session (an exact match, accepted one by one) are far from it."""
    rng = np.random.default_rng(5)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    a, e = basis(0), basis(7)
    lookalike = mix(a, e, 0.65)

    def run(pool_bar, path):
        path.mkdir()
        tracker, emb, sl = tracker_for(
            path, store, accept_threshold=0.9, cluster_match_pool=pool_bar
        )
        for _ in range(3):  # Alice herself, windows accepted directly
            out = say(tracker, emb, [noisy(a, 0.05, rng) for _ in range(3)])
            assert out[0].label.speaker_id == PROFILE_PREFIX + alice.id
        for _ in range(3):
            say(tracker, emb, [noisy(lookalike, 0.05, rng) for _ in range(3)])
        return sl

    assert run(0.75, tmp_path / "strict").resolve_root("S1") == "S1"  # not merged into Alice
    assert run(0.0, tmp_path / "off").resolve_root("S1") == PROFILE_PREFIX + alice.id


def test_binding_is_sticky_and_survives_cluster_merges(tmp_path):
    rng = np.random.default_rng(6)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    tracker, emb, sl = tracker_for(tmp_path, store)
    pid = PROFILE_PREFIX + alice.id
    for _ in range(2):
        say(tracker, emb, drifted_voice(rng, 3))
    assert tracker._canon("S1") == pid

    # A second cluster opens and later merges into the bound one (survivor S1) ...
    say(tracker, emb, [noisy(basis(9), 0.1, rng) for _ in range(3)])
    assert tracker._canon("S2") == "S2"
    tracker.clusterer._absorb("S1", "S2")
    tracker._sync_labels()
    assert tracker._canon("S2") == pid and sl.resolve_root("S2") == pid

    # ... and the other way round: the bound cluster dies into an unbound survivor, which
    # then carries the binding.
    say(tracker, emb, [noisy(basis(11), 0.1, rng) for _ in range(3)])
    survivor = tracker.clusterer.cluster_ids[-1]
    tracker.clusterer._absorb(survivor, "S1")
    tracker._sync_labels()
    assert tracker._canon(survivor) == pid
    assert sl.resolve_root("S1") == pid and sl.resolve_root(survivor) == pid


def test_cluster_named_by_hand_for_somebody_else_is_not_overwritten(tmp_path):
    sl = SessionLabels(tmp_path)
    sl.ensure_cluster("S1")
    sl.set_cluster_name("S1", "Bob", "deadbeef")
    assert sl.bind_profile("S1", "00000001", "Alice") is False
    assert sl.resolve_root("S1") == "S1" and sl.display_name("S1") == "Bob"
    # the same person is fine
    assert sl.bind_profile("S1", "deadbeef", "Bob") is True
    assert sl.resolve_root("S1") == PROFILE_PREFIX + "deadbeef"


def test_transcript_lines_resolve_to_the_profile_after_binding(tmp_path):
    w = SessionWriter.create(model="fake", sources=["mic"], now=datetime(2026, 1, 1, 10, 0, 0))
    for i, sid in enumerate(["S1", "S1", "S2"]):
        w.append(t_start=i, t_end=i + 0.9, source="mic", speaker="Speaker 1", text="x",
                 lang="sv", speaker_id=sid, speaker_conf=0.5, provisional=True)
    w.close()
    sl = SessionLabels(w.dir)
    sl.ensure_cluster("S1")
    sl.ensure_cluster("S2")
    sl.bind_profile("S1", "00000001", "Alice")
    sl.merge("S2", "S1")  # a later merge into the bound cluster must not resurrect S1
    assert "S1" not in sl.load()["clusters"]
    ids = labels.effective_speaker_ids(w.dir, transcript.read_segments(w.dir))
    assert set(ids.values()) == {PROFILE_PREFIX + "00000001"}
    names = {s.speaker for s in labels.resolve_segments(w.dir, transcript.read_segments(w.dir))}
    assert names == {"Alice"}
    # the transcript itself is untouched
    assert [s.speaker_id for s in transcript.read_segments(w.dir)] == ["S1", "S1", "S2"]


def test_naming_a_cluster_that_is_a_recognised_person_explains_instead_of_failing_oddly():
    w = SessionWriter.create(model="fake", sources=["mic"], now=datetime(2026, 1, 1, 11, 0, 0))
    seg = w.append(t_start=0, t_end=3, source="mic", speaker="Speaker 1", text="x", lang="sv",
                   speaker_id="S1", speaker_conf=0.5, provisional=True)
    emb = SessionEmbeddings(w.dir)
    emb.add(seg.seq, basis(0))
    emb.close()
    w.close()
    sl = SessionLabels(w.dir)
    sl.ensure_cluster("S1")
    sl.bind_profile("S1", "00000001", "Alice")
    try:
        ops.name_cluster(w.dir, "S1", "Carol", ProfileStore())
    except ops.OpsError as exc:
        assert "Alice" in str(exc) and "recognised" in str(exc)
    else:
        raise AssertionError("expected OpsError")


# --------------------------------------------------------------------------- auto-grow


def pool_of(direction, n, rng, noise=0.3):
    return np.vstack([noisy(direction, noise, rng) for _ in range(n)])


def test_select_growth_takes_diverse_samples_up_to_the_cap():
    rng = np.random.default_rng(10)
    a, d = basis(0), basis(1)
    existing = np.vstack([noisy(a, 0.1, rng) for _ in range(3)])
    shifted = mix(a, d, 0.8)  # this session's sound: close to the profile, not the same
    pool = pool_of(shifted, 40, rng, noise=0.4)
    new = select_growth(existing, pool, max_new=5)
    assert new.shape == (5, EMB_DIM)
    assert np.allclose(np.linalg.norm(new, axis=1), 1.0, atol=1e-5)
    # picks differ from each other and from the profile (farthest-first), none is a duplicate
    sims = new @ np.vstack([existing, new]).T
    sims[np.arange(5), 3 + np.arange(5)] = -1  # ignore self-similarity
    assert sims.max() < 0.93
    assert len(select_growth(existing, pool, max_new=2)) == 2
    assert len(select_growth(existing, pool, max_new=0)) == 0


def test_select_growth_skips_near_duplicates_of_the_profile():
    rng = np.random.default_rng(11)
    a = basis(0)
    existing = np.vstack([noisy(a, 0.05, rng) for _ in range(6)])
    pool = pool_of(a, 20, rng, noise=0.05)  # sounds exactly like the profile already
    assert len(select_growth(existing, pool, dup=0.97)) == 0


def test_select_growth_refuses_a_session_that_is_not_the_profiles_voice():
    rng = np.random.default_rng(12)
    existing = np.vstack([noisy(basis(0), 0.1, rng) for _ in range(3)])
    stranger = pool_of(basis(5), 30, rng, noise=0.3)
    assert len(select_growth(existing, stranger)) == 0
    assert len(select_growth(existing, np.zeros((0, EMB_DIM)))) == 0


def drifted_pool(rng, n=40):
    return pool_of(mix(basis(0), basis(1), 0.8), n, rng, noise=0.4)


def test_store_grow_adds_encrypted_samples_and_respects_the_cap():
    from steno import paths
    from steno.profiles import MAX_EMBEDDINGS

    rng = np.random.default_rng(13)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    path = paths.profiles_dir() / f"{alice.id}.bin"
    before = store.get(alice.id)
    assert store.grow(alice.id, drifted_pool(rng), tag="auto:20260101-100000", max_new=5) == 5
    grown = store.get(alice.id)
    assert len(grown.embeddings) == len(before.embeddings) + 5
    assert grown.tags[-5:] == ["auto:20260101-100000"] * 5
    assert np.allclose(grown.embeddings[: len(before.embeddings)], before.embeddings)
    raw = path.read_bytes()
    assert raw.startswith((b"STENO-DPAPI1", b"STENO-PLAIN1"))
    assert b"auto:2026" not in raw or raw.startswith(b"STENO-PLAIN1")  # tags are encrypted

    for _ in range(12):  # many sessions: never more than the cap
        store.grow(alice.id, drifted_pool(rng), tag="auto:later", max_new=5, dup=0.999)
    assert len(store.get(alice.id).embeddings) <= MAX_EMBEDDINGS


def test_store_grow_leaves_the_file_alone_when_there_is_nothing_to_add():
    rng = np.random.default_rng(14)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    stamp = store.get(alice.id).updated
    mtime = store.signature()
    assert store.grow(alice.id, pool_of(basis(5), 30, rng)) == 0  # somebody else's voice
    assert store.grow("0badc0de", drifted_pool(rng)) == 0  # no such profile
    assert store.get(alice.id).updated == stamp and store.signature() == mtime


def test_tracker_grows_the_recognised_profile_only_when_enabled(tmp_path):
    rng = np.random.default_rng(15)
    pcfg = ProfilesConfig(auto_update_min_s=5.0)

    def session(sub, profiles_cfg):
        store = ProfileStore()
        # a fresh profile each time keeps the two runs independent
        alice = alice_profile(store, np.random.default_rng(15), name=f"Alice {sub}")
        tracker, emb, _ = tracker_for(tmp_path / sub, store, accept_threshold=0.5)
        tracker.profiles_cfg = profiles_cfg
        for _ in range(6):
            say(tracker, emb, [noisy(mix(basis(0), basis(1), 0.8), 0.3, rng) for _ in range(3)])
        # somebody else speaks too: they must not end up in anyone's profile
        for _ in range(2):
            say(tracker, emb, [noisy(basis(6), 0.1, rng) for _ in range(3)])
        tracker.close()
        return store, alice

    (tmp_path / "on").mkdir()
    store, alice = session("on", pcfg)
    grown = store.get(alice.id)
    assert len(grown.embeddings) > 3
    assert all(t.startswith("auto:") for t in grown.tags[3:])
    assert all(float(e @ basis(6)) < 0.5 for e in grown.embeddings)  # nobody else's voice

    (tmp_path / "off").mkdir()
    store, alice = session("off", ProfilesConfig(auto_update=False, auto_update_min_s=5.0))
    assert len(store.get(alice.id).embeddings) == 3
    (tmp_path / "none").mkdir()
    store, alice = session("none", None)
    assert len(store.get(alice.id).embeddings) == 3


def test_tracker_does_not_grow_a_profile_after_too_little_speech(tmp_path):
    rng = np.random.default_rng(16)
    store = ProfileStore()
    alice = alice_profile(store, rng)
    tracker, emb, _ = tracker_for(tmp_path, store, accept_threshold=0.5)
    tracker.profiles_cfg = ProfilesConfig(auto_update_min_s=30.0)
    for _ in range(3):
        say(tracker, emb, [noisy(mix(basis(0), basis(1), 0.8), 0.3, rng) for _ in range(3)])
    tracker.close()
    assert len(store.get(alice.id).embeddings) == 3
