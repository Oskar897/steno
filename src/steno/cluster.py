"""Offline clustering of window embeddings (pure numpy, no scipy / sklearn).

Used by ``steno refine``. Follows the recipe of 3D-Speaker for CAM++ embeddings:

1. cosine similarity matrix, *p-pruning* (each row keeps only its strongest
   neighbours, at least ``min_pnum`` of them, so a speaker's cluster stays a tight
   graph component), symmetrise;
2. unnormalised graph Laplacian; the speaker count is the *eigengap* of its smallest
   eigenvalues (or given by the caller); k-means on the leading eigenvectors;
3. *minor clusters* (a few windows only) are not trusted as people: their windows are
   reassigned to the nearest major cluster.

With fewer than ``min_spectral`` embeddings the graph is too small to be meaningful and
average-linkage agglomerative clustering with a similarity threshold is used instead.
"""

from __future__ import annotations

import numpy as np


def _unit_rows(emb: np.ndarray) -> np.ndarray:
    emb = np.asarray(emb, dtype=np.float64)
    return emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12)


def p_prune(sim: np.ndarray, pval: float = 0.012, min_pnum: int = 6) -> np.ndarray:
    """Zero all but the strongest ``max(pval * n, min_pnum)`` entries of every row."""
    n = len(sim)
    keep = max(int(round(pval * n)), min_pnum)
    keep = min(keep, n)
    out = np.zeros_like(sim)
    idx = np.argpartition(-sim, keep - 1, axis=1)[:, :keep]
    rows = np.arange(n)[:, None]
    out[rows, idx] = sim[rows, idx]
    return out


def eigengap_count(eigenvalues: np.ndarray, min_speakers: int, max_speakers: int) -> int:
    """Speaker count = position of the largest gap between consecutive small eigenvalues."""
    lam = np.sort(np.asarray(eigenvalues))
    hi = min(max_speakers, len(lam) - 1)
    lo = max(1, min(min_speakers, hi))
    if hi <= lo:
        return max(1, min(lo, len(lam)))
    gaps = np.diff(lam[lo - 1 : hi + 1])  # gap after eigenvalue k, k = lo .. hi
    return int(np.argmax(gaps)) + lo


def kmeans(x: np.ndarray, k: int, n_init: int = 10, iters: int = 100, seed: int = 0) -> np.ndarray:
    """Plain k-means with k-means++ seeding; best of ``n_init`` runs. Returns labels."""
    n = len(x)
    k = max(1, min(k, n))
    if k == 1:
        return np.zeros(n, dtype=int)
    rng = np.random.default_rng(seed)
    best_labels, best_inertia = np.zeros(n, dtype=int), np.inf
    for _ in range(n_init):
        centers = [x[int(rng.integers(n))]]
        for _ in range(1, k):
            d2 = np.min(((x[:, None, :] - np.asarray(centers)[None]) ** 2).sum(-1), axis=1)
            total = d2.sum()
            probs = d2 / total if total > 0 else np.full(n, 1.0 / n)
            centers.append(x[int(rng.choice(n, p=probs))])
        c = np.asarray(centers)
        labels = np.zeros(n, dtype=int)
        for it in range(iters):
            dist = ((x[:, None, :] - c[None]) ** 2).sum(-1)
            new = dist.argmin(axis=1)
            if it and np.array_equal(new, labels):
                break
            labels = new
            for j in range(k):
                members = x[labels == j]
                if len(members):
                    c[j] = members.mean(axis=0)
        inertia = float(((x - c[labels]) ** 2).sum())
        if inertia < best_inertia:
            best_labels, best_inertia = labels.copy(), inertia
    return best_labels


def spectral_cluster(
    emb: np.ndarray,
    *,
    num_speakers: int | None = None,
    min_speakers: int = 1,
    max_speakers: int = 10,
    pval: float = 0.012,
    min_pnum: int = 6,
    seed: int = 0,
) -> tuple[np.ndarray, int]:
    """Spectral clustering with eigengap speaker-count estimation. Returns ``(labels, k)``."""
    x = _unit_rows(emb)
    n = len(x)
    if n == 0:
        return np.zeros(0, dtype=int), 0
    if n == 1:
        return np.zeros(1, dtype=int), 1
    sim = np.clip(x @ x.T, 0.0, None)
    np.fill_diagonal(sim, 0.0)
    a = p_prune(sim, pval, min_pnum)
    a = 0.5 * (a + a.T)
    lap = np.diag(a.sum(axis=1)) - a
    vals, vecs = np.linalg.eigh(lap)
    if num_speakers is not None:
        k = max(1, min(num_speakers, n))
    else:
        k = eigengap_count(vals, min_speakers, min(max_speakers, n - 1))
    if k == 1:
        return np.zeros(n, dtype=int), 1
    return kmeans(vecs[:, :k], k, seed=seed), k


def reassign_minor(
    emb: np.ndarray, labels: np.ndarray, min_size: int, keep: int | None = None
) -> np.ndarray:
    """Move windows of clusters with at most ``min_size`` members to the nearest major cluster.

    Major clusters are the ones larger than ``min_size``; if none is, the largest cluster
    stays (so a very short session still has a speaker). ``keep`` forces that many
    clusters to survive (the largest ones) when the caller fixed the speaker count.
    """
    labels = np.asarray(labels).copy()
    if len(labels) == 0:
        return labels
    ids, counts = np.unique(labels, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    major = [int(ids[i]) for i in order if counts[i] > min_size]
    if keep is not None:
        major = [int(ids[i]) for i in order[:keep]]
    if not major:
        major = [int(ids[order[0]])]
    if len(major) == len(ids):
        return labels
    x = _unit_rows(emb)
    cents = {m: x[labels == m].mean(axis=0) for m in major}
    cents = {m: c / max(np.linalg.norm(c), 1e-12) for m, c in cents.items()}
    mat = np.vstack([cents[m] for m in major])
    for cid in ids:
        if int(cid) in cents:
            continue
        sel = labels == cid
        # nearest major centroid per window (a minor cluster may straddle two people)
        labels[sel] = np.asarray(major)[np.argmax(x[sel] @ mat.T, axis=1)]
    return labels


def agglomerative_threshold(emb: np.ndarray, threshold: float) -> np.ndarray:
    """Average-linkage agglomerative clustering on cosine similarity (see speakers)."""
    from steno.speakers import agglomerative_average

    return agglomerative_average(emb, threshold)


def relabel_by_appearance(labels: np.ndarray) -> np.ndarray:
    order: dict[int, int] = {}
    for x in labels:
        order.setdefault(int(x), len(order))
    return np.array([order[int(x)] for x in labels], dtype=int)


def cluster_embeddings(
    emb: np.ndarray,
    *,
    num_speakers: int | None = None,
    max_speakers: int = 10,
    pval: float = 0.012,
    min_pnum: int = 6,
    min_spectral: int = 40,
    ahc_threshold: float = 0.4,
    minor_size: int = 4,
    max_points: int = 2500,
    seed: int = 0,
) -> np.ndarray:
    """Full offline recipe: spectral (or AHC when few points) + minor-cluster reassignment.

    More than ``max_points`` embeddings are clustered on an even subsample and the rest
    joins the nearest centroid (the eigendecomposition is cubic in the point count).
    Labels are numbered by first appearance.
    """
    emb = np.asarray(emb, dtype=np.float32)
    n = len(emb)
    if n == 0:
        return np.zeros(0, dtype=int)
    x = _unit_rows(emb)
    if n <= max_points:
        sub = np.arange(n)
    else:
        sub = np.unique(np.linspace(0, n - 1, max_points).astype(int))
    xs = x[sub]
    if len(xs) < min_spectral and num_speakers is None:
        labels = agglomerative_threshold(xs, ahc_threshold)
    elif len(xs) < min_spectral:
        labels = kmeans(xs, num_speakers or 1, seed=seed)
    else:
        labels, _ = spectral_cluster(
            xs, num_speakers=num_speakers, max_speakers=max_speakers, pval=pval,
            min_pnum=min_pnum, seed=seed,
        )
    labels = reassign_minor(xs, labels, minor_size, keep=num_speakers)
    if len(sub) < n:
        full = np.zeros(n, dtype=int)
        ids = np.unique(labels)
        cents = np.vstack([xs[labels == i].mean(axis=0) for i in ids])
        cents /= np.maximum(np.linalg.norm(cents, axis=1, keepdims=True), 1e-12)
        full[:] = ids[np.argmax(x @ cents.T, axis=1)]
        full[sub] = labels
        labels = full
    return relabel_by_appearance(labels)
