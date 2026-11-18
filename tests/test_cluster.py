from __future__ import annotations

import numpy as np

from steno.cluster import (
    accept_split,
    cluster_embeddings,
    eigengap_count,
    kmeans,
    p_prune,
    reassign_minor,
    relabel_by_appearance,
    spectral_cluster,
)


def blobs(sizes, dim=24, noise=0.25, seed=0):
    """Unit vectors around ``len(sizes)`` orthogonal centres; returns (embeddings, truth)."""
    rng = np.random.default_rng(seed)
    emb, truth = [], []
    for k, n in enumerate(sizes):
        centre = np.zeros(dim)
        centre[k] = 1.0
        for _ in range(n):
            v = centre + rng.normal(0, noise, dim) / np.sqrt(dim) * 2
            emb.append(v / np.linalg.norm(v))
            truth.append(k)
    order = rng.permutation(len(truth))
    return np.array(emb, dtype=np.float32)[order], np.array(truth)[order]


def same_partition(a, b):
    """True if two label arrays describe the same grouping (ignoring label names)."""
    ra, rb = relabel_by_appearance(np.asarray(a)), relabel_by_appearance(np.asarray(b))
    return np.array_equal(ra, rb)


def test_p_prune_keeps_at_least_min_pnum_strongest_per_row():
    rng = np.random.default_rng(1)
    sim = rng.random((50, 50))
    out = p_prune(sim, pval=0.012, min_pnum=6)
    assert ((out > 0).sum(axis=1) == 6).all()
    for i in range(50):
        assert set(np.flatnonzero(out[i])) == set(np.argsort(-sim[i])[:6])
    wide = p_prune(sim, pval=0.5, min_pnum=6)
    assert ((wide > 0).sum(axis=1) == 25).all()


def test_eigengap_count_picks_the_largest_gap():
    vals = np.array([0.0, 0.01, 0.02, 0.9, 1.0, 1.1])
    assert eigengap_count(vals, 1, 5) == 3
    assert eigengap_count(vals, 1, 2) in (1, 2)  # capped by max_speakers
    assert eigengap_count(np.array([0.0]), 1, 5) == 1


def test_kmeans_separates_obvious_groups():
    x = np.vstack([np.zeros((10, 2)), np.ones((10, 2)) * 5])
    labels = kmeans(x, 2)
    assert len(set(labels[:10])) == 1 and len(set(labels[10:])) == 1 and labels[0] != labels[-1]
    assert (kmeans(x, 1) == 0).all()


def test_spectral_cluster_estimates_the_speaker_count():
    for sizes in [(60, 50, 40), (70, 60), (50, 45, 40, 35)]:
        emb, truth = blobs(sizes)
        labels, k = spectral_cluster(emb, max_speakers=8)
        assert k == len(sizes), sizes
        assert same_partition(labels, truth)


def test_spectral_cluster_with_given_count_and_single_speaker():
    emb, truth = blobs((60, 50, 40))
    labels, k = spectral_cluster(emb, num_speakers=3)
    assert k == 3 and same_partition(labels, truth)
    labels, k = spectral_cluster(emb, num_speakers=2)
    assert k == 2 and len(set(labels)) == 2
    one, _ = blobs((80,))
    assert spectral_cluster(one, num_speakers=1)[1] == 1
    assert spectral_cluster(one[:1])[1] == 1 and spectral_cluster(one[:0])[1] == 0


def test_reassign_minor_clusters_to_the_nearest_major_one():
    emb, truth = blobs((50, 50, 3))  # a 3-window "speaker": a cough, not a person
    labels = reassign_minor(emb, truth, min_size=4)
    assert len(set(labels)) == 2
    # nothing is minor: unchanged
    assert np.array_equal(reassign_minor(emb, truth, min_size=2), truth)
    # all minor: the largest cluster survives
    tiny = reassign_minor(emb[:6], np.array([0, 0, 0, 1, 1, 2]), min_size=10)
    assert len(set(tiny)) == 1
    # a forced count keeps that many
    assert len(set(reassign_minor(emb, truth, min_size=100, keep=2))) == 2


def test_cluster_embeddings_spectral_agglomerative_and_subsampling():
    emb, truth = blobs((60, 50, 40))
    assert same_partition(cluster_embeddings(emb), truth)
    # few embeddings: average linkage with a threshold, no spectral graph
    small, small_truth = blobs((12, 10, 8), seed=3)
    assert same_partition(cluster_embeddings(small, ahc_threshold=0.5, minor_size=0), small_truth)
    # a fixed count is honoured
    assert len(set(cluster_embeddings(emb, num_speakers=2))) == 2
    assert len(set(cluster_embeddings(small, num_speakers=2, minor_size=0))) == 2
    # above max_points the rest joins the nearest centroid
    big, big_truth = blobs((120, 100, 80), seed=5)
    assert same_partition(cluster_embeddings(big, max_points=90), big_truth)
    assert len(cluster_embeddings(emb[:0])) == 0
    assert cluster_embeddings(emb)[0] == 0  # numbered by first appearance


def dominant_and_quiet(n_major=120, n_minor=30, cos=0.6, dim=24, noise=0.5, seed=3):
    """One dominant voice plus a quiet one whose centre has the given cosine to the first."""
    rng = np.random.default_rng(seed)
    a = np.zeros(dim)
    a[0] = 1.0
    b = np.zeros(dim)
    b[0], b[1] = cos, np.sqrt(1 - cos**2)
    rows, truth = [], []
    for centre, n, k in ((a, n_major, 0), (b, n_minor, 1)):
        for _ in range(n):
            v = centre + rng.normal(0, noise, dim) / np.sqrt(dim)
            rows.append(v / np.linalg.norm(v))
            truth.append(k)
    return np.array(rows, dtype=np.float32), np.array(truth)


def test_accept_split_needs_a_far_centroid_and_enough_minority_weight():
    x, truth = dominant_and_quiet(cos=0.3)
    assert accept_split(x, truth, max_cos=0.78, min_weight=20)
    assert not accept_split(x, truth, max_cos=0.78, min_weight=31)  # minority has only 30 rows
    w = np.full(len(x), 0.5)  # ... or 15 s when every row is half a second
    assert not accept_split(x, truth, weights=w, max_cos=0.78, min_weight=20)
    near, truth_near = dominant_and_quiet(cos=0.95)
    assert not accept_split(near, truth_near, max_cos=0.78, min_weight=20)
    assert not accept_split(x, np.zeros(len(x), dtype=int), max_cos=0.78, min_weight=0)


def agreement(labels, truth):
    """Share of rows on which two 2-way labelings agree (either way round)."""
    same = float(np.mean(np.asarray(labels) == np.asarray(truth)))
    return max(same, 1.0 - same)


def test_split_test_finds_a_quiet_speaker_the_eigengap_misses():
    x, truth = dominant_and_quiet(n_major=150, n_minor=40, cos=0.5, noise=1.5)
    assert spectral_cluster(x, max_speakers=8)[1] == 1  # the situation the test is for
    labels, k = spectral_cluster(x, max_speakers=8, split_max_cos=0.78, split_min_weight=10)
    assert k == 2 and agreement(labels, truth) > 0.85
    # a fixed speaker count is never second-guessed
    assert spectral_cluster(x, num_speakers=1, split_max_cos=0.78, split_min_weight=1)[1] == 1


def test_split_test_leaves_a_single_speaker_alone():
    one, _ = dominant_and_quiet(n_major=190, n_minor=0, noise=0.8)
    assert spectral_cluster(one, split_max_cos=0.78, split_min_weight=10)[1] == 1
    labels = cluster_embeddings(
        one, split_max_cos=0.78, split_min_weight=10, weights=np.full(len(one), 0.5)
    )
    assert len(set(labels)) == 1


def test_cluster_embeddings_passes_the_split_test_through():
    x, truth = dominant_and_quiet(n_major=150, n_minor=40, cos=0.5, noise=1.5)
    w = np.full(len(x), 0.5)  # 40 rows x 0.5 s = 20 s of minority speech
    labels = cluster_embeddings(x, weights=w, split_max_cos=0.78, split_min_weight=10)
    assert len(set(labels)) == 2 and agreement(labels, truth) > 0.85
    too_little = cluster_embeddings(x, weights=w, split_max_cos=0.78, split_min_weight=30)
    assert len(set(too_little)) == 1
    assert len(set(cluster_embeddings(x, weights=w))) == 1  # off by default
