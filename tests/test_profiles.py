from __future__ import annotations

import numpy as np
import pytest

from steno import paths, profile_updates
from steno.profiles import (
    MAX_EMBEDDINGS,
    ProfileError,
    ProfileStore,
    SessionEmbeddings,
    prune_redundant,
)

from .synth import voice_vector


def vecs(index, n, noise=0.1):
    return np.vstack([voice_vector(index, noise, seed=i) for i in range(n)])


def test_profile_roundtrip_and_file_is_not_plain():
    store = ProfileStore()
    created = store.create("Alice", vecs(0, 3), tag="20260101-100000")
    path = paths.profiles_dir() / f"{created.id}.bin"
    assert path.is_file()
    raw = path.read_bytes()
    assert raw.startswith((b"STENO-DPAPI1", b"STENO-PLAIN1"))
    assert b"Alice" not in raw or raw.startswith(b"STENO-PLAIN1")  # name is encrypted too

    loaded = ProfileStore().get(created.id)
    assert loaded is not None and loaded.name == "Alice"
    assert loaded.embeddings.shape == (3, 16) and loaded.embeddings.dtype == np.float32
    assert np.allclose(np.linalg.norm(loaded.embeddings, axis=1), 1.0, atol=1e-5)
    assert loaded.tags == ["20260101-100000"] * 3
    assert loaded.created and loaded.updated


def test_names_are_unique_case_insensitive_and_findable():
    store = ProfileStore()
    store.create("Alice", vecs(0, 2))
    with pytest.raises(ProfileError, match="already exists"):
        store.create("alice", vecs(0, 2))
    assert store.find_by_name("  ALICE ").name == "Alice"
    assert store.find_by_name("Bob") is None
    with pytest.raises(ProfileError):
        store.create("   ", vecs(0, 1))


def test_add_embeddings_extends_and_caps_at_30():
    store = ProfileStore()
    p = store.create("Alice", vecs(0, 20), tag="a")
    p = store.add_embeddings(p.id, vecs(0, 20, noise=0.2), tag="b")
    assert len(p.embeddings) == MAX_EMBEDDINGS == 30
    assert len(p.tags) == 30 and set(p.tags) <= {"a", "b"}
    assert len(store.get(p.id).embeddings) == 30
    with pytest.raises(ProfileError):
        store.add_embeddings("deadbeef", vecs(0, 1))


def test_prune_drops_redundant_embeddings_first():
    base = vecs(0, 1, noise=0)[0]
    near_duplicates = np.vstack([base, base, base])
    distinct = vecs(1, 1, noise=0)
    other = vecs(2, 1, noise=0)
    emb = np.vstack([near_duplicates, distinct, other])
    kept, tags = prune_redundant(emb, list("abcde"), limit=3)
    assert len(kept) == 3 and len(tags) == 3
    # Both distinct directions survive; only duplicates of `base` were dropped.
    assert any(np.allclose(k, distinct[0]) for k in kept)
    assert any(np.allclose(k, other[0]) for k in kept)


def test_rename_delete_list_and_signature():
    store = ProfileStore()
    sig0 = store.signature()
    a = store.create("Alice", vecs(0, 2))
    store.create("Bob", vecs(1, 2))
    assert [p.name for p in store.list()] == ["Alice", "Bob"]
    assert store.signature() != sig0

    store.rename("Alice", "Alicia")
    assert store.get(a.id).name == "Alicia"
    with pytest.raises(ProfileError, match="already exists"):
        store.rename("Alicia", "bob")
    with pytest.raises(ProfileError, match="no such person"):
        store.rename("Nobody", "X")

    store.delete("Bob")
    assert [p.name for p in store.list()] == ["Alicia"]
    with pytest.raises(ProfileError):
        store.delete("Bob")
    assert not any(f.name.endswith(".tmp") for f in paths.profiles_dir().iterdir())


def test_profile_ids_cannot_escape_the_directory():
    with pytest.raises(ProfileError):
        ProfileStore().get("../../etc/passwd")


def test_session_embeddings_roundtrip_and_batched_flush(tmp_path):
    emb = SessionEmbeddings(tmp_path, flush_every=3)
    emb.add(1, voice_vector(0))
    emb.add(2, voice_vector(1))
    assert not (tmp_path / "embeddings.bin").exists()  # buffered
    emb.add(5, voice_vector(2))
    assert (tmp_path / "embeddings.bin").exists()  # third add flushed
    emb.add(7, voice_vector(0, 0.1, 1))
    emb.close()

    loaded = SessionEmbeddings.load(tmp_path)
    assert sorted(loaded) == [1, 2, 5, 7]
    assert np.allclose(loaded[5], voice_vector(2))
    assert SessionEmbeddings.load(tmp_path / "missing") == {}
    assert len(SessionEmbeddings(tmp_path)._items) == 4  # reopening keeps earlier data


# --------------------------------------------------------------------------- pending updates


def test_pending_updates_are_encrypted_and_the_summary_has_no_embeddings(tmp_path):
    new = vecs(0, 4, noise=0.3)
    profile_updates.write(tmp_path, [profile_updates.Pending("0a1b2c3d", "Alice", 185.04, new)])
    raw = (tmp_path / profile_updates.DATA_NAME).read_bytes()
    assert raw.startswith((b"STENO-DPAPI1", b"STENO-PLAIN1"))
    assert profile_updates.summary(tmp_path) == [
        {"id": "0a1b2c3d", "name": "Alice", "samples": 4, "seconds": 185.0}]
    plain = (tmp_path / profile_updates.SUMMARY_NAME).read_text(encoding="utf-8")
    assert "embedding" not in plain and str(float(new[0][0]))[:6] not in plain
    (item,) = profile_updates.load(tmp_path)
    assert item.profile_id == "0a1b2c3d" and item.samples == 4 and item.seconds == 185.0
    assert np.allclose(np.linalg.norm(item.embeddings, axis=1), 1.0, atol=1e-5)
    profile_updates.write(tmp_path, [])  # nothing to ask about: both files go
    assert not list(tmp_path.iterdir())
    assert profile_updates.load(tmp_path) == [] and profile_updates.summary(tmp_path) == []


def test_resolve_applies_skips_and_drops_people_who_are_gone(tmp_path):
    sess = tmp_path / "20260101-100000"
    sess.mkdir()
    store = ProfileStore()
    alice = store.create("Alice", vecs(0, 3))
    bob = store.create("Bob", vecs(1, 3))
    carol = store.create("Carol", vecs(2, 3))
    items = [profile_updates.Pending(p.id, p.name, 60.0, vecs(i + 3, 2, noise=0.05))
             for i, p in enumerate((alice, bob, carol))]
    profile_updates.write(sess, items)
    store.delete("Carol")

    assert profile_updates.resolve(sess, store, apply=[alice.id]) == {alice.id: 2}
    assert len(store.get(alice.id).embeddings) == 5
    assert store.get(alice.id).tags[-1] == f"auto:{sess.name}"
    assert [r["id"] for r in profile_updates.summary(sess)] == [bob.id]  # Carol is gone
    assert len(store.get(bob.id).embeddings) == 3  # still pending, not applied

    assert profile_updates.resolve(sess, store, skip=[bob.id]) == {}
    assert len(store.get(bob.id).embeddings) == 3
    assert profile_updates.summary(sess) == []
    assert not list(sess.iterdir())


def test_prune_uses_current_names_and_forgets_deleted_people(tmp_path):
    store = ProfileStore()
    alice = store.create("Alice", vecs(0, 3))
    bob = store.create("Bob", vecs(1, 3))
    profile_updates.write(tmp_path, [
        profile_updates.Pending(alice.id, "Alice", 30.0, vecs(5, 2)),
        profile_updates.Pending(bob.id, "Bob", 30.0, vecs(6, 2))])
    store.rename("Alice", "Alicia")
    store.delete("Bob")
    assert profile_updates.prune(tmp_path, store) == [
        {"id": alice.id, "name": "Alicia", "samples": 2, "seconds": 30.0}]
    assert [i.profile_id for i in profile_updates.load(tmp_path)] == [alice.id]


def test_store_candidates_match_what_grow_adds():
    store = ProfileStore()
    alice = store.create("Alice", vecs(0, 3, noise=0.05))
    pool = np.vstack([voice_vector(0, 0.4, seed=100 + i) for i in range(20)])
    cand = store.candidates(alice.id, pool)
    assert len(store.get(alice.id).embeddings) == 3  # nothing was added
    assert store.grow(alice.id, pool) == len(cand)
    assert store.candidates("0badc0de", pool).shape == (0, 16)
