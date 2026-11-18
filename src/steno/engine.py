"""The live pipeline: audio sources -> VAD -> one ASR worker -> transcript.

    sources --(AudioChunk)--> [pipeline thread: VAD per stream + echo gate]
            --(Utterance, ordered by time)--> [ASR thread: one shared model]
            --> SessionWriter (JSONL + Markdown) --> subscribers

``Engine`` has no I/O of its own beyond the session files, so a CLI, a desktop
UI or a WebSocket server can all drive it: ``subscribe(callback)`` to receive
each final ``Segment``, ``start()`` / ``stop()`` to control it.

Speaker recognition (layer 2, ``[speakers]``): when an ``embedder`` is given, the
ASR thread identifies the speakers of every transcribed segment right after ASR
(one worker keeps segments in time order, which the live clustering relies on) and
writes ``speaker_id`` / ``speaker_conf`` / ``provisional`` next to the display
label; see ``steno.speakers``. A segment in which the speaker changes (a short
interjection inside a long turn) is written as one line per speaker turn: the ASR
backend's word timestamps decide which words belong to which turn (``steno.windows``).
Lines keep strictly increasing ``seq`` and cover disjoint parts of the segment.
Without an embedder (or with ``[speakers] enabled = false``) the fixed per-stream
labels from ``[audio]`` are used. Subscribers receive the full ``Segment`` including
those fields. Embeddings are stored encrypted in the
session directory; profiles are never modified by the engine.

Echo heuristic (``[echo]`` config): when the speakers play the remote side, the
microphone picks it up again, quieter. A mic utterance is dropped when it overlaps
(>= 30 % of its duration) a loopback utterance and its RMS is below
``ratio`` x that loopback utterance's RMS. Mic utterances are therefore held back
a little (``min_silence`` + 0.3 s of loopback stream time) so the overlapping
loopback utterance is known when the decision is made. Genuine cross-talk louder
than the threshold is kept; quiet interjections over a loud remote speaker may
be lost (tune ``ratio``).
"""

from __future__ import annotations

import itertools
import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from steno import paths
from steno.asr import Transcriber
from steno.audio import SOURCE_LOOPBACK, SOURCE_MIC, AudioChunk, AudioSource, StreamRecorder
from steno.config import Config
from steno.labels import SessionLabels
from steno.profiles import ProfileStore, SessionEmbeddings, SessionWindows
from steno.speakers import Embedder, SpeakerTracker, TurnLabel
from steno.transcript import Segment, SessionWriter
from steno.vad import (
    AutoGain,
    SileroVad,
    SpeechProbModel,
    SpeechSegmenter,
    Utterance,
    VadParams,
    rms,
)
from steno.windows import Turn, split_by_turns

log = logging.getLogger(__name__)

SegmentCallback = Callable[[Segment], None]


# --------------------------------------------------------------------------- echo gate


class EchoGate:
    """Decides whether a microphone utterance is just loopback audio leaking in."""

    def __init__(self, ratio: float = 0.35, enabled: bool = True, history_s: float = 60.0):
        self.ratio = ratio
        self.enabled = enabled
        self.history_s = history_s
        self._loop: list[tuple[float, float, float]] = []  # (t0, t1, rms)

    def note_loopback(self, t0: float, t1: float, level: float) -> None:
        self._loop.append((t0, t1, level))
        cutoff = t1 - self.history_s
        self._loop = [e for e in self._loop if e[1] >= cutoff]

    def is_echo(
        self,
        t0: float,
        t1: float,
        level: float,
        open_loopback: tuple[float, float] | None = None,
    ) -> bool:
        """``open_loopback`` = (start, rms) of a loopback utterance still in progress."""
        if not self.enabled:
            return False
        dur = max(t1 - t0, 1e-6)
        candidates = list(self._loop)
        if open_loopback is not None:
            candidates.append((open_loopback[0], math.inf, open_loopback[1]))
        loud = 0.0
        for e0, e1, e_level in candidates:
            overlap = min(t1, e1) - max(t0, e0)
            if overlap >= 0.3 * dur:
                loud = max(loud, e_level)
        return loud > 0.0 and level < self.ratio * loud


# --------------------------------------------------------------------------- engine


@dataclass
class _Stream:
    name: str
    segmenter: SpeechSegmenter
    origin: float | None = None  # session-clock time of the first sample
    agc: AutoGain | None = None  # input gain for the VAD only
    eof: bool = False

    @property
    def watermark(self) -> float:
        if self.eof:
            return math.inf
        return 0.0 if self.origin is None else self.origin + self.segmenter.position_s


@dataclass
class _Pending:
    stream: str
    utt: Utterance
    t0: float
    t1: float
    level: float


@dataclass(order=True)
class _Job:
    t_start: float
    order: int
    stream: str = field(compare=False, default="")
    t_end: float = field(compare=False, default=0.0)
    audio: object = field(compare=False, default=None)


class Engine:
    def __init__(
        self,
        config: Config,
        transcriber: Transcriber,
        sources: list[AudioSource],
        *,
        sessions_root: Path | None = None,
        speaker_labels: dict[str, str] | None = None,
        vad_factory: Callable[[], SpeechProbModel] = SileroVad,
        embedder: Embedder | None = None,
        profile_store: ProfileStore | None = None,
        keep_audio: bool = False,
    ):
        self.config = config
        self.transcriber = transcriber
        self.sources = sources
        self._sessions_root = sessions_root
        self._labels = {
            SOURCE_MIC: config.audio.mic_speaker_label,
            SOURCE_LOOPBACK: config.audio.loopback_speaker_label,
            **(speaker_labels or {}),
        }
        self._forced = set(speaker_labels or {})  # explicit labels bypass speaker recognition
        self._vad_factory = vad_factory
        self._embedder = embedder if config.speakers.enabled else None
        self._profile_store = profile_store
        self._tracker: SpeakerTracker | None = None
        self._keep_audio = keep_audio
        self._recorders: dict[str, StreamRecorder] = {}
        self._subscribers: list[SegmentCallback] = []
        self._sub_lock = threading.Lock()

        self._audio_q: queue.Queue[AudioChunk] = queue.Queue()
        self._asr_q: queue.PriorityQueue[_Job] = queue.PriorityQueue()
        self._counter = itertools.count()
        self._streams: dict[str, _Stream] = {}
        self._pending: list[_Pending] = []
        self._gate = EchoGate(config.echo.ratio, config.echo.enabled)
        self._hold_s = config.vad.min_silence_ms / 1000 + 0.3

        self._writer: SessionWriter | None = None
        self._pipeline: threading.Thread | None = None
        self._asr: threading.Thread | None = None
        self._started: list[AudioSource] = []
        self._done = threading.Event()
        self._abort = threading.Event()
        self._force_end = threading.Event()
        self._stopped = False

    # -- public API -------------------------------------------------------------

    @property
    def session_dir(self) -> Path:
        if self._writer is None:
            raise RuntimeError("engine not started")
        return self._writer.dir

    @property
    def session_id(self) -> str:
        return self.session_dir.name

    @property
    def backlog(self) -> int:
        """Utterances waiting for transcription (a growing value means ASR is too slow)."""
        return self._asr_q.qsize()

    def subscribe(self, callback: SegmentCallback) -> Callable[[], None]:
        """Call ``callback(segment)`` from the ASR thread for every final segment."""
        with self._sub_lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._sub_lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def start(self) -> Path:
        """Load the model, create the session and start capturing. Returns the session dir."""
        if self._writer is not None:
            raise RuntimeError("engine already started")
        self.transcriber.load()
        if self._embedder is not None:
            self._embedder.load()  # may download the model; fail before capture starts
        params = VadParams(
            threshold=self.config.vad.threshold,
            min_silence_ms=self.config.vad.min_silence_ms,
            min_speech_ms=self.config.vad.min_speech_ms,
            max_segment_s=self.config.vad.max_segment_s,
        )
        for src in self.sources:
            agc = None
            if self.config.vad.auto_gain:
                agc = AutoGain(self.config.vad.auto_gain_target, self.config.vad.auto_gain_max)
            self._streams[src.name] = _Stream(
                src.name, SpeechSegmenter(self._vad_factory(), params), agc=agc
            )
        self._writer = SessionWriter.create(
            root=self._sessions_root,
            model=self.transcriber.name,
            sources=[s.name for s in self.sources],
            extra={
                "language": self.config.asr.language,
                "speakers": self._embedder.name if self._embedder is not None else None,
            },
        )
        if self._embedder is not None:
            labels = SessionLabels(self._writer.dir)
            labels.init()
            self._tracker = SpeakerTracker(
                self._embedder,
                self.config.speakers,
                self._profile_store or ProfileStore(paths.profiles_dir()),
                labels,
                SessionEmbeddings(self._writer.dir),
                SessionWindows(self._writer.dir),
            )
        if self._keep_audio:
            for name in self._streams:
                self._recorders[name] = StreamRecorder(self._writer.dir, name)
        self._pipeline = threading.Thread(target=self._pipeline_loop, name="vad", daemon=True)
        self._asr = threading.Thread(target=self._asr_loop, name="asr", daemon=True)
        self._pipeline.start()
        self._asr.start()
        t0 = time.monotonic()
        try:
            for src in self.sources:
                src.start(self._audio_q, t0)
                self._started.append(src)
        except Exception:
            for src in self.sources:  # sources that never started will never send EOF
                if src not in self._started:
                    self._streams[src.name].eof = True
            self.stop(drain=False)
            raise
        return self._writer.dir

    def wait(self, timeout: float | None = None) -> bool:
        """Block until every source has ended and all utterances are transcribed.

        Only meaningful for finite sources (files); live capture ends via ``stop()``.
        """
        return self._done.wait(timeout)

    def stop(self, drain: bool = True) -> None:
        """Stop capture, finish pending transcription (unless ``drain=False``), close session."""
        if self._writer is None:
            return
        if not drain:
            self._abort.set()  # also honoured when a draining stop() is already waiting
        if self._stopped:
            if self._asr is not None:
                self._asr.join()
            self._writer.close()
            return
        self._stopped = True
        for src in self._started:
            try:
                src.stop()
            except Exception:
                log.exception("Error stopping source %s", src.name)
        if self._pipeline is not None:
            self._pipeline.join(timeout=5)
            if self._pipeline.is_alive():
                self._force_end.set()
                self._pipeline.join(timeout=5)
        if self._asr is not None:
            self._asr.join()
        self._writer.close()

    # -- pipeline thread --------------------------------------------------------

    def _pipeline_loop(self) -> None:
        try:
            while not all(s.eof for s in self._streams.values()):
                if self._force_end.is_set():
                    break
                try:
                    chunk = self._audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                stream = self._streams.get(chunk.source)
                if stream is None:
                    continue
                if stream.origin is None:
                    stream.origin = chunk.t_start
                recorder = self._recorders.get(chunk.source)
                if recorder is not None and chunk.samples.size:
                    recorder.write(chunk.samples)
                if chunk.samples.size:
                    gain = stream.agc.update(chunk.samples) if stream.agc else 1.0
                    for utt in stream.segmenter.feed(chunk.samples, gain):
                        self._on_utterance(stream, utt)
                if chunk.eof:
                    for utt in stream.segmenter.flush():
                        self._on_utterance(stream, utt)
                    stream.eof = True
                self._release_pending()
            for stream in self._streams.values():
                if not stream.eof:
                    for utt in stream.segmenter.flush():
                        self._on_utterance(stream, utt)
                    stream.eof = True
            self._release_pending()
        except Exception:
            log.exception("Pipeline thread crashed")
        finally:
            for recorder in self._recorders.values():
                recorder.close()
            self._asr_q.put(_Job(math.inf, next(self._counter)))  # sentinel

    def _on_utterance(self, stream: _Stream, utt: Utterance) -> None:
        origin = stream.origin or 0.0
        t0, t1 = origin + utt.t_start, origin + utt.t_end
        level = rms(utt.audio)
        if stream.name == SOURCE_LOOPBACK:
            self._gate.note_loopback(t0, t1, level)
            self._submit(stream.name, utt, t0, t1)
        elif stream.name == SOURCE_MIC and self._gate.enabled and self._loopback_live():
            self._pending.append(_Pending(stream.name, utt, t0, t1, level))
        else:
            self._submit(stream.name, utt, t0, t1)

    def _loopback_live(self) -> bool:
        loop = self._streams.get(SOURCE_LOOPBACK)
        return loop is not None and not loop.eof

    def _release_pending(self) -> None:
        if not self._pending:
            return
        loop = self._streams.get(SOURCE_LOOPBACK)
        still: list[_Pending] = []
        for p in self._pending:
            ready = loop is None or loop.eof or loop.watermark >= p.t1 + self._hold_s
            if not ready:
                still.append(p)
                continue
            open_loop = None
            if loop is not None and not loop.eof:
                start = loop.segmenter.open_start_s
                if start is not None:
                    open_loop = ((loop.origin or 0.0) + start, loop.segmenter.open_rms())
            if self._gate.is_echo(p.t0, p.t1, p.level, open_loop):
                log.debug("Dropped mic utterance at %.1fs as echo", p.t0)
                continue
            self._submit(p.stream, p.utt, p.t0, p.t1)
        self._pending = still

    def _submit(self, stream: str, utt: Utterance, t0: float, t1: float) -> None:
        self._asr_q.put(_Job(t0, next(self._counter), stream, t1, utt.audio))

    # -- ASR thread -------------------------------------------------------------

    def _asr_loop(self) -> None:
        assert self._writer is not None
        try:
            while True:
                job = self._asr_q.get()
                if math.isinf(job.t_start):
                    break
                if self._abort.is_set():
                    continue
                try:
                    text, lang, words = self._transcribe(job)
                except Exception:
                    log.exception("Transcription failed for utterance at %.1fs", job.t_start)
                    continue
                if not text.strip(" .,!?-…"):
                    continue
                turns = self._identify(job)
                if turns is None:
                    self._write(job, 0.0, job.t_end - job.t_start, None, text.strip(), lang)
                    continue
                pieces = split_by_turns(
                    text.strip(), words, [Turn(x.t_start, x.t_end, "") for x in turns]
                )
                for index, piece in pieces:
                    turn = turns[index]
                    if len(turns) > 1 and words:  # the cut is real: a turn spans only its part
                        t0, t1 = turn.t_start, turn.t_end
                    else:
                        t0, t1 = 0.0, job.t_end - job.t_start
                    self._write(job, t0, t1, turn.label, piece, lang)
        finally:
            if self._tracker is not None:
                try:
                    self._tracker.close()
                except Exception:
                    log.exception("Could not save session embeddings")
            self._done.set()

    def _transcribe(self, job: _Job) -> tuple[str, str, list | None]:
        """``(text, language, words)``; ``words`` is None for backends without timestamps."""
        fn = getattr(self.transcriber, "transcribe_words", None)
        if fn is not None and self._tracker is not None and job.stream not in self._forced:
            return fn(job.audio)
        text, lang = self.transcriber.transcribe(job.audio)  # type: ignore[arg-type]
        return text, lang, None

    def _identify(self, job: _Job) -> list[TurnLabel] | None:
        if self._tracker is None or job.stream in self._forced:
            return None
        try:
            return self._tracker.identify_turns(job.stream, job.audio, job.t_start)  # type: ignore[arg-type]
        except Exception:
            log.exception("Speaker identification failed for utterance at %.1fs", job.t_start)
            return None

    def _write(self, job: _Job, t0: float, t1: float, label, text: str, lang: str) -> None:
        assert self._writer is not None
        seg = self._writer.append(
            t_start=job.t_start + t0,
            t_end=job.t_start + t1,
            source=job.stream,
            speaker=label.name if label else self._labels.get(job.stream, job.stream),
            text=text,
            lang=lang,
            speaker_id=label.speaker_id if label else None,
            speaker_conf=label.conf if label else None,
            provisional=label.provisional if label else None,
        )
        if label is not None and self._tracker is not None:
            self._tracker.record(seg.seq, label)
        self._notify(seg)

    def _notify(self, seg: Segment) -> None:
        with self._sub_lock:
            subscribers = list(self._subscribers)
        for cb in subscribers:
            try:
                cb(seg)
            except Exception:
                log.exception("Segment subscriber raised")
