"""In-process recording for the UI: drives one ``Engine`` and publishes what it does."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from steno import labels
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


class RecorderError(RuntimeError):
    """A start/stop request that cannot be honoured; the message is shown to the user."""


def default_engine_factory(cfg: Config, keep_audio: bool, mic: bool, loopback: bool) -> Engine:
    """Same wiring as ``steno record`` (``steno.cli._run_engine``)."""
    from steno.asr import create_transcriber
    from steno.audio import create_capture_sources
    from steno.speakers import create_embedder

    transcriber = create_transcriber(cfg.asr)
    embedder = create_embedder(cfg.speakers) if cfg.speakers.enabled else None
    sources = create_capture_sources(mic=mic, loopback=loopback)
    return Engine(cfg, transcriber, sources, embedder=embedder, keep_audio=keep_audio)


# --------------------------------------------------------------------------- level meter


class _TapQueue:
    """Looks like the engine's audio queue; reports every chunk before forwarding it."""

    def __init__(self, inner, on_chunk: Callable[[str, np.ndarray], None]):
        self._inner = inner
        self._on_chunk = on_chunk

    def put(self, chunk, *args, **kwargs) -> None:
        try:
            if chunk.samples.size:
                self._on_chunk(chunk.source, chunk.samples)
        except Exception:  # metering must never break capture
            log.debug("level tap failed", exc_info=True)
        self._inner.put(chunk, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _TappedSource:
    def __init__(self, source, on_chunk: Callable[[str, np.ndarray], None]):
        self._source = source
        self._on_chunk = on_chunk
        self.name = source.name

    def start(self, sink, t0: float) -> None:
        self._source.start(_TapQueue(sink, self._on_chunk), t0)

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
        self._engine: Engine | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._t0: float | None = None
        self._levels: dict[str, _Level] = {}
        self._stop_thread: threading.Thread | None = None
        self.on_stopped: Callable[[str, bool], None] | None = None  # (session id, keep_audio)

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

    def start(self, *, keep_audio: bool, mic: bool, loopback: bool) -> None:
        if not (mic or loopback):
            raise RecorderError("nothing to record: enable the microphone or system audio")
        with self._lock:
            if self.active:
                raise RecorderError("already recording")
            self.state = "starting"
            self.error = ""
            self.keep_audio = keep_audio
            self.sources = [n for n, on in (("mic", mic), ("loopback", loopback)) if on]
            self.speaker = ""
            self.segments = 0
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
            engine.sources = [_TappedSource(s, self._on_chunk) for s in engine.sources]
            self._engine = engine  # segment callbacks need it from the first line on
            self._unsubscribe = engine.subscribe(self._on_segment)
            session_dir = engine.start()  # loads the models: can take a while
        except Exception as exc:
            log.exception("Could not start recording")
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
            with self._lock:
                self.state = "error"
                self.error = str(exc).strip() or type(exc).__name__
                self._engine = None
                self._t0 = None
            self._publish_status()
            return
        with self._lock:
            self.session_id = session_dir.name
            self._t0 = time.monotonic()
            self.state = "recording"
        self._publish_status()
        self._bus.publish("sessions", {"session": session_dir.name})

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
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        session, keep = self.session_id, self.keep_audio
        with self._lock:
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
                self.on_stopped(session, keep)
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
        session = engine.session_id if engine is not None else self.session_id
        self._bus.publish("segment", {"session": session, "segment": segment_dict(shown)})

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
