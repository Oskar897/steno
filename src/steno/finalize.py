"""`steno finalize`: the post-meeting pass over the kept audio.

The live transcript is written under time pressure: a 1.5 s window decides each speaker,
a VAD cut decides where speech starts, a small ASR model decides the words. Afterwards
there is time to do better, and this module does:

1. **Diarization** of every kept stream (``steno.diarize``: pyannote segmentation-3.0
   windows linked by CAM++ embeddings, clustered with Steno's spectral clustering and
   matched to enrolled profiles).
2. **ASR repair**: live lines with a low average log-probability or a repetitive text (and
   every line with ``--full-asr``) are transcribed again with ``[post] asr_model``; the new
   text replaces the old only when it is not worse.
3. **Words to speakers** (the WhisperX rule): each word goes to the speaker turn it overlaps
   most, consecutive words of one speaker become a line. Sessions recorded before word
   timestamps were stored get approximate word times (spread over the line by length).
4. **Names**: a cluster that the user named with ``steno name`` keeps that name (locked,
   by time overlap with the live lines that carried it); enrolled people matched by voice
   keep theirs; the rest are "Speaker N" in order of first appearance. Final speaker ids
   (``S<n>``, ``P:<profile id>``) are a new namespace, separate from the live ids.
5. Output, all new files (``transcript.jsonl`` is never touched): ``final.jsonl`` (schema v1
   with ``final: true``), ``final.md``, ``final.json`` (speakers, parameters, counts).

Heavy collaborators (models, audio decoding, the ASR backend) are injected through
``Deps`` so the logic can be tested with synthetic fakes.
"""

from __future__ import annotations

import logging
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from steno import __version__, labels, transcript
from steno.asr import AsrResult
from steno.config import Config
from steno.diarize import (
    DiarizationResult,
    DiarizeParams,
    SpeakerTurn,
    StreamStats,
    extract_stream,
    resolve,
)
from steno.profiles import ProfileStore, now_iso
from steno.segmentation import SAMPLE_RATE
from steno.speakers import ProfileMatcher
from steno.transcript import META_NAME, Segment, atomic_write_text, format_line
from steno.windows import Word

log = logging.getLogger(__name__)

FINAL_NAME = "final.jsonl"
FINAL_MARKDOWN_NAME = "final.md"
FINAL_META_NAME = "final.json"
MIN_REASR_S = 0.6  # shorter live lines are not worth transcribing again
REASR_PAD_S = 0.1
MAX_MERGED_S = 20.0  # joined same-speaker lines never get longer than this
NAME_MIN_S = 2.0  # seconds of overlap a cluster needs before it inherits a live name

Progress = Callable[[str, int, int], None]


class FinalizeError(ValueError):
    """User-facing problem (nothing was written)."""


# --------------------------------------------------------------------------- live lines


@dataclass
class Line:
    """One live transcript line while it is being repaired."""

    seq: int
    source: str
    t_start: float
    t_end: float
    text: str
    lang: str
    words: list[Word] | None = None  # None: no word timestamps were stored
    avg_logprob: float | None = None
    redone: bool = False

    @classmethod
    def from_segment(cls, seg: Segment) -> Line:
        words = [Word(a, b, w) for a, b, w in seg.words] if seg.words else None
        return cls(seg.seq, seg.source, seg.t_start, seg.t_end, seg.text, seg.lang, words,
                   seg.avg_logprob)


def approximate_words(text: str, t_start: float, t_end: float) -> list[Word]:
    """Word times for a line without timestamps: spread over the line, weighted by length."""
    tokens = text.split()
    if not tokens:
        return []
    weights = np.array([len(t) + 1 for t in tokens], dtype=np.float64)
    share = np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum()
    edges = t_start + (t_end - t_start) * share
    return [Word(float(edges[i]), float(edges[i + 1]), t) for i, t in enumerate(tokens)]


def line_words(line: Line) -> list[Word]:
    return line.words if line.words else approximate_words(line.text, line.t_start, line.t_end)


def compression_ratio(text: str) -> float:
    """UTF-8 bytes / zlib bytes: whisper's repetition test (loops compress extremely well)."""
    raw = text.encode("utf-8")
    return len(raw) / max(len(zlib.compress(raw)), 1) if raw else 0.0


def is_low_confidence(
    line: Line, min_logprob: float = -0.7, max_compression: float = 2.4
) -> bool:
    if line.avg_logprob is not None and line.avg_logprob < min_logprob:
        return True
    return len(line.text) >= 24 and compression_ratio(line.text) > max_compression


def accept_reasr(
    line: Line, new: AsrResult, *, full: bool, min_logprob: float, max_compression: float
) -> bool:
    """Replace the live text only when the new one is not worse.

    Empty or looping output is never accepted. With a known live confidence the new
    log-probability must not be lower (``--full-asr`` over lines whose live confidence was
    never stored accepts every sane result).
    """
    if not new.text.strip(" .,!?-…") or not new.words:
        return False
    if len(new.text) >= 24 and compression_ratio(new.text) > max_compression:
        return False
    if new.avg_logprob is None or line.avg_logprob is None:
        return full or is_low_confidence(line, min_logprob, max_compression)
    return new.avg_logprob >= line.avg_logprob


def clip_words(words: Sequence[Word], lo: float, hi: float) -> list[Word]:
    """Words whose midpoint lies in ``[lo, hi]`` (drops neighbouring speech cut in by padding)."""
    return [w for w in words if lo <= (w.start + w.end) / 2 <= hi]


# --------------------------------------------------------------------------- words -> turns


@dataclass(frozen=True)
class FinalLine:
    source: str
    t_start: float
    t_end: float
    speaker_id: str  # final id, e.g. "S1" or "P:1a2b3c4d"; "live:<seq>" = no turn found
    text: str
    lang: str
    avg_logprob: float | None = None
    words: tuple[Word, ...] = ()


def assign_line_words(
    words: Sequence[Word], turns: Sequence[tuple[float, float, str]], reach_s: float = 1.5
) -> list[tuple[str, list[Word]]]:
    """Group a line's words by speaker: ``[(speaker id, words), ...]`` in time order.

    Every word goes to the turn it overlaps most (the WhisperX rule; the first one on a tie);
    a word in a gap goes to the nearest turn within ``reach_s``, and a word with no turn
    that close gets the id ``""``. Consecutive words with the same id form one group.
    """
    if not words:
        return []
    lo, hi = words[0].start - reach_s, words[-1].end + reach_s
    near = [t for t in turns if t[1] >= lo and t[0] <= hi]
    groups: list[tuple[str, list[Word]]] = []
    for w in words:
        best, best_ov = "", 0.0
        for a, b, sid in near:
            ov = min(w.end, b) - max(w.start, a)
            if ov > best_ov:
                best, best_ov = sid, ov
        if not best:
            mid = (w.start + w.end) / 2
            dist = min(
                ((max(a - mid, mid - b, 0.0), sid) for a, b, sid in near),
                default=(np.inf, ""),
            )
            best = dist[1] if dist[0] <= reach_s else ""
        if groups and groups[-1][0] == best:
            groups[-1][1].append(w)
        else:
            groups.append((best, [w]))
    return groups


def build_final_lines(
    lines: Sequence[Line], turns_by_stream: dict[str, list[tuple[float, float, str]]]
) -> list[FinalLine]:
    """Split every live line at speaker changes (see ``assign_line_words``), time-ordered.

    Words that no final turn explains keep the speaker id ``live:<seq>`` (the live line they
    came from), so that no text is lost; the caller resolves it to the live label.
    """
    out: list[FinalLine] = []
    for line in lines:
        words = line_words(line)
        turns = turns_by_stream.get(line.source, [])
        for sid, group in assign_line_words(words, turns):
            exact = bool(line.words)
            out.append(
                FinalLine(
                    line.source,
                    group[0].start if exact else max(line.t_start, group[0].start),
                    group[-1].end if exact else min(line.t_end, group[-1].end),
                    sid or f"live:{line.seq}",
                    " ".join(w.text for w in group),
                    line.lang,
                    line.avg_logprob,
                    tuple(group) if exact else (),
                )
            )
    out.sort(key=lambda fl: (fl.t_start, fl.t_end))
    return out


def merge_lines(
    lines: Sequence[FinalLine], gap_s: float, max_s: float = MAX_MERGED_S
) -> list[FinalLine]:
    """Join consecutive lines of one speaker on one stream when the pause is short."""
    out: list[FinalLine] = []
    for fl in lines:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.source == fl.source
            and prev.speaker_id == fl.speaker_id
            and fl.t_start - prev.t_end <= gap_s
            and fl.t_end - prev.t_start <= max_s
        ):
            lp = [x for x in (prev.avg_logprob, fl.avg_logprob) if x is not None]
            out[-1] = replace(
                prev,
                t_end=max(prev.t_end, fl.t_end),
                text=f"{prev.text} {fl.text}",
                avg_logprob=float(np.mean(lp)) if lp else None,
                words=prev.words + fl.words,
            )
        else:
            out.append(fl)
    return out


# --------------------------------------------------------------------------- names


@dataclass(frozen=True)
class LiveName:
    """A live line whose speaker had a name when finalize ran."""

    source: str
    t_start: float
    t_end: float
    name: str
    profile_id: str | None
    locked: bool  # True: set by the user (`steno name`); False: an enrolled-voice match


@dataclass(frozen=True)
class ClusterName:
    speaker_id: str
    name: str
    locked: bool = False


def live_names(session_dir: Path, segments: Sequence[Segment]) -> list[LiveName]:
    """The named speakers of the live transcript (manual cluster names and profile matches)."""
    data = labels.SessionLabels(session_dir).load()
    ids = labels.effective_speaker_ids(session_dir, list(segments))
    out = []
    for seg in segments:
        sid = ids.get(seg.seq, "")
        if sid.startswith(labels.PROFILE_PREFIX):
            pid = sid[len(labels.PROFILE_PREFIX):]
            name = data.get("profiles", {}).get(pid)
            if name:
                out.append(LiveName(seg.source, seg.t_start, seg.t_end, str(name), pid, False))
            continue
        entry = data.get("clusters", {}).get(sid) or {}
        if entry.get("name"):
            pid = entry.get("profile_id")
            out.append(
                LiveName(seg.source, seg.t_start, seg.t_end, str(entry["name"]), pid, True)
            )
    return out


def vote_names(
    turns: Sequence[SpeakerTurn], live: Sequence[LiveName], min_s: float = NAME_MIN_S
) -> dict[int, LiveName]:
    """Which final cluster inherits which live name.

    Every named live line votes, with its overlap in seconds, for the clusters whose
    turns (same stream) cover it. Locked names (the user's own) are settled first, then
    profile matches; within a group the strongest (cluster, name) pair wins and each
    cluster and each name is used once. Pairs under ``min_s`` seconds are ignored.
    """
    votes: dict[tuple[bool, int, str], float] = {}
    meta: dict[str, LiveName] = {}
    by_stream: dict[str, list[SpeakerTurn]] = {}
    for t in turns:
        by_stream.setdefault(t.stream, []).append(t)
    for ln in live:
        meta.setdefault(ln.name, ln)
        for t in by_stream.get(ln.source, []):
            ov = min(ln.t_end, t.t_end) - max(ln.t_start, t.t_start)
            if ov > 0:
                key = (ln.locked, t.cluster, ln.name)
                votes[key] = votes.get(key, 0.0) + ov
    result: dict[int, LiveName] = {}
    used: set[str] = set()
    for locked in (True, False):
        ranked = sorted(
            ((v, c, n) for (lk, c, n), v in votes.items() if lk == locked), reverse=True
        )
        for v, cluster, name in ranked:
            if v < min_s or cluster in result or name in used:
                continue
            first = meta[name]
            result[cluster] = replace(first, locked=locked)
            used.add(name)
    return result


def name_final_clusters(
    diar: DiarizationResult, live: Sequence[LiveName]
) -> list[ClusterName]:
    """Final id and display name of every cluster (see the module docstring, point 4)."""
    voted = vote_names(diar.turns, live)
    first: dict[int, float] = {}
    for t in diar.turns:
        first[t.cluster] = min(first.get(t.cluster, np.inf), t.t_start)
    out: dict[int, ClusterName] = {}
    for c, ln in voted.items():
        if ln.profile_id:
            out[c] = ClusterName(labels.PROFILE_PREFIX + ln.profile_id, ln.name, ln.locked)
    for c, sid in enumerate(diar.speaker_ids):
        if c not in out and c not in voted and sid.startswith(labels.PROFILE_PREFIX):
            out[c] = ClusterName(sid, diar.profile_names.get(sid[2:], sid))
    n = 1
    for c in sorted(range(len(diar.speaker_ids)), key=lambda c: first.get(c, np.inf)):
        if c in out:
            continue
        ln = voted.get(c)
        name = ln.name if ln else f"Speaker {n}"
        out[c] = ClusterName(f"S{n}", name, bool(ln and ln.locked))
        n += 1
    return [out[c] for c in range(len(diar.speaker_ids))]


# --------------------------------------------------------------------------- orchestration


@dataclass
class Deps:
    """Heavy collaborators, replaceable in tests."""

    embedder: Any = None
    segmenter: Any = None
    audio_loader: Callable[[Path], np.ndarray] | None = None
    transcriber_factory: Callable[[], Any] | None = None


@dataclass
class FinalizeResult:
    segments: int
    live_segments: int
    redone: int = 0  # lines transcribed again and replaced
    flagged: int = 0  # lines considered for re-transcription
    speakers: list[tuple[str, str, float]] = field(default_factory=list)  # id, name, seconds
    seconds: float = 0.0
    audio_s: float = 0.0
    llm_fix: dict[str, Any] | None = None
    diar_seconds: float = 0.0


def load_flac(path: Path) -> np.ndarray:
    try:
        import soundfile
    except ImportError as exc:
        raise FinalizeError("Finalize needs the 'audio' extra (soundfile)") from exc
    data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    if rate != SAMPLE_RATE:
        raise FinalizeError(f"{path.name}: expected {SAMPLE_RATE} Hz audio, got {rate}")
    return data.mean(axis=1).astype(np.float32)


def diarize_params(cfg: Config) -> DiarizeParams:
    p = cfg.post
    return DiarizeParams(
        step_s=p.seg_step_s, min_clean_s=p.seg_min_clean_s, minor_points=p.minor_points,
        split_cos=p.split_cos if p.split_cos > 0 else None, split_min_s=p.split_min_s,
    )


def _read_meta(session_dir: Path) -> dict[str, Any]:
    import json

    try:
        data = json.loads((session_dir / META_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _say(progress: Progress | None, stage: str, done: int = 0, total: int = 0) -> None:
    if progress is not None:
        progress(stage, done, total)


def finalize_session(
    session_dir: Path,
    cfg: Config,
    store: ProfileStore,
    *,
    num_speakers: int | None = None,
    full_asr: bool = False,
    deps: Deps | None = None,
    post_edit: Callable[[list[Segment]], tuple[list[Segment], dict[str, Any]]] | None = None,
    progress: Progress | None = None,
) -> FinalizeResult:
    """Run the whole pass for one session and write ``final.*`` (see the module docstring)."""
    t_begin = time.perf_counter()
    session_dir = Path(session_dir)
    deps = deps or Deps()
    if num_speakers is not None and num_speakers < 1:
        raise FinalizeError("the number of speakers must be at least 1")
    live = transcript.read_segments(session_dir)
    if not live:
        raise FinalizeError("session has no transcript lines")
    meta = _read_meta(session_dir)
    origins = {k: float(v) for k, v in (meta.get("stream_origins") or {}).items()}
    loader = deps.audio_loader or load_flac
    streams = sorted({s.source for s in live} | set(meta.get("sources", [])))
    audio: dict[str, np.ndarray] = {}
    for name in streams:
        path = session_dir / f"audio-{name}.flac"
        if path.is_file():
            audio[name] = loader(path)
    if not audio:
        raise FinalizeError(
            "no kept audio in this session (audio-<stream>.flac); record with --keep-audio"
        )
    audio_s = max(len(a) for a in audio.values()) / SAMPLE_RATE

    # 1. diarization -------------------------------------------------------------------
    embedder, segmenter = deps.embedder, deps.segmenter
    if embedder is None:
        from steno.speakers import create_embedder

        embedder = create_embedder(replace(cfg.speakers, num_threads=cfg.post.threads))
        embedder.load()
    if segmenter is None:
        from steno.segmentation import PyannoteSegmenter

        segmenter = PyannoteSegmenter(num_threads=cfg.post.threads)
        segmenter.load()
    params = diarize_params(cfg)
    t_diar = time.perf_counter()
    stats: list[StreamStats] = [
        extract_stream(name, a, segmenter, embedder, params,
                       (lambda st, d, n, name=name: _say(progress, f"{st} {name}", d, n)))
        for name, a in audio.items()
    ]
    matcher = ProfileMatcher(store.list(), cfg.speakers.accept_threshold, cfg.speakers.margin)
    diar = resolve(stats, params, cfg.speakers, num_speakers=num_speakers, matcher=matcher)
    diar_seconds = time.perf_counter() - t_diar
    shifted = [
        replace(t, t_start=t.t_start + origins.get(t.stream, 0.0),
                t_end=t.t_end + origins.get(t.stream, 0.0))
        for t in diar.turns
    ]
    diar = replace(diar, turns=shifted)
    names = name_final_clusters(diar, live_names(session_dir, live))

    # 2. ASR repair --------------------------------------------------------------------
    lines = [Line.from_segment(s) for s in live]
    flagged = [
        i for i, ln in enumerate(lines)
        if ln.source in audio and (ln.t_end - ln.t_start) >= MIN_REASR_S
        and (full_asr or is_low_confidence(ln, cfg.post.asr_min_logprob,
                                           cfg.post.asr_max_compression))
    ]
    redone = 0
    if flagged:
        transcriber = (deps.transcriber_factory or _default_transcriber(cfg))()
        transcriber.load()
        for n, i in enumerate(flagged):
            _say(progress, "transcribing", n, len(flagged))
            ln = lines[i]
            off = origins.get(ln.source, 0.0)
            a0 = max(0.0, ln.t_start - off - REASR_PAD_S)
            a1 = ln.t_end - off + REASR_PAD_S
            clip = audio[ln.source][int(a0 * SAMPLE_RATE) : int(a1 * SAMPLE_RATE)]
            try:
                res = _transcribe_clip(transcriber, clip, cfg.post.asr_beam_size)
            except Exception:
                log.exception("Re-transcription failed for line %d", ln.seq)
                continue
            start = a0 + off
            words = clip_words(
                [Word(start + w.start, start + w.end, w.text) for w in res.words],
                ln.t_start - REASR_PAD_S, ln.t_end + REASR_PAD_S,
            )
            res = replace(res, words=words, text=" ".join(w.text for w in words))
            if accept_reasr(ln, res, full=full_asr, min_logprob=cfg.post.asr_min_logprob,
                            max_compression=cfg.post.asr_max_compression):
                lines[i] = replace(ln, text=res.text, words=words, avg_logprob=res.avg_logprob,
                                   lang=res.lang or ln.lang, redone=True)
                redone += 1

    # 3. words -> speakers ---------------------------------------------------------------
    by_stream: dict[str, list[tuple[float, float, str]]] = {}
    for t in diar.turns:
        by_stream.setdefault(t.stream, []).append((t.t_start, t.t_end, names[t.cluster].speaker_id))
    final_lines = merge_lines(build_final_lines(lines, by_stream), cfg.post.merge_gap_s)

    # 4. write ---------------------------------------------------------------------------
    started = _started_at(meta, session_dir)
    display = {n.speaker_id: n.name for n in names}
    live_name = {s.seq: s.speaker for s in labels.resolve_segments(session_dir, live)}
    out: list[Segment] = []
    for seq, fl in enumerate(final_lines, start=1):
        sid = fl.speaker_id
        if sid.startswith("live:"):  # no final turn explained these words: keep the live label
            speaker = live_name.get(int(sid[5:]), labels.UNKNOWN_NAME)
            sid = labels.UNKNOWN_ID
        else:
            speaker = display.get(sid, labels.UNKNOWN_NAME)
        out.append(
            Segment(
                seq=seq,
                t_start=round(fl.t_start, 2),
                t_end=round(fl.t_end, 2),
                wall=(started + timedelta(seconds=fl.t_start)).isoformat(timespec="seconds"),
                source=fl.source,
                speaker=speaker,
                text=fl.text,
                lang=fl.lang,
                speaker_id=sid,
                avg_logprob=None if fl.avg_logprob is None else round(fl.avg_logprob, 3),
                words=[[round(w.start, 2), round(w.end, 2), w.text] for w in fl.words] or None,
                final=True,
            )
        )
    fix_report = None
    if post_edit is not None and out:
        _say(progress, "llm-fix")
        out, fix_report = post_edit(out)

    seconds_by_id: dict[str, float] = {}
    for s in out:
        seconds_by_id[s.speaker_id or ""] = seconds_by_id.get(s.speaker_id or "", 0.0) + (
            s.t_end - s.t_start)
    unique = {c.speaker_id: c for c in names}  # clusters sharing one person count once
    speakers = [(c.speaker_id, c.name, seconds_by_id.get(c.speaker_id, 0.0))
                for c in unique.values()]
    speakers = [t for t in speakers if t[2] > 0]
    result = FinalizeResult(
        len(out), len(live), redone, len(flagged), sorted(speakers, key=lambda t: -t[2]),
        time.perf_counter() - t_begin, audio_s, fix_report, diar_seconds,
    )
    _write_outputs(session_dir, out, names, result, cfg, num_speakers, full_asr)
    return result


def _default_transcriber(cfg: Config):
    def make():
        from steno.asr import create_transcriber

        return create_transcriber(replace(cfg.asr, model=cfg.post.asr_model,
                                          beam_size=cfg.post.asr_beam_size))

    return make


def _transcribe_clip(transcriber, clip: np.ndarray, beam_size: int) -> AsrResult:
    detailed = getattr(transcriber, "transcribe_detailed", None)
    if detailed is not None:
        return detailed(clip, beam_size=beam_size)
    text, lang, words = transcriber.transcribe_words(clip)
    return AsrResult(text, lang, list(words))


def _started_at(meta: dict[str, Any], session_dir: Path) -> datetime:
    try:
        return datetime.fromisoformat(str(meta["started_at"]))
    except (KeyError, ValueError):
        try:
            return datetime.strptime(session_dir.name, transcript.SESSION_ID_FORMAT)
        except ValueError:
            return datetime.now().replace(microsecond=0)


def _write_outputs(
    session_dir: Path,
    segments: list[Segment],
    names: list[ClusterName],
    result: FinalizeResult,
    cfg: Config,
    num_speakers: int | None,
    full_asr: bool,
) -> None:
    import json

    atomic_write_text(session_dir / FINAL_NAME, "".join(s.to_json() + "\n" for s in segments))
    atomic_write_text(
        session_dir / FINAL_MARKDOWN_NAME, "".join(format_line(s) + "\n\n" for s in segments)
    )
    meta = {
        "v": 1,
        "created": now_iso(),
        "steno_version": __version__,
        "segments": len(segments),
        "live_segments": result.live_segments,
        "speakers": [
            {"id": sid, "name": name, "seconds": round(sec, 1),
             "locked": any(n.speaker_id == sid and n.locked for n in names)}
            for sid, name, sec in result.speakers
        ],
        "diarization": "pyannote-segmentation-3.0 + CAM++ + spectral clustering",
        "asr_model": cfg.post.asr_model,
        "asr_redone": result.redone,
        "asr_flagged": result.flagged,
        "full_asr": full_asr,
        "num_speakers": num_speakers,
        "llm_fix": result.llm_fix,
        "seconds": round(result.seconds, 1),
        "audio_seconds": round(result.audio_s, 1),
    }
    atomic_write_text(session_dir / FINAL_META_NAME, json.dumps(meta, indent=2) + "\n")


def has_final(session_dir: Path) -> bool:
    return (Path(session_dir) / FINAL_NAME).is_file()


def read_final(session_dir: Path) -> list[Segment]:
    return transcript.read_segments(session_dir, name=FINAL_NAME)
