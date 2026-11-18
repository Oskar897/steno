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
from steno.config import SpeakersConfig
from steno.profiles import Profile, ProfileStore, SessionEmbeddings, now_iso
from steno.speakers import (
    Embedder,
    ProfileMatcher,
    agglomerative_average,
    cosine,
    l2_normalize,
)
from steno.vad import SileroVad, SpeechProbModel, SpeechSegmenter, VadParams

NAME_MIN_SEGMENT_S = 2.0  # `steno name` only learns from segments at least this long


class OpsError(ValueError):
    """A user-facing problem with an operation (nothing was changed)."""


# --------------------------------------------------------------------------- enrol


@dataclass(frozen=True)
class EnrollResult:
    profile: Profile
    created: bool
    used: int  # speech chunks embedded this time
    total: int  # embeddings now in the profile


def enroll_audio(
    audio: np.ndarray,
    name: str,
    store: ProfileStore,
    embedder: Embedder,
    *,
    min_chunk_s: float = 1.0,
    vad_factory: Callable[[], SpeechProbModel] = SileroVad,
) -> EnrollResult:
    """Embed the speech chunks of ``audio`` (one person talking) into a new or existing profile."""
    segmenter = SpeechSegmenter(vad_factory(), VadParams())
    utterances = segmenter.feed(np.asarray(audio, dtype=np.float32)) + segmenter.flush()
    chunks = [u for u in utterances if u.duration >= min_chunk_s]
    if not chunks:
        raise OpsError(
            f"no speech of at least {min_chunk_s:g} s found; speak continuously and close to "
            "the microphone"
        )
    embeddings = np.vstack([embedder.embed(u.audio) for u in chunks])
    tag = f"enroll:{now_iso()}"
    existing = store.find_by_name(name)
    if existing is None:
        profile = store.create(name, embeddings, tag)
    else:
        profile = store.add_embeddings(existing.id, embeddings, tag)
    return EnrollResult(profile, existing is None, len(chunks), len(profile.embeddings))


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


def refine_session(session_dir: Path, cfg: SpeakersConfig, store: ProfileStore) -> RefineResult:
    """Offline re-labelling: cluster all session embeddings, then match clusters to profiles.

    Writes ``labels.json`` (the overlay readers prefer over live labels), replaces the
    cluster namespace in ``speakers.json`` (unmatched clusters become S1, S2, ... in
    order of first appearance, names given to clusters earlier are dropped but live on
    in the profiles) and regenerates ``transcript.md``. The JSONL is not touched.
    """
    session_dir = Path(session_dir)
    segments = transcript.read_segments(session_dir)
    if not segments:
        raise OpsError("session has no segments")
    emb = SessionEmbeddings.load(session_dir)
    embedded = [s for s in segments if s.seq in emb]
    if not embedded:
        raise OpsError("session has no speaker embeddings (recorded without speaker recognition?)")

    matrix = np.vstack([emb[s.seq] for s in embedded])
    cluster_of = agglomerative_average(matrix, cfg.refine_threshold)
    matcher = ProfileMatcher(store.list(), cfg.accept_threshold, cfg.margin)

    ids: dict[int, str] = {}  # cluster index -> speaker id
    profile_names: dict[str, str] = {}
    centroids: dict[int, np.ndarray] = {}
    next_n = 1
    for k in range(int(cluster_of.max()) + 1):  # numbered by first appearance
        centroids[k] = l2_normalize(matrix[cluster_of == k].mean(axis=0))
        match = matcher.match(centroids[k])
        if match.accepted and match.profile is not None:
            ids[k] = lab.PROFILE_PREFIX + match.profile.id
            profile_names[match.profile.id] = match.profile.name
        else:
            ids[k] = f"S{next_n}"
            next_n += 1

    entries: dict[int, dict] = {}
    for seg, k in zip(embedded, cluster_of, strict=True):
        entries[seg.seq] = {
            "speaker_id": ids[int(k)],
            "conf": round(cosine(emb[seg.seq], centroids[int(k)]), 3),
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
