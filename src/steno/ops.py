"""Explicit speaker operations: enrol, name a session speaker, refine a session.

These are the only code paths that change voice profiles (the live engine never
does) and the only ones that write ``labels.json`` / name entries in
``speakers.json``. They have no CLI or audio-device dependencies so they can be
tested with synthetic embeddings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from steno import labels as lab
from steno import transcript
from steno.cluster import cluster_embeddings
from steno.config import SpeakersConfig
from steno.profiles import (
    Profile,
    ProfileStore,
    SessionEmbeddings,
    SessionWindows,
    WindowSet,
    now_iso,
)
from steno.speakers import Embedder, ProfileMatcher, l2_normalize
from steno.vad import SileroVad, SpeechProbModel, SpeechSegmenter, VadParams
from steno.windows import window_spans

SAMPLE_RATE = 16000
NAME_MIN_SEGMENT_S = 2.0  # `steno name` only learns from segments at least this long


class OpsError(ValueError):
    """A user-facing problem with an operation (nothing was changed)."""


# --------------------------------------------------------------------------- enrol


@dataclass(frozen=True)
class EnrollResult:
    profile: Profile
    created: bool
    used: int  # windows embedded and kept this time
    total: int  # embeddings now in the profile
    speech_s: float = 0.0  # speech found in the recording
    dropped: int = 0  # outlier windows left out


#: Enrolment recordings with less speech than this give a thin, noisy profile.
ENROLL_SUGGESTED_SPEECH_S = 30.0


def drop_outliers(emb: np.ndarray, sigma: float = 2.0, min_keep: int = 5) -> np.ndarray:
    """Boolean keep-mask: drop embeddings whose cosine to the mean is below mean - sigma * std.

    Throat clearing, a cough or a second voice show up as windows far from the rest.
    Fewer than ``min_keep`` embeddings are all kept.
    """
    n = len(emb)
    keep = np.ones(n, dtype=bool)
    if n < min_keep or sigma <= 0:
        return keep
    unit = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12)
    centre = unit.mean(axis=0)
    centre /= max(float(np.linalg.norm(centre)), 1e-12)
    sims = unit @ centre
    return sims >= sims.mean() - sigma * sims.std()


def enroll_audio(
    audio: np.ndarray,
    name: str,
    store: ProfileStore,
    embedder: Embedder,
    *,
    min_chunk_s: float = 1.0,
    window_s: float = 2.5,
    hop_s: float = 1.0,
    outlier_sigma: float = 2.0,
    vad_factory: Callable[[], SpeechProbModel] = SileroVad,
) -> EnrollResult:
    """Embed windows of the speech in ``audio`` (one person talking) into a new or existing profile.

    The speech is cut into ``window_s`` windows every ``hop_s`` (many overlapping
    embeddings per minute instead of a handful of long ones), outliers are dropped, and
    the profile keeps at most 30 of them (the most redundant go first).
    """
    segmenter = SpeechSegmenter(vad_factory(), VadParams())
    utterances = segmenter.feed(np.asarray(audio, dtype=np.float32)) + segmenter.flush()
    chunks = [u for u in utterances if u.duration >= min_chunk_s]
    if not chunks:
        raise OpsError(
            f"no speech of at least {min_chunk_s:g} s found; speak continuously and close to "
            "the microphone"
        )
    vectors = []
    for u in chunks:
        for a, b in window_spans(u.duration, window_s, hop_s, min_chunk_s):
            vectors.append(embedder.embed(u.audio[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)]))
    embeddings = np.vstack(vectors)
    keep = drop_outliers(embeddings, outlier_sigma)
    embeddings = embeddings[keep]
    tag = f"enroll:{now_iso()}"
    existing = store.find_by_name(name)
    if existing is None:
        profile = store.create(name, embeddings, tag)
    else:
        profile = store.add_embeddings(existing.id, embeddings, tag)
    speech_s = float(sum(u.duration for u in chunks))
    return EnrollResult(
        profile, existing is None, len(embeddings), len(profile.embeddings), speech_s,
        int((~keep).sum()),
    )


# --------------------------------------------------------------------------- name


@dataclass(frozen=True)
class NameResult:
    cluster_id: str
    profile: Profile
    created: bool
    used: int  # segments (>= 2 s) that contributed embeddings
    skipped: int  # segments of the cluster that were too short or had no embedding
    markdown_updated: bool


def name_cluster(
    session_dir: Path,
    spec: str,
    name: str,
    store: ProfileStore,
    *,
    min_segment_s: float = NAME_MIN_SEGMENT_S,
) -> NameResult:
    """Give a session cluster ("Speaker 2" / "S2") a name and learn it as a profile."""
    session_dir = Path(session_dir)
    name = name.strip()
    cluster = lab.parse_cluster_spec(spec)
    if cluster is None:
        raise OpsError(f"not a speaker cluster: {spec!r} (use e.g. S2 or \"Speaker 2\")")
    if not name:
        raise OpsError("name must not be empty")
    session_labels = lab.SessionLabels(session_dir)
    cluster = session_labels.resolve_root(cluster)

    segments = transcript.read_segments(session_dir)
    emb = SessionEmbeddings.load(session_dir)
    ids = lab.effective_speaker_ids(session_dir, segments)
    members = [s for s in segments if ids.get(s.seq) == cluster]
    if not members:
        known = sorted({i for i in ids.values() if lab.cluster_number(i) is not None})
        raise OpsError(
            f"session has no segments for {cluster}; clusters: {', '.join(known) or '-'}"
        )
    usable = [s for s in members if s.seq in emb and (s.t_end - s.t_start) >= min_segment_s]
    if not usable:
        raise OpsError(
            f"{cluster} has no embedded segments of at least {min_segment_s:g} s to learn from"
        )
    vectors = np.vstack([emb[s.seq] for s in usable])
    tag = session_dir.name
    existing = store.find_by_name(name)
    if existing is None:
        profile = store.create(name, vectors, tag)
    else:
        profile = store.add_embeddings(existing.id, vectors, tag)
    session_labels.set_cluster_name(cluster, profile.name, profile.id)
    md = lab.regenerate_markdown(session_dir)
    return NameResult(
        cluster, profile, existing is None, len(usable), len(members) - len(usable), md
    )


# --------------------------------------------------------------------------- refine


@dataclass
class RefineResult:
    segments: int
    embedded: int
    speakers: list[tuple[str, str, int]] = field(default_factory=list)  # (id, name, segments)
    markdown_updated: bool = True


def refine_session(
    session_dir: Path,
    cfg: SpeakersConfig,
    store: ProfileStore,
    *,
    num_speakers: int | None = None,
) -> RefineResult:
    """Offline re-labelling: cluster the whole session, then match clusters to profiles.

    The window embeddings of the live run (``windows.bin``) are clustered together with
    spectral clustering and an eigengap speaker-count estimate (or ``num_speakers``);
    tiny clusters are reassigned to the nearest large one (``steno.cluster``). Each
    transcript segment then takes the majority label of the windows inside it. Sessions
    recorded before windows existed fall back to one embedding per segment.

    Writes ``labels.json`` (the overlay readers prefer over live labels), replaces the
    cluster namespace in ``speakers.json`` (unmatched clusters become S1, S2, ... in
    order of first appearance, names given to clusters earlier are dropped but live on
    in the profiles) and regenerates ``transcript.md``. The JSONL is not touched.
    """
    if num_speakers is not None and num_speakers < 1:
        raise OpsError("the number of speakers must be at least 1")
    session_dir = Path(session_dir)
    segments = transcript.read_segments(session_dir)
    if not segments:
        raise OpsError("session has no segments")
    windows = SessionWindows.load(session_dir)
    emb = SessionEmbeddings.load(session_dir)
    if windows is not None and len(windows):
        points = windows.emb
        owners = _window_owners(segments, windows)
        embedded = [s for s in segments if owners.get(s.seq)]
    else:
        embedded = [s for s in segments if s.seq in emb]
        if not embedded:
            raise OpsError(
                "session has no speaker embeddings (recorded without speaker recognition?)"
            )
        points = np.vstack([emb[s.seq] for s in embedded])
        owners = {s.seq: [i] for i, s in enumerate(embedded)}
    if not embedded:
        raise OpsError("no transcript segment overlaps an embedded window")

    cluster_of = cluster_embeddings(
        points,
        num_speakers=num_speakers,
        max_speakers=cfg.refine_max_speakers,
        pval=cfg.refine_pval,
        min_pnum=cfg.refine_min_pnum,
        min_spectral=cfg.refine_min_spectral,
        ahc_threshold=cfg.refine_threshold,
        minor_size=cfg.refine_minor_windows if windows is not None and len(windows) else 0,
    )
    matcher = ProfileMatcher(store.list(), cfg.accept_threshold, cfg.margin)

    ids: dict[int, str] = {}  # cluster index -> speaker id
    profile_names: dict[str, str] = {}
    centroids: dict[int, np.ndarray] = {}
    next_n = 1
    for k in range(int(cluster_of.max()) + 1):  # numbered by first appearance
        centroids[k] = l2_normalize(points[cluster_of == k].mean(axis=0))
        match = matcher.match(centroids[k])
        if match.accepted and match.profile is not None:
            ids[k] = lab.PROFILE_PREFIX + match.profile.id
            profile_names[match.profile.id] = match.profile.name
        else:
            ids[k] = f"S{next_n}"
            next_n += 1

    entries: dict[int, dict] = {}
    for seg in embedded:
        idx = owners[seg.seq]
        votes: dict[int, int] = {}
        for i in idx:
            votes[int(cluster_of[i])] = votes.get(int(cluster_of[i]), 0) + 1
        # majority; ties go to the cluster whose centroid is closest to the segment's windows
        mean = l2_normalize(points[idx].mean(axis=0))
        k = max(votes, key=lambda c: (votes[c], float(centroids[c] @ mean)))
        entries[seg.seq] = {
            "speaker_id": ids[k],
            "conf": round(float(np.mean([points[i] @ centroids[k] for i in idx])), 3),
        }
    _fill_unembedded(segments, entries)

    session_labels = lab.SessionLabels(session_dir)
    session_labels.replace_clusters(
        [i for i in ids.values() if not i.startswith(lab.PROFILE_PREFIX)], profile_names
    )
    data = session_labels.load()
    for entry in entries.values():
        entry["name"] = lab.SessionLabels.display(data, entry["speaker_id"])
    lab.write_overlay(session_dir, entries, created=now_iso())
    markdown = lab.regenerate_markdown(session_dir)

    counts: dict[str, int] = {}
    for entry in entries.values():
        counts[entry["speaker_id"]] = counts.get(entry["speaker_id"], 0) + 1
    speakers = [(sid, lab.SessionLabels.display(data, sid), n) for sid, n in counts.items()]
    return RefineResult(len(segments), len(embedded), speakers, markdown)


def _window_owners(
    segments: list[transcript.Segment], windows: WindowSet
) -> dict[int, list[int]]:
    """seq -> indices of the windows (same stream) whose centre lies inside the segment.

    A segment shorter than the window spacing may contain no centre; it then takes the
    window overlapping it the most.
    """
    centers = (windows.t0 + windows.t1) / 2
    streams = np.array(windows.stream)
    out: dict[int, list[int]] = {}
    for seg in segments:
        same = streams == seg.source
        inside = np.flatnonzero(same & (centers >= seg.t_start) & (centers < seg.t_end))
        if len(inside):
            out[seg.seq] = [int(i) for i in inside]
            continue
        overlap = np.minimum(windows.t1, seg.t_end) - np.maximum(windows.t0, seg.t_start)
        overlap = np.where(same, overlap, -np.inf)
        best = int(np.argmax(overlap)) if len(overlap) else -1
        if best >= 0 and overlap[best] > 0:
            out[seg.seq] = [best]
    return out


def _fill_unembedded(segments: list[transcript.Segment], entries: dict[int, dict]) -> None:
    """Segments without an embedding (too short) take the neighbouring speaker of their stream."""
    by_source: dict[str, list[transcript.Segment]] = {}
    for seg in segments:
        by_source.setdefault(seg.source, []).append(seg)
    for segs in by_source.values():
        last: str | None = None
        for seg in segs:  # forward pass: previous labelled segment of the stream
            if seg.seq in entries:
                last = entries[seg.seq]["speaker_id"]
            elif last is not None:
                entries[seg.seq] = {"speaker_id": last, "conf": 0.0}
        nxt: str | None = None
        for seg in reversed(segs):  # leading segments: the next labelled one
            if seg.seq in entries:
                nxt = entries[seg.seq]["speaker_id"]
            elif nxt is not None:
                entries[seg.seq] = {"speaker_id": nxt, "conf": 0.0}
            else:
                entries[seg.seq] = {"speaker_id": lab.UNKNOWN_ID, "conf": 0.0}
