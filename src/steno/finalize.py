"""`steno finalize`: the post-meeting pass over the kept audio.

The live transcript is written under time pressure: a 1.5 s window decides each speaker,
a VAD cut decides where speech starts, a small ASR model decides the words. Afterwards
there is time to do better, and this module does:

1. **Diarization**, by ``[post] diarization``:

   - ``fast`` (default): the live window embeddings (``windows.bin``) are clustered again
     with all windows at once (``steno.diarize.diarize_windows``): seconds, no audio needed,
     the accuracy of ``steno refine``.
   - ``thorough``: every kept stream goes through ``steno.diarize`` (pyannote
     segmentation-3.0 windows linked by CAM++ embeddings, clustered with Steno's spectral
     clustering and matched to enrolled profiles). Needs kept audio and takes about 0.1 x
     the audio length, but is much more accurate (AMI far-field: DER 18 % vs 31 %).
2. **ASR**, by ``[post] asr_mode``:

   - ``refined`` (default): the text the rolling refine worker wrote during the meeting
     (``revisions.jsonl``, see ``steno.refine``); no new ASR and no kept audio needed. A
     session without revisions that has kept audio falls back to ``longform``.
   - ``longform``: every kept stream is transcribed once as a whole with
     ``[post] asr_model`` (a large model is affordable that way: whole-recording decoding runs
     in batches of 30 s windows and sees whole sentences, where line-by-line decoding pads
     every clip to 30 s). The live lines are replaced by the new words; see "Echo" below.
   - ``selective``: live lines with a low average log-probability or a repetitive text (and
     every line with ``--full-asr``) are transcribed again one by one; the new text replaces
     the old only when it is not worse.
   - ``off``: the live draft words are kept.
3. **Words to speakers** (the WhisperX rule): each word goes to the speaker turn it overlaps
   most, consecutive words of one speaker become a line. Sessions recorded before word
   timestamps were stored get approximate word times (spread over the line by length).
4. **Names**: a cluster that the user named with ``steno name`` keeps that name (locked,
   by time overlap with the live lines that carried it); enrolled people matched by voice
   keep theirs; the rest are "Speaker N" in order of first appearance. Final speaker ids
   (``S<n>``, ``P:<profile id>``) are a new namespace, separate from the live ids.
5. Output, all new files (``transcript.jsonl`` is never touched): ``final.jsonl`` (schema v1
   with ``final: true``), ``final.md``, ``final.json`` (speakers, parameters, counts).

Echo (longform only). The live pipeline drops microphone utterances that are just the
loopback audio leaking into the mic and writes nothing about them, so a whole-file
transcription of ``audio-mic.flac`` would bring that text back as duplicates of the remote
side. The rule (``drop_echo_words``): a mic word is kept when it lies inside a live mic line
(+- ``ECHO_PAD_S``; the live pass judged that speech real) or when no loopback speech is
going on at that time; otherwise (it overlaps loopback speech and the live pass kept no mic
line there) it is dropped. Loopback speech is the union of the live loopback lines and the
new loopback words. Applies only when the session has both streams and ``[echo]`` is enabled.

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
from steno.asr import AsrResult, LongSegment
from steno.config import ASR_MODES, Config
from steno.diarize import (
    DiarizationResult,
    DiarizeParams,
    SpeakerTurn,
    StreamStats,
    diarize_windows,
    extract_stream,
    resolve,
)
from steno.profiles import ProfileStore, SessionWindows, now_iso
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
ECHO_PAD_S = 0.4  # a mic word this close to a live mic line still counts as inside it

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


# --------------------------------------------------------------------------- longform ASR


def merge_spans(
    spans: Sequence[tuple[float, float]], pad: float = 0.0
) -> list[tuple[float, float]]:
    """Sorted, merged ``(start, end)`` spans, each widened by ``pad`` on both sides."""
    out: list[tuple[float, float]] = []
    for a, b in sorted((a - pad, b + pad) for a, b in spans if b >= a):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _inside(t: float, spans: Sequence[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in spans)


def drop_echo_words(
    words: Sequence[Word],
    live_mic: Sequence[tuple[float, float]],
    loopback: Sequence[tuple[float, float]],
    pad: float = ECHO_PAD_S,
) -> tuple[list[Word], list[Word]]:
    """Split mic words into ``(kept, echo)`` (see "Echo" in the module docstring).

    ``live_mic`` / ``loopback`` are the time spans of the live mic lines and of the loopback
    speech. A word is judged by its midpoint.
    """
    inside_live = merge_spans(live_mic, pad)
    loud = merge_spans(loopback)
    kept: list[Word] = []
    echo: list[Word] = []
    for w in words:
        mid = (w.start + w.end) / 2
        if _inside(mid, inside_live) or not _inside(mid, loud):
            kept.append(w)
        else:
            echo.append(w)
    return kept, echo


def split_long_words(words: Sequence[Word], max_s: float = MAX_MERGED_S) -> list[list[Word]]:
    """Cut a word run longer than ``max_s`` at its longest pauses (recursively)."""
    if len(words) < 2 or words[-1].end - words[0].start <= max_s:
        return [list(words)] if words else []
    i = max(range(len(words) - 1), key=lambda k: words[k + 1].start - words[k].end)
    return split_long_words(words[: i + 1], max_s) + split_long_words(words[i + 1 :], max_s)


def segments_to_lines(
    segments: Sequence[LongSegment], source: str, lang: str, offset: float,
    max_compression: float = 2.4,
) -> list[Line]:
    """Long-form model segments -> ``Line`` objects in session time (``seq`` still 0).

    Looping segments (hallucinations) and segments without words are dropped.
    """
    out: list[Line] = []
    for seg in segments:
        text = " ".join(w.text for w in seg.words)
        if not seg.words or not text.strip(" .,!?-…"):
            continue
        if len(text) >= 24 and compression_ratio(text) > max_compression:
            log.warning("Dropped a looping long-form segment at %.1fs on %s", seg.start, source)
            continue
        shifted = [Word(w.start + offset, w.end + offset, w.text) for w in seg.words]
        for part in split_long_words(shifted):
            out.append(
                Line(0, source, part[0].start, part[-1].end, " ".join(w.text for w in part),
                     lang, part, seg.avg_logprob, redone=True)
            )
    return out


def nearest_live_seq(line: Line, live: Sequence[Line], reach_s: float = 2.0) -> int:
    """``seq`` of the live line (same stream) overlapping ``line`` most, else the closest one
    within ``reach_s``, else 0. Only used to find a label for words no final turn explains."""
    best, best_key = 0, (-1.0, -np.inf)
    for other in live:
        if other.source != line.source:
            continue
        ov = min(line.t_end, other.t_end) - max(line.t_start, other.t_start)
        gap = max(other.t_start - line.t_end, line.t_start - other.t_end, 0.0)
        if ov <= 0 and gap > reach_s:
            continue
        key = (max(ov, 0.0), -gap)
        if key > best_key:
            best, best_key = other.seq, key
    return best


def restrict_line(line: Line, kept: Sequence[Word]) -> list[Line]:
    """``line`` reduced to the ``kept`` words, cut where words were removed in the middle."""
    own = line.words or []
    if len(kept) == len(own):
        return [line]
    pos = {id(w): i for i, w in enumerate(own)}
    runs: list[list[Word]] = []
    for w in kept:
        if runs and pos[id(w)] == pos[id(runs[-1][-1])] + 1:
            runs[-1].append(w)
        else:
            runs.append([w])
    return [
        replace(line, t_start=r[0].start, t_end=r[-1].end, text=" ".join(w.text for w in r),
                words=r)
        for r in runs
    ]


@dataclass
class LongformReport:
    lines: int = 0
    words: int = 0
    echo_dropped: int = 0  # mic words removed by the echo rule
    fallback: list[str] = field(default_factory=list)  # streams that kept their live lines
    seconds: float = 0.0


def longform_lines(
    audio: dict[str, np.ndarray],
    origins: dict[str, float],
    live: Sequence[Line],
    transcriber: Any,
    *,
    batch_size: int,
    beam_size: int,
    max_compression: float = 2.4,
    echo_enabled: bool = True,
    detect_english: bool = False,
    progress: Progress | None = None,
) -> tuple[list[Line], LongformReport]:
    """Transcribe every kept stream once and return the lines that replace the live ones.

    Streams without kept audio keep their live lines; so does a stream the long-form pass
    could not transcribe (error) or found no speech in while the live pass did (the live
    lines are then the better bet). Loopback goes first: its words (with the live loopback
    lines) tell the echo rule when the remote side was talking.
    """
    report = LongformReport()
    t_begin = time.perf_counter()
    fn = getattr(transcriber, "transcribe_longform", None)
    if fn is None:
        raise FinalizeError(
            'this ASR backend has no long-form mode; set [post] asr_mode = "selective" or "off"'
        )
    live_by: dict[str, list[Line]] = {}
    for ln in live:
        live_by.setdefault(ln.source, []).append(ln)
    result: dict[str, list[Line]] = {}
    for name in sorted(audio, key=lambda n: (n != "loopback", n)):
        label = f"transcribing {name} (s)"

        def tick(done: float, total: float, label: str = label) -> None:
            _say(progress, label, int(done), int(total))

        _say(progress, label, 0, int(len(audio[name]) / SAMPLE_RATE))
        try:
            extra = {"detect_english": True} if detect_english else {}
            segs, lang = fn(audio[name], batch_size=batch_size, beam_size=beam_size,
                            progress=tick, **extra)
        except Exception:
            log.exception("Long-form transcription of %s failed; keeping its live lines", name)
            report.fallback.append(name)
            continue
        new = segments_to_lines(segs, name, lang, origins.get(name, 0.0), max_compression)
        if not new and live_by.get(name):
            log.warning("Long-form pass found no speech on %s; keeping its live lines", name)
            report.fallback.append(name)
            continue
        result[name] = new
    if echo_enabled and {"mic", "loopback"} <= set(audio) and "mic" in result:
        loop_spans = [(ln.t_start, ln.t_end) for ln in result.get("loopback", [])]
        loop_spans += [(ln.t_start, ln.t_end) for ln in live_by.get("loopback", [])]
        live_mic = [(ln.t_start, ln.t_end) for ln in live_by.get("mic", [])]
        mic: list[Line] = []
        for ln in result["mic"]:
            kept, echo = drop_echo_words(ln.words or [], live_mic, loop_spans)
            report.echo_dropped += len(echo)
            mic.extend(restrict_line(ln, kept))
        result["mic"] = mic
    out: list[Line] = []
    for name in sorted({ln.source for ln in live} | set(result)):
        if name in result:
            for ln in result[name]:
                out.append(replace(ln, seq=nearest_live_seq(ln, live_by.get(name, []))))
                report.lines += 1
                report.words += len(ln.words or [])
        else:
            out.extend(live_by.get(name, []))
    out.sort(key=lambda ln: (ln.t_start, ln.t_end))
    report.seconds = time.perf_counter() - t_begin
    return out, report


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
    asr_mode: str = "selective"
    longform: LongformReport | None = None
    diarization: str = "thorough"  # "fast", "thorough" or "none" (no window embeddings)
    revised: int = 0  # refined mode: live lines whose text is a refine revision
    fell_back: bool = False  # refined mode asked for, but the session had no revisions
    diar_source: str = ""  # fast: "incremental" (diar.bin) or "windows" (windows.bin)


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
    asr_mode: str | None = None,
    thorough: bool | None = None,
    deps: Deps | None = None,
    post_edit: Callable[[list[Segment]], tuple[list[Segment], dict[str, Any]]] | None = None,
    progress: Progress | None = None,
) -> FinalizeResult:
    """Run the whole pass for one session and write ``final.*`` (see the module docstring).

    ``asr_mode`` overrides ``[post] asr_mode``, ``thorough`` (True: pyannote over the kept
    audio, False: the live window embeddings) overrides ``[post] diarization``. ``full_asr``
    only matters in ``selective`` mode (every live line is transcribed again); ``longform``
    transcribes everything anyway.
    """
    t_begin = time.perf_counter()
    mode = asr_mode or cfg.post.asr_mode
    if mode not in ASR_MODES:
        raise FinalizeError(f"unknown ASR mode {mode!r}; use one of: {', '.join(ASR_MODES)}")
    session_dir = Path(session_dir)
    deps = deps or Deps()
    if num_speakers is not None and num_speakers < 1:
        raise FinalizeError("the number of speakers must be at least 1")
    drafts = transcript.read_segments(session_dir)
    if not drafts:
        raise FinalizeError("session has no transcript lines")
    if thorough is None:
        thorough = cfg.post.diarization == "thorough"
    meta = _read_meta(session_dir)
    origins = {k: float(v) for k, v in (meta.get("stream_origins") or {}).items()}
    loader = deps.audio_loader or load_flac
    streams = sorted({s.source for s in drafts} | set(meta.get("sources", [])))
    kept = {n: session_dir / f"audio-{n}.flac" for n in streams
            if (session_dir / f"audio-{n}.flac").is_file()}
    revisions = transcript.read_revisions(session_dir)
    fell_back = False
    if mode == "refined" and not revisions and kept:
        log.info("No refine revisions in this session: using long-form ASR instead")
        mode, fell_back = "longform", True
    live = drafts if mode == "off" else transcript.read_best(session_dir)
    revised = sum(1 for s in live if s.rev is not None)
    audio: dict[str, np.ndarray] = {}
    if thorough or mode in ("longform", "selective"):
        audio = {name: loader(path) for name, path in kept.items()}
        if not audio:
            raise FinalizeError(
                "no kept audio in this session (audio-<stream>.flac); record with --keep-audio, "
                "or use the defaults ([post] asr_mode = \"refined\", diarization = \"fast\")"
            )
    audio_s = (
        max(len(a) for a in audio.values()) / SAMPLE_RATE if audio
        else max(s.t_end for s in live)
    )

    # 1. diarization -------------------------------------------------------------------
    params = diarize_params(cfg)
    matcher = ProfileMatcher(store.list(), cfg.speakers.accept_threshold, cfg.speakers.margin)
    t_diar = time.perf_counter()
    if thorough:
        embedder, segmenter = deps.embedder, deps.segmenter
        if embedder is None:
            from steno.speakers import create_embedder

            embedder = create_embedder(replace(cfg.speakers, num_threads=cfg.post.threads))
            embedder.load()
        if segmenter is None:
            from steno.segmentation import PyannoteSegmenter

            segmenter = PyannoteSegmenter(num_threads=cfg.post.threads)
            segmenter.load()
        stats: list[StreamStats] = [
            extract_stream(name, a, segmenter, embedder, params,
                           (lambda st, d, n, name=name: _say(progress, f"{st} {name}", d, n)))
            for name, a in audio.items()
        ]
        diar = resolve(stats, params, cfg.speakers, num_speakers=num_speakers, matcher=matcher)
        shifted = [
            replace(t, t_start=t.t_start + origins.get(t.stream, 0.0),
                    t_end=t.t_end + origins.get(t.stream, 0.0))
            for t in diar.turns
        ]
        diar = replace(diar, turns=shifted)
        diarization = "thorough"
        diar_source = "audio"
    else:
        _say(progress, "clustering speakers")
        incremental = _load_incremental(session_dir)
        windows = SessionWindows.load(session_dir)
        if incremental:
            diar = resolve(incremental, params, cfg.speakers, num_speakers=num_speakers,
                           matcher=matcher)
            diar = replace(diar, turns=[
                replace(t, t_start=t.t_start + origins.get(t.stream, 0.0),
                        t_end=t.t_end + origins.get(t.stream, 0.0))
                for t in diar.turns
            ])
            diarization, diar_source = "fast", "incremental"
        elif windows is not None and len(windows):
            diar = diarize_windows(
                windows.stream, windows.t0, windows.t1, windows.emb, cfg.speakers, params,
                num_speakers=num_speakers, matcher=matcher,
            )
            diarization, diar_source = "fast", "windows"
        else:
            log.warning("No window embeddings in this session: the live speaker labels are kept")
            diar = DiarizationResult([], [], {}, np.zeros((0, 0), dtype=np.float32), 0)
            diarization, diar_source = "none", ""
    diar_seconds = time.perf_counter() - t_diar
    names = name_final_clusters(diar, live_names(session_dir, live))

    # 2. ASR ---------------------------------------------------------------------------
    lines = [Line.from_segment(s) for s in live]
    flagged = [
        i for i, ln in enumerate(lines)
        if mode == "selective" and ln.source in audio and (ln.t_end - ln.t_start) >= MIN_REASR_S
        and (full_asr or is_low_confidence(ln, cfg.post.asr_min_logprob,
                                           cfg.post.asr_max_compression))
    ]
    redone = 0
    report: LongformReport | None = None
    if mode == "longform":
        transcriber = (deps.transcriber_factory or _default_transcriber(cfg, longform=True))()
        _load(transcriber, cfg.post.asr_model)
        lines, report = longform_lines(
            audio, origins, lines, transcriber,
            batch_size=cfg.post.longform_batch_size, beam_size=cfg.post.longform_beam_size,
            max_compression=cfg.post.asr_max_compression, echo_enabled=cfg.echo.enabled,
            detect_english=cfg.refine.detect_english, progress=progress,
        )
        redone = report.lines
    elif flagged:
        transcriber = (deps.transcriber_factory or _default_transcriber(cfg))()
        _load(transcriber, cfg.post.asr_model)
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
    if not names:  # no diarization at all: the live labels are all there is
        by_name: dict[str, float] = {}
        for s in out:
            by_name[s.speaker] = by_name.get(s.speaker, 0.0) + (s.t_end - s.t_start)
        speakers = [(labels.UNKNOWN_ID, name, secs) for name, secs in by_name.items()]
    result = FinalizeResult(
        len(out), len(live), redone, len(flagged), sorted(speakers, key=lambda t: -t[2]),
        time.perf_counter() - t_begin, audio_s, fix_report, diar_seconds, mode, report,
        diarization, revised, fell_back, diar_source,
    )
    _write_outputs(session_dir, out, names, result, cfg, num_speakers, full_asr)
    return result


def _load_incremental(session_dir: Path) -> list[StreamStats] | None:
    """``diar.bin`` of a session recorded with ``[refine] diarize`` (None: absent or unreadable)."""
    from steno import livediar

    try:
        return livediar.load_stats(session_dir)
    except Exception:
        log.exception("Could not read %s; clustering the window embeddings instead",
                      livediar.DIAR_NAME)
        return None


def _default_transcriber(cfg: Config, *, longform: bool = False):
    """The ``[post] asr_model`` backend with the ``[asr]`` language and initial prompt."""

    def make():
        from steno.asr import create_transcriber

        beam = cfg.post.longform_beam_size if longform else cfg.post.asr_beam_size
        return create_transcriber(replace(cfg.asr, model=cfg.post.asr_model, beam_size=beam))

    return make


def _load(transcriber, model: str) -> None:
    try:
        transcriber.load()
    except Exception as exc:
        log.exception("Could not load ASR model %s", model)
        raise FinalizeError(
            f"could not load the ASR model {model!r} ({type(exc).__name__}: {exc}); "
            "check [post] asr_model, or use `--asr-mode off`"
        ) from exc


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
        "diarization": (
            "pyannote-segmentation-3.0 + CAM++ + spectral clustering"
            if result.diarization == "thorough"
            else "live window embeddings + spectral clustering (fast)"
            if result.diarization == "fast"
            else "none (live speaker labels)"
        ),
        "diarization_mode": result.diarization,
        "diarization_source": result.diar_source or None,
        "revised_lines": result.revised if result.asr_mode == "refined" else None,
        "asr_fell_back_to_longform": result.fell_back,
        "asr_mode": result.asr_mode,
        "asr_model": (
            None if result.asr_mode in ("off", "refined") else cfg.post.asr_model
        ),
        "asr_redone": result.redone,
        "asr_flagged": result.flagged,
        "full_asr": full_asr and result.asr_mode == "selective",
        "longform": None if result.longform is None else {
            "lines": result.longform.lines,
            "words": result.longform.words,
            "echo_words_dropped": result.longform.echo_dropped,
            "streams_kept_live": result.longform.fallback,
            "seconds": round(result.longform.seconds, 1),
        },
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
