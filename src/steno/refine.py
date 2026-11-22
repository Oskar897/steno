"""Rolling refinement: better text for the live transcript while the meeting is still going.

The live draft is transcribed one VAD utterance at a time (a few seconds of lag, but little
context, so WER is high). In the background a second, slower pass looks at windows of
accumulated speech and decodes each as long-form audio with an optional bigger model:

    pipeline thread --feed(samples)--> AudioRing (per stream, int16, bounded)
    ASR thread      --note_line(seg)--> pending lines per stream
    refine thread:  pick a window (>= window_speech_s of speech, cut at a pause)
                    -> long-form ASR of the window audio (speech of the live lines only)
                    -> words assigned to the live lines by time
                    -> revisions (``revisions.jsonl``), same ``seq``, same speaker.

Lines keep their ``seq``, speaker and times: the revision only carries a new text (and word
timestamps), so every label layer (live clusters, ``labels.json``, names) stays attached.

Echo and the gates stay consistent because refinement can only *replace* the text of lines
the live pass already wrote: the audio outside the live lines (mic speech the echo gate
dropped as loopback leakage, noise) is muted before decoding and words that do not fall
inside a line are discarded, so dropped echo can not come back.

Never starve the live draft: at most one job at a time, the worker yields between model
segments while live utterances wait for transcription, and (Windows) runs at below-normal
thread priority. When the worker falls behind (more unrefined speech than
``max_backlog_windows`` windows) it switches to ``fallback_model`` if one is configured, and
beyond ``skip_after_windows`` windows the oldest speech is skipped for good (its draft
stays). Stopping flushes only what is still pending.

All model work goes through ``transcribe_longform`` of the transcriber, so tests can use a
fake; the audio ring, the window planning and the word assignment are plain numpy/python.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from steno.config import Config, RefineConfig
from steno.finalize import compression_ratio
from steno.transcript import Segment, same_words
from steno.windows import Word

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MIN_LINE_S = 0.3  # shorter lines are not worth a window of their own
MIN_AUDIO_S = 0.5  # shorter windows are skipped
MASK_PAD_S = 0.3  # audio kept around every live line when muting the rest of a window
ASSIGN_PAD_S = 0.3  # a word this close to a line (and in no other) still belongs to it
MAX_YIELD_S = 2.0  # how long the worker waits between model segments for the live ASR
MIN_WORD_RATIO = 0.4  # a revision with fewer words than this x the draft's is not trusted ...
MAX_WORD_RATIO = 3.0  # ... nor one with more than this x (+3) the draft's words

#: ``emit(items, model)``: items are ``(seq, text, {"lang", "words", "avg_logprob"})``.
Emit = Callable[[list[tuple[int, str, dict]], str], None]


class _Aborted(Exception):
    pass


class _Requeue(Exception):
    """The window is given up at stop so the cheaper fallback can do it within the budget."""


#: Seconds of decoding per second of speech assumed for ``[refine] model`` before the first
#: window has been measured (turbo on a 4-core laptop CPU is about this) ...
DEFAULT_RATE = 1.1
#: ... or, when the model id contains one of these, that rate (measured in live replays on an
#: i5-1235U with the live draft running next to it).
KNOWN_RATES = {"kb-whisper-small": 0.3, "kb-whisper-medium": 0.65, "turbo": 1.1}


def default_rate(model_name: str | None) -> float:
    """The assumed decode rate of ``model_name`` before its first window was measured."""
    name = (model_name or "").lower()
    return next((r for key, r in KNOWN_RATES.items() if key in name), DEFAULT_RATE)


# --------------------------------------------------------------------------- audio ring


class AudioRing:
    """The last ``capacity_s`` seconds of one stream as int16, addressed in stream time.

    ``origin`` is the stream time of the first sample (the engine's ``_Stream.origin``), so
    ``get(t0, t1)`` takes the same times as the live lines. Older audio is dropped block by
    block; 16 bit halves the memory of float32 (about 1.9 MB per minute) and is what the
    kept FLAC holds anyway.
    """

    def __init__(self, capacity_s: float, rate: int = SAMPLE_RATE):
        self.rate = rate
        self._cap = max(int(capacity_s * rate), rate)
        self._blocks: deque[tuple[int, np.ndarray]] = deque()  # (first sample index, samples)
        self._n = 0  # samples ever appended
        self.origin = 0.0
        self._lock = threading.Lock()

    def append(self, samples: np.ndarray, origin: float | None = None) -> None:
        if not samples.size:
            return
        block = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        with self._lock:
            if not self._blocks and self._n == 0 and origin is not None:
                self.origin = origin
            self._blocks.append((self._n, block))
            self._n += len(block)
            while len(self._blocks) > 1 and self._n - self._blocks[1][0] >= self._cap:
                self._blocks.popleft()

    @property
    def start_s(self) -> float:
        """Stream time of the oldest sample still held."""
        with self._lock:
            first = self._blocks[0][0] if self._blocks else self._n
            return self.origin + first / self.rate

    @property
    def end_s(self) -> float:
        """Stream time just after the newest sample."""
        with self._lock:
            return self.origin + self._n / self.rate

    @property
    def n_samples(self) -> int:
        """Samples ever appended (the stream's sample index just after the newest one)."""
        with self._lock:
            return self._n

    def read(self, first: int, last: int) -> np.ndarray | None:
        """float32 samples ``[first, last)`` by stream sample index; None if any of them has
        already been dropped (or does not exist yet)."""
        with self._lock:
            blocks = list(self._blocks)
            n = self._n
        if not blocks or first < blocks[0][0] or last > n or last <= first:
            return None
        parts = []
        for start, block in blocks:
            end = start + len(block)
            if end <= first or start >= last:
                continue
            parts.append(block[max(first - start, 0) : last - start])
        return np.concatenate(parts).astype(np.float32) / 32767.0

    @property
    def nbytes(self) -> int:
        with self._lock:
            return sum(b.nbytes for _, b in self._blocks)

    def get(self, t0: float, t1: float) -> tuple[np.ndarray, float]:
        """``(audio float32, actual start time)`` of ``[t0, t1]`` clipped to what is held."""
        with self._lock:
            blocks = list(self._blocks)
            n = self._n
        if not blocks:
            return np.zeros(0, dtype=np.float32), t0
        lo = max(int(round((t0 - self.origin) * self.rate)), blocks[0][0])
        hi = min(int(round((t1 - self.origin) * self.rate)), n)
        if hi <= lo:
            return np.zeros(0, dtype=np.float32), t0
        parts = []
        for start, block in blocks:
            end = start + len(block)
            if end <= lo or start >= hi:
                continue
            parts.append(block[max(lo - start, 0) : hi - start])
        audio = np.concatenate(parts).astype(np.float32) / 32767.0
        return audio, self.origin + lo / self.rate


def mute_outside(
    audio: np.ndarray, t0: float, spans: Sequence[tuple[float, float]], pad: float,
    rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """``audio`` (starting at stream time ``t0``) with everything outside the ``spans`` (each
    widened by ``pad``) set to silence."""
    keep = np.zeros(len(audio), dtype=bool)
    for a, b in spans:
        i, j = int((a - pad - t0) * rate), int((b + pad - t0) * rate)
        keep[max(i, 0) : max(j, 0)] = True
    out = audio.copy()
    out[~keep] = 0.0
    return out


# --------------------------------------------------------------------------- planning


@dataclass
class LiveLine:
    """A live transcript line waiting for refinement."""

    seq: int
    source: str
    t_start: float
    t_end: float
    text: str

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    @classmethod
    def from_segment(cls, seg: Segment) -> LiveLine:
        return cls(seg.seq, seg.source, seg.t_start, seg.t_end, seg.text)


def speech_seconds(lines: Sequence[LiveLine]) -> float:
    return float(sum(max(ln.duration, 0.0) for ln in lines))


def pick_window(
    lines: Sequence[LiveLine],
    *,
    speech_s: float,
    now: float | None,
    max_wait_s: float,
    flush: bool = False,
) -> int | None:
    """How many lines from the head of ``lines`` form the next window; None when not due.

    Due when the lines hold ``speech_s`` seconds of speech, when the oldest is older than
    ``max_wait_s`` at stream time ``now``, or when flushing (stop). A window that reaches
    ``speech_s`` is cut at the longest pause among the cut points that leave at least 60 %
    of ``speech_s`` in it (a pause is the natural place to cut: no word is split and the
    model gets whole sentences); otherwise everything pending goes into one window.
    """
    if not lines:
        return None
    total = speech_seconds(lines)
    old = now is not None and now - lines[0].t_start >= max_wait_s
    if not (flush or old or total >= speech_s):
        return None
    acc = 0.0
    candidates: list[int] = []
    reached = False
    for i, ln in enumerate(lines):
        acc += ln.duration
        if acc >= 0.6 * speech_s:
            candidates.append(i)
        if acc >= speech_s:
            reached = True
            break
    if not reached:
        return len(lines)

    def gap_after(i: int) -> float:
        if i + 1 < len(lines):
            return lines[i + 1].t_start - lines[i].t_end
        return max(0.0, (now - lines[i].t_end) if now is not None else 0.0)

    best = max(candidates, key=lambda i: (round(gap_after(i), 2), i))
    return best + 1


def assign_words_to_lines(
    words: Sequence[Word], lines: Sequence[LiveLine], pad: float = ASSIGN_PAD_S
) -> list[list[Word]]:
    """Words per line (same order as ``lines``), by the word's midpoint.

    A word inside a line's span goes to it; one in a gap goes to the nearest line within
    ``pad`` seconds; the others (speech the live pass did not keep: dropped echo, noise)
    are discarded.
    """
    out: list[list[Word]] = [[] for _ in lines]
    for w in words:
        mid = (w.start + w.end) / 2
        best, best_d = -1, np.inf
        for i, ln in enumerate(lines):
            d = max(ln.t_start - mid, mid - ln.t_end, 0.0)
            if d < best_d:
                best, best_d = i, d
                if d == 0.0:
                    break
        if best >= 0 and best_d <= pad:
            out[best].append(w)
    return out


def accept_revision(draft: str, new: str, max_compression: float = 2.4) -> bool:
    """Is ``new`` a plausible replacement for ``draft`` (not empty, not looping, similar size)?"""
    if not new.strip(" .,!?-…"):
        return False
    if len(new) >= 24 and compression_ratio(new) > max_compression:
        return False
    n_new, n_old = len(new.split()), len(draft.split())
    if n_old >= 6 and n_new < MIN_WORD_RATIO * n_old:
        return False
    return n_new <= MAX_WORD_RATIO * max(n_old, 3) + 3


# --------------------------------------------------------------------------- the worker


@dataclass
class LagSample:
    """One finished window: how far behind live the refined text arrived."""

    stream_t: float  # stream time when the window finished
    lag_s: float  # ... minus the end of its last line: seconds from speech to refined text
    speech_s: float
    compute_s: float
    model: str


@dataclass
class RefineStats:
    windows: int = 0
    failed: int = 0
    revisions: int = 0
    speech_s: float = 0.0  # speech seconds refined
    compute_s: float = 0.0  # time spent decoding
    skipped_s: float = 0.0  # speech seconds skipped because the worker was too far behind
    fallback_windows: int = 0
    lines: int = 0  # lines that went through a window
    unchanged: int = 0  # ... whose refined text said the same as the draft
    rejected: int = 0  # ... whose refined text looked wrong (empty, looping, wrong size)
    no_words: int = 0  # ... that received no refined word at all (draft kept)
    lags: list[LagSample] = field(default_factory=list)


@dataclass
class _Job:
    source: str
    lines: list[LiveLine]


@dataclass
class RefineModels:
    """What ``Engine`` takes for rolling refinement (any part may be None)."""

    transcriber: object | None = None
    fallback: Callable[[], object] | None = None  # builds the fallback transcriber when needed
    diarizer: object | None = None


def create_refine_models(cfg: Config) -> RefineModels:
    """Models for ``[refine]``: the long-form transcriber (and lazy fallback) and the
    incremental diarizer. Nothing is loaded here; the engine loads them before capture."""
    from dataclasses import replace

    from steno.asr import create_transcriber

    r = cfg.refine
    out = RefineModels()

    def make(model: str):
        return create_transcriber(
            replace(cfg.asr, model=model, cpu_threads=r.threads, beam_size=r.beam_size))

    if r.enabled:
        out.transcriber = make(r.model)
        if r.fallback_model and r.fallback_model != r.model:
            out.fallback = lambda: make(r.fallback_model)
    if r.diarize and cfg.speakers.enabled:
        from steno.finalize import diarize_params
        from steno.livediar import LiveDiarizer
        from steno.segmentation import PyannoteSegmenter
        from steno.speakers import create_embedder

        embedder = create_embedder(replace(cfg.speakers, num_threads=r.diarize_threads))
        out.diarizer = LiveDiarizer(
            PyannoteSegmenter(num_threads=r.diarize_threads, batch_size=1), embedder,
            diarize_params(cfg),
        )
    return out


def _lower_thread_priority() -> None:
    """Best effort: run the calling thread below normal priority (no-op where unsupported)."""
    try:
        import ctypes
        import sys

        if sys.platform == "win32":
            kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)  # BELOW_NORMAL
        else:
            import os

            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 5)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - platform specific
        log.debug("could not lower the refine thread priority", exc_info=True)


class Refiner:
    """Background worker that refines live lines (see the module docstring)."""

    def __init__(
        self,
        cfg: RefineConfig,
        transcriber,
        emit: Emit,
        *,
        fallback_factory: Callable[[], object] | None = None,
        live_busy: Callable[[], bool] | None = None,
        max_compression: float = 2.4,
        diarizer=None,
    ):
        """``transcriber`` None: no text refinement, only the incremental ``diarizer``."""
        self.cfg = cfg
        self._tr = transcriber
        self._diar = diarizer
        self._fallback_factory = fallback_factory
        self._fallback = None
        self._using_fallback = False
        self._emit = emit
        self._live_busy = live_busy or (lambda: False)
        self._max_compression = max_compression
        self._rings: dict[str, AudioRing] = {}
        self._pending: dict[str, list[LiveLine]] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._flushing = False
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self._rate: float | None = None  # measured decode seconds per speech second (model)
        self._tail_fallback = False  # at stop: the rest goes to the fallback (stop_budget_s)
        self._current: tuple[_Job, float, bool] | None = None  # (job, started, primary model)
        self._yielded = 0.0  # seconds the current window waited for the live model
        self.stats = RefineStats()
        self.name = getattr(transcriber, "name", "refine") if transcriber else ""

    # -- life cycle ---------------------------------------------------------------

    def load(self) -> None:
        """Load the models; a diarizer that cannot load is dropped, the transcriber's error
        is raised (the engine then runs without refinement)."""
        if self._diar is not None:
            try:
                self._diar.load()
            except Exception:
                log.exception("Could not load the incremental diarizer; it is off")
                self._diar = None
        if self._tr is not None:
            try:
                self._tr.load()
            except Exception:
                if self._diar is None:
                    raise
                log.exception("Could not load the refine model; only diarization runs")
                self._tr = None
        if self._tr is not None and self._fallback_factory and self.cfg.stop_budget_s > 0:
            try:  # loaded up front: at stop there is no time to load it
                self._fallback = self._fallback_factory()
                self._fallback.load()  # type: ignore[attr-defined]
            except Exception:
                log.exception("Could not load the refine fallback model")
                self._fallback, self._fallback_factory = None, None

    @property
    def active(self) -> bool:
        return self._tr is not None or self._diar is not None

    def save_diarization(self, session_dir) -> bool:
        """Write ``diar.bin`` once the worker has finished (see ``steno.livediar``)."""
        if self._diar is None:
            return False
        try:
            return bool(self._diar.save(session_dir))
        except Exception:
            log.exception("Could not save the incremental diarization")
            return False

    @property
    def diarizer(self):
        return self._diar

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="refine", daemon=True)
        self._thread.start()

    def finish(self, timeout: float | None = None) -> bool:
        """Refine what is still pending, then stop the worker. False if ``timeout`` ran out."""
        with self._lock:
            self._flushing = True
            self._plan_tail()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("Refining the tail did not finish in %.0f s; keeping the drafts",
                            timeout or 0)
                self.abort()
                self._thread.join(5)
                return False
        return True

    def abort(self) -> None:
        self._abort.set()
        self._wake.set()

    # -- input from the engine -------------------------------------------------------

    def feed(self, stream: str, samples: np.ndarray, origin: float) -> None:
        ring = self._rings.get(stream)
        if ring is None:
            with self._lock:
                ring = self._rings.setdefault(stream, AudioRing(self.cfg.buffer_s))
        ring.append(samples, origin)

    def note_line(self, seg: Segment) -> None:
        if self._diar is not None:
            self._diar.note_line(seg.source, seg.t_start, seg.t_end)
        line = LiveLine.from_segment(seg)
        if line.duration < MIN_LINE_S or self._tr is None:
            self._wake.set()
            return
        with self._lock:
            self._pending.setdefault(line.source, []).append(line)
        self._wake.set()

    @property
    def backlog_s(self) -> float:
        """Seconds of live speech waiting for refinement (all streams)."""
        with self._lock:
            return sum(speech_seconds(v) for v in self._pending.values())

    @property
    def model_name(self) -> str:
        tr = self._fallback if self._using_fallback else self._tr
        return str(getattr(tr, "name", self.name))

    def _diarize_step(self, limit: int = 4) -> int:
        """A few windows of incremental diarization; failures turn the diarizer off."""
        if self._diar is None:
            return 0
        try:
            return int(self._diar.step(self._rings, limit))
        except Exception:
            log.exception("Incremental diarization failed; it is off for this session")
            self._diar = None
            return 0

    # -- scheduling -----------------------------------------------------------------

    def estimate_tail_s(self) -> float:
        """Expected seconds for ``[refine] model`` to refine what is left (caller holds lock)."""
        rate = self._rate
        if rate is None:
            rate = default_rate(getattr(self._tr, "name", None))
        left = sum(speech_seconds(v) for v in self._pending.values()) * rate
        if self._current is not None and self._current[2]:
            job, started, _ = self._current
            left += max(0.0, speech_seconds(job.lines) * rate - (time.perf_counter() - started))
        return left

    def _plan_tail(self) -> None:
        """At stop: hand the rest to the fallback if the main model would exceed the budget."""
        budget = self.cfg.stop_budget_s
        if budget <= 0 or self._fallback is None or self._using_fallback:
            return
        estimate = self.estimate_tail_s()
        if estimate > budget:
            self._tail_fallback = True
            log.info("Stop: ~%.0f s of refining left with %s (budget %.0f s); using %s",
                     estimate, getattr(self._tr, "name", "?"), budget,
                     getattr(self._fallback, "name", "?"))

    def _degrade(self) -> None:
        """Fall back to the cheaper model, or skip the oldest speech (caller holds the lock)."""
        cfg = self.cfg
        windows = sum(speech_seconds(v) for v in self._pending.values()) / cfg.window_speech_s
        behind = windows > cfg.max_backlog_windows
        if behind and self._fallback_factory and not self._using_fallback:
            try:
                if self._fallback is None:
                    self._fallback = self._fallback_factory()
                    self._fallback.load()  # type: ignore[attr-defined]
                self._using_fallback = True
                log.warning("Refine is %.1f windows behind: switching to the fallback model %s",
                            windows, getattr(self._fallback, "name", "?"))
            except Exception:
                log.exception("Could not load the refine fallback model")
                self._fallback_factory = None
        if self._tail_fallback and not self._using_fallback:
            self._using_fallback = True
        elif self._using_fallback and windows < 1.0 and not self._tail_fallback:
            self._using_fallback = False
            log.info("Refine has caught up: back to %s", getattr(self._tr, "name", "?"))
        if windows > cfg.skip_after_windows:
            keep_s = cfg.max_backlog_windows * cfg.window_speech_s
            dropped = 0.0
            while sum(speech_seconds(v) for v in self._pending.values()) > keep_s:
                oldest = min((v for v in self._pending.values() if v),
                             key=lambda v: v[0].t_start, default=None)
                if oldest is None:
                    break
                dropped += oldest.pop(0).duration
            self.stats.skipped_s += dropped
            log.warning("Refine is %.1f windows behind: skipped %.0f s of speech (drafts kept)",
                        windows, dropped)

    def _next_job(self) -> _Job | None:
        with self._lock:
            self._degrade()
            order = sorted((v for v in self._pending.values() if v), key=lambda v: v[0].t_start)
            for lines in order:
                ring = self._rings.get(lines[0].source)
                now = ring.end_s if ring is not None else None
                n = pick_window(
                    lines, speech_s=self.cfg.window_speech_s, now=now,
                    max_wait_s=self.cfg.max_wait_s, flush=self._flushing,
                )
                if n:
                    job = _Job(lines[0].source, lines[:n])
                    del lines[:n]
                    return job
        return None

    def _idle(self) -> bool:
        with self._lock:
            return not any(self._pending.values())

    def _loop(self) -> None:
        _lower_thread_priority()
        while not self._abort.is_set():
            job = self._next_job() if self._tr is not None else None
            if job is not None:
                try:
                    self._run(job)
                except _Requeue:
                    with self._lock:  # back to the front; the fallback takes it next
                        self._pending.setdefault(job.source, [])[:0] = job.lines
                    continue
                except _Aborted:
                    break
                except Exception:
                    self.stats.failed += 1
                    log.exception("Refining a window failed; the drafts stay")
                finally:
                    self._current = None
            # speakers get their turn between text windows (a window is short, its model fast)
            while not self._live_busy() and self._diarize_step() and job is None:
                pass
            if job is None:
                if self._flushing and self._idle():
                    self._finish_diarization()
                    break
                self._wake.wait(0.5)
                self._wake.clear()

    def _finish_diarization(self) -> None:
        if self._diar is None or self._abort.is_set():
            return
        try:
            self._diar.finish(self._rings)
        except Exception:
            log.exception("Incremental diarization failed at the end; it is off")
            self._diar = None

    # -- one window -----------------------------------------------------------------

    def _tick(self, done: float, total: float) -> None:
        """Called by the model between segments: stop on abort, let live utterances go first."""
        waited = 0.0
        while self._live_busy() and waited < MAX_YIELD_S and not self._abort.is_set():
            time.sleep(0.05)
            waited += 0.05
        self._yielded += waited
        if self._abort.is_set():
            raise _Aborted
        current = self._current
        if self._tail_fallback and current is not None and current[2]:
            raise _Requeue

    def _run(self, job: _Job) -> None:
        ring = self._rings.get(job.source)
        if ring is None:
            return
        lines = [ln for ln in job.lines if ln.t_start >= ring.start_s - MASK_PAD_S]
        if not lines:
            log.debug("Refine window left the audio buffer; skipped")
            return
        t0 = max(lines[0].t_start - self.cfg.pad_s, ring.start_s)
        audio, a0 = ring.get(t0, lines[-1].t_end + self.cfg.pad_s)
        if len(audio) < MIN_AUDIO_S * SAMPLE_RATE:
            return
        audio = mute_outside(audio, a0, [(ln.t_start, ln.t_end) for ln in lines], MASK_PAD_S)
        primary = not self._using_fallback
        tr = self._tr if primary else self._fallback
        started = time.perf_counter()
        self._current = (job, started, primary)
        self._yielded = 0.0
        segments, lang = tr.transcribe_longform(  # type: ignore[attr-defined]
            audio, batch_size=self.cfg.batch_size, beam_size=self.cfg.beam_size,
            progress=self._tick, detect_english=self.cfg.detect_english,
        )
        compute = time.perf_counter() - started
        speech = speech_seconds(lines)
        if primary and speech > 0:  # the speed at stop, when the live model no longer competes
            rate = max(compute - self._yielded, 0.0) / speech
            self._rate = rate if self._rate is None else 0.7 * self._rate + 0.3 * rate
        words: list[Word] = []
        logprob: dict[int, float] = {}
        for s in segments:
            text = " ".join(w.text for w in s.words)
            if len(text) >= 24 and compression_ratio(text) > self._max_compression:
                log.warning("Dropped a looping refined segment at %.1fs", a0 + s.start)
                continue
            for w in s.words:
                words.append(Word(w.start + a0, w.end + a0, w.text))
                if s.avg_logprob is not None:
                    logprob[id(words[-1])] = s.avg_logprob
        words.sort(key=lambda w: w.start)
        per_line = assign_words_to_lines(words, lines)
        items: list[tuple[int, str, dict]] = []
        st = self.stats
        st.lines += len(lines)
        for ln, ws in zip(lines, per_line, strict=True):
            if not ws:
                st.no_words += 1
                continue
            text = " ".join(w.text for w in ws)
            if same_words(text, ln.text):
                st.unchanged += 1
                continue
            if not accept_revision(ln.text, text, self._max_compression):
                st.rejected += 1
                log.debug("Refined text of line %d rejected", ln.seq)
                continue
            lps = [logprob[id(w)] for w in ws if id(w) in logprob]
            items.append((ln.seq, text, {
                "lang": lang or None,
                "words": [[w.start, w.end, w.text] for w in ws],
                "avg_logprob": float(np.mean(lps)) if lps else None,
            }))
        if items:
            self._emit(items, str(getattr(tr, "name", self.name)))
        st.windows += 1
        st.revisions += len(items)
        st.speech_s += speech_seconds(lines)
        st.compute_s += compute
        st.fallback_windows += int(self._using_fallback)
        st.lags.append(LagSample(
            ring.end_s, ring.end_s - lines[-1].t_end, speech_seconds(lines), compute,
            str(getattr(tr, "name", self.name)),
        ))
