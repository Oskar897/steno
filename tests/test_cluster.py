from __future__ import annotations

import numpy as np

from steno.cluster import (
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
