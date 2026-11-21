"""In-process recording for the UI: drives one ``Engine`` and publishes what it does."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import numpy as np

from steno import labels, projects
from steno.config import Config
from steno.engine import Engine
from steno.transcript import Segment
from steno.ui.events import EventBus

log = logging.getLogger(__name__)

#: ``factory(config, keep_audio, mic, loopback) -> Engine`` (not yet started).
EngineFactory = Callable[[Config, bool, bool, bool], Engine]

LEVEL_FLOOR_DB = -60.0
ACTIVE_LEVEL = 0.35  # normalised level above which a stream counts as "active"
ACTIVE_HOLD_S = 0.6
MIRROR_EVERY_S = 2.0  # transcript.md in the meeting folder is rewritten at most this often


class RecorderError(RuntimeError):
    """A start/stop request that cannot be honoured; the message is shown to the user."""


def default_engine_factory(cfg: Config, keep_audio: bool, mic: bool, loopback: bool) -> Engine:
    """Same wiring as ``steno record`` (``steno.cli._run_engine``)."""
    from steno.asr import create_transcriber
    from steno.audio import create_capture_sources
    from steno.refine import create_refine_models
    from steno.speakers import create_embedder

    transcriber = create_transcriber(cfg.asr)
    embedder = create_embedder(cfg.speakers) if cfg.speakers.enabled else None
    sources = create_capture_sources(mic=mic, loopback=loopback)
    refine = create_refine_models(cfg)
    return Engine(cfg, transcriber, sources, embedder=embedder, keep_audio=keep_audio,
                  refine_transcriber=refine.transcriber, refine_fallback=refine.fallback,
                  refine_diarizer=refine.diarizer)


# --------------------------------------------------------------------------- level meter


class _TapQueue:
    """Looks like the engine's audio queue; reports every chunk before forwarding it.

    While ``paused()`` the samples are replaced by silence: the stream clock keeps running
    (kept audio stays aligned) and the engine simply hears nobody.
    """

    def __init__(self, inner, on_chunk: Callable[[str, np.ndarray], None],
                 paused: Callable[[], bool] = lambda: False):
        self._inner = inner
        self._on_chunk = on_chunk
        self._paused = paused

    def put(self, chunk, *args, **kwargs) -> None:
        if self._paused() and chunk.samples.size:
            chunk = dc_replace(chunk, samples=np.zeros_like(chunk.samples))
        try:
            if chunk.samples.size:
                self._on_chunk(chunk.source, chunk.samples)
        except Exception:  # metering must never break capture
            log.debug("level tap failed", exc_info=True)
        self._inner.put(chunk, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _TappedSource:
    def __init__(self, source, on_chunk: Callable[[str, np.ndarray], None],
                 paused: Callable[[], bool] = lambda: False):
        self._source = source
        self._on_chunk = on_chunk
        self._paused = paused
        self.name = source.name

    def start(self, sink, t0: float) -> None:
        self._source.start(_TapQueue(sink, self._on_chunk, self._paused), t0)

    def stop(self) -> None:
        self._source.stop()

    def __getattr__(self, name: str):
        return getattr(self._source, name)


def normalise_level(rms: float) -> float:
    """RMS (0..1 full scale) -> 0..1 on a -60..0 dB scale."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return min(1.0, max(0.0, (db - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB))


@dataclass
class _Level:
    value: float = 0.0
    active_until: float = 0.0


# --------------------------------------------------------------------------- controller


class Recorder:
    """State machine: idle -> starting -> recording -> stopping -> idle (or error)."""

    def __init__(self, cfg: Config, bus: EventBus, factory: EngineFactory | None = None):
        self._cfg = cfg
        self._bus = bus
        self._factory = factory or default_engine_factory
        self._lock = threading.Lock()
        self.state = "idle"
        self.error = ""
        self.session_id: str | None = None  # also kept after stop, until the next start
        self.keep_audio = False
        self.sources: list[str] = []
        self.speaker = ""
        self.last_segment_at: float | None = None
        self.segments = 0
        self.paused = False
        self.project: projects.Project | None = None
        self.mtype: str | None = None  # meeting type within the project
        # Name the meeting from its transcript after Stop (only while the user typed no title).
        self.auto_title = False
        self._title: str | None = None
        self._mirror_dirty = False
        self._engine: Engine | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._unsubscribe_rev: Callable[[], None] | None = None
        self._t0: float | None = None
        self._levels: dict[str, _Level] = {}
        self._stop_thread: threading.Thread | None = None
        # (session id, keep_audio, auto_title) once the recording has been finished
        self.on_stopped: Callable[[str, bool, bool], None] | None = None

    # -- queries ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state in ("starting", "recording", "stopping")

    def active_session(self) -> str | None:
        return self.session_id if self.state in ("recording", "stopping") else None

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            running = self._t0 is not None and self.state in ("recording", "stopping")
            elapsed = (now - self._t0) if running else 0.0
            levels = {
                name: {"level": round(lv.value, 3), "active": now < lv.active_until}
                for name, lv in self._levels.items()
            }
            engine = self._engine
            return {
                "state": self.state,
                "paused": self.paused,
                "project": self.project.id if self.project is not None else None,
                "type": self.mtype,
                "auto_title": self.auto_title,
                "error": self.error,
                "session": self.session_id,
                "keep_audio": self.keep_audio,
                "sources": list(self.sources),
                "elapsed_s": round(elapsed, 1),
                "speaker": self.speaker,
                "segments": self.segments,
                "backlog": engine.backlog if engine is not None else 0,
                "levels": levels,
                "idle_s": (round(now - self.last_segment_at, 1)
                           if self.last_segment_at is not None else None),
            }

    # -- control ------------------------------------------------------------------

    def start(self, *, keep_audio: bool, mic: bool, loopback: bool,
              project: projects.Project | None = None, title: str | None = None,
              mtype: str | None = None, auto_title: bool = False) -> None:
        if not (mic or loopback):
            raise RecorderError("nothing to record: enable the microphone or system audio")
        with self._lock:
            if self.active:
                raise RecorderError("already recording")
            self.state = "starting"
            # The previous session id must not linger: the page opens "the session being
            # recorded" as soon as it shows up, and would open the old meeting instead.
            self.session_id = None
            self.error = ""
            self.keep_audio = keep_audio
            self.sources = [n for n, on in (("mic", mic), ("loopback", loopback)) if on]
            self.speaker = ""
            self.segments = 0
            self.paused = False
            self.project = project
            self.mtype = mtype
            self.auto_title = auto_title and not title  # a typed title is never replaced
            self._title = title
            self.last_segment_at = None
            self._t0 = None
            self._levels = {n: _Level() for n in self.sources}
        self._publish_status()
        threading.Thread(
            target=self._start_worker, args=(keep_audio, mic, loopback), name="ui-rec-start",
            daemon=True,
        ).start()

    def _start_worker(self, keep_audio: bool, mic: bool, loopback: bool) -> None:
        try:
            engine = self._factory(self._cfg, keep_audio, mic, loopback)
            engine.sources = [_TappedSource(s, self._on_chunk, lambda: self.paused)
                              for s in engine.sources]
            self._engine = engine  # segment callbacks need it from the first line on
            self._unsubscribe = engine.subscribe(self._on_segment)
            if hasattr(engine, "subscribe_revisions"):
                self._unsubscribe_rev = engine.subscribe_revisions(self._on_revision)
            session_dir = engine.start()  # loads the models: can take a while
        except Exception as exc:
            log.exception("Could not start recording")
            self._drop_subscriptions()
            with self._lock:
                self.state = "error"
                self.error = str(exc).strip() or type(exc).__name__
                self._engine = None
                self._t0 = None
            self._publish_status()
            return
        if self.project is not None:
            try:
                projects.create_meeting(self.project, session_dir, self._title, self.mtype)
            except Exception:  # recording goes on; the session stays in the inbox
                log.exception("Could not create the meeting folder")
        with self._lock:
            self.session_id = session_dir.name
            self._t0 = time.monotonic()
            self.state = "recording"
        self._publish_status()
        self._bus.publish("sessions", {"session": session_dir.name})
        self._bus.publish("projects", {"session": session_dir.name})
        threading.Thread(target=self._mirror_loop, args=(session_dir,), name="ui-rec-mirror",
                         daemon=True).start()

    def pause(self) -> None:
        with self._lock:
            if self.state != "recording":
                raise RecorderError("not recording")
            if self.paused:
                raise RecorderError("already paused")
            self.paused = True
        self._publish_status()

    def set_auto_title(self, on: bool) -> None:
        """Switch the automatic title of the meeting being recorded (before Stop only)."""
        with self._lock:
            if self.state not in ("starting", "recording"):
                raise RecorderError("not recording")
            self.auto_title = on
        self._publish_status()

    def resume(self) -> None:
        with self._lock:
            if self.state != "recording" or not self.paused:
                raise RecorderError("not paused")
            self.paused = False
        self._publish_status()

    def _mirror_loop(self, session_dir: Path) -> None:
        """Keep ``transcript.md`` in the meeting folder current while recording."""
        while self.state in ("recording", "stopping") and self.session_id == session_dir.name:
            time.sleep(MIRROR_EVERY_S)
            if self._mirror_dirty:
                self._mirror_dirty = False
                try:
                    projects.sync(session_dir, recording=True, docs=False)
                except Exception:
                    log.debug("meeting folder sync failed", exc_info=True)

    def stop(self, *, drain: bool = True) -> None:
        with self._lock:
            if self.state != "recording":
                raise RecorderError("not recording")
            self.state = "stopping"
            engine = self._engine
        self._publish_status()
        self._stop_thread = threading.Thread(
            target=self._stop_worker, args=(engine, drain), name="ui-rec-stop", daemon=True
        )
        self._stop_thread.start()

    def _stop_worker(self, engine: Engine | None, drain: bool) -> None:
        error = ""
        try:
            if engine is not None:
                engine.stop(drain=drain)
        except Exception as exc:
            log.exception("Error while stopping the recording")
            error = str(exc).strip() or type(exc).__name__
        self._drop_subscriptions()
        session, keep, auto_title = self.session_id, self.keep_audio, self.auto_title
        if engine is not None and self.project is not None:
            try:  # the finished text, and the index without the "recording" mark
                projects.sync(engine.session_dir)
                projects.write_index(self.project)
            except Exception:
                log.exception("Could not update the meeting folder")
        with self._lock:
            self.paused = False
            self.state = "error" if error else "idle"
            self.error = error
            self._engine = None
            self._t0 = None
            for lv in self._levels.values():
                lv.value, lv.active_until = 0.0, 0.0
        self._publish_status()
        self._bus.publish("sessions", {"session": session})
        if session and self.on_stopped is not None:
            try:
                self.on_stopped(session, keep, auto_title)
            except Exception:
                log.exception("on_stopped hook failed")

    def shutdown(self, *, drain: bool = True) -> None:
        """Stop synchronously (server exit). Safe when idle."""
        for _ in range(600):  # let a model load finish, then stop cleanly
            if self.state != "starting":
                break
            time.sleep(0.1)
        with self._lock:
            engine = self._engine if self.state in ("recording", "stopping") else None
        if engine is not None:
            engine.stop(drain=drain)
        if self._stop_thread is not None and self._stop_thread.is_alive():
            self._stop_thread.join()

    # -- engine callbacks ---------------------------------------------------------

    def _on_chunk(self, source: str, samples: np.ndarray) -> None:
        lv = self._levels.get(source)
        if lv is None:
            return
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        level = normalise_level(rms)
        lv.value = max(level, lv.value * 0.85)  # fast attack, gentle decay
        if level >= ACTIVE_LEVEL:
            lv.active_until = time.monotonic() + ACTIVE_HOLD_S

    def _on_segment(self, seg: Segment) -> None:
        engine = self._engine
        shown = seg
        if engine is not None:
            try:  # current names (e.g. a speaker named while recording), not write-time labels
                shown = labels.resolve_segments(engine.session_dir, [seg])[0]
            except Exception:
                log.debug("could not resolve segment labels", exc_info=True)
        self.speaker = shown.speaker
        self.last_segment_at = time.monotonic()
        self.segments += 1
        self._mirror_dirty = True
        session = engine.session_id if engine is not None else self.session_id
        self._bus.publish("segment", {"session": session, "segment": segment_dict(shown)})

    def _on_revision(self, seg: Segment) -> None:
        """A refined line: same ``seq``, better text. The page swaps the text in place."""
        engine = self._engine
        self._mirror_dirty = True
        session = engine.session_id if engine is not None else self.session_id
        self._bus.publish("revision", {"session": session, "seq": seg.seq, "text": seg.text})

    def _drop_subscriptions(self) -> None:
        for name in ("_unsubscribe", "_unsubscribe_rev"):
            fn = getattr(self, name)
            if fn is not None:
                fn()
                setattr(self, name, None)

    def _publish_status(self) -> None:
        self._bus.publish("status", {"recorder": self.snapshot()})


def segment_dict(seg: Segment, *, with_words: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "seq": seg.seq,
        "t_start": seg.t_start,
        "t_end": seg.t_end,
        "source": seg.source,
        "speaker": seg.speaker,
        "speaker_id": seg.speaker_id,
        "provisional": bool(seg.provisional),
        "text": seg.text,
        "lang": seg.lang,
    }
    if with_words and seg.words:
        out["words"] = seg.words
    return out
