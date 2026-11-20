"""Voice enrolment from the microphone for the desktop app (People view).

One enrolment at a time, on a background thread: record a few seconds of one person talking,
embed the speech and add it to a new or existing voice profile (``ops.enroll_audio``, the same
code as ``steno enroll``). The audio only lives in memory and is dropped afterwards.

States: ``idle`` -> ``preparing`` (loading the speaker model) -> ``recording`` -> ``processing``
-> ``done`` | ``error``. Every change, and the live level while recording (about 4 times a
second), is published as the SSE event ``enroll``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from steno import ops
from steno.audio import TARGET_RATE, AudioChunk, AudioError, CaptureSource
from steno.config import Config
from steno.profiles import ProfileError, ProfileStore
from steno.secure import SecureStorageError
from steno.speakers import SpeakerError
from steno.ui.events import EventBus
from steno.ui.recorder import normalise_level

log = logging.getLogger(__name__)

MIN_SECONDS = 10.0  # shortest recording that is processed (also the floor of the length choice)
MAX_SECONDS = 120.0
DEFAULT_SECONDS = 30.0
PUBLISH_EVERY_S = 0.25

#: record_fn(seconds, on_chunk, stop) -> mono 16 kHz float32 audio. ``on_chunk(samples)`` gets
#: every captured chunk; recording ends after ``seconds`` or as soon as ``stop`` is set.
RecordFn = Callable[[float, Callable[[np.ndarray], None], threading.Event], np.ndarray]
EnrollFn = Callable[[np.ndarray, str, ProfileStore, Any], ops.EnrollResult]


class EnrollError(RuntimeError):
    """Enrolment cannot start right now (busy, or a meeting is being recorded)."""


def record_mic(seconds: float, on_chunk: Callable[[np.ndarray], None],
               stop: threading.Event) -> np.ndarray:
    """Capture the default microphone like ``audio.record_seconds``, reporting every chunk."""
    source = CaptureSource("mic")
    sink: queue.Queue[AudioChunk] = queue.Queue()
    source.start(sink, time.monotonic())
    parts: list[np.ndarray] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline and not stop.is_set():
            try:
                chunk = sink.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk.samples.size:
                parts.append(chunk.samples)
                on_chunk(chunk.samples)
            if chunk.eof:
                break
    finally:
        source.stop()
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


class Enroller:
    def __init__(
        self,
        cfg: Config,
        bus: EventBus,
        *,
        store_factory: Callable[[], ProfileStore] = ProfileStore,
        is_recording: Callable[[], bool] = lambda: False,
        record_fn: RecordFn = record_mic,
        embedder_factory: Callable[[], Any] | None = None,
        enroll_fn: EnrollFn | None = None,
    ):
        self._cfg = cfg
        self._bus = bus
        self._store_factory = store_factory
        self._is_recording = is_recording
        self._record_fn = record_fn
        self._embedder_factory = embedder_factory or self._default_embedder
        self._enroll_fn = enroll_fn or self._default_enroll
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._name = ""
        self._seconds = DEFAULT_SECONDS
        self._elapsed = 0.0
        self._level = 0.0
        self._message = ""
        self._created: bool | None = None
        self._speech_s = 0.0
        self._last_pub = 0.0

    # -- defaults -----------------------------------------------------------------

    def _default_embedder(self):
        from steno.speakers import create_embedder

        embedder = create_embedder(self._cfg.speakers)
        embedder.load()
        return embedder

    def _default_enroll(self, audio: np.ndarray, name: str, store: ProfileStore, embedder: Any):
        sp = self._cfg.speakers
        return ops.enroll_audio(
            audio, name, store, embedder, min_chunk_s=sp.min_embed_s,
            window_s=sp.enroll_window_s, hop_s=sp.enroll_hop_s,
            outlier_sigma=sp.enroll_outlier_sigma)

    # -- queries ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._state in ("preparing", "recording", "processing")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "state": self._state, "name": self._name, "elapsed_s": round(self._elapsed, 2),
            "seconds": self._seconds, "level": round(self._level, 3), "message": self._message,
            "created": self._created, "speech_s": round(self._speech_s, 1),
        }

    def _publish(self, snap: dict[str, Any] | None = None) -> None:
        self._bus.publish("enroll", snap if snap is not None else self.snapshot())

    # -- control ------------------------------------------------------------------

    def start(self, name: str, seconds: float = DEFAULT_SECONDS, existing: bool = False) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("name must not be empty")
        seconds = float(min(MAX_SECONDS, max(MIN_SECONDS, seconds)))
        if self._is_recording():
            raise EnrollError("a meeting is being recorded; stop it before adding a voice")
        with self._lock:
            if self._state in ("preparing", "recording", "processing"):
                raise EnrollError("a voice recording is already running")
            self._stop = threading.Event()
            self._state, self._name, self._seconds = "preparing", name, seconds
            self._elapsed, self._level, self._created, self._speech_s = 0.0, 0.0, None, 0.0
            self._message = "Loading the speaker model ..."
            self._last_pub = 0.0
            snap = self._snapshot_locked()
            self._thread = threading.Thread(
                target=self._run, args=(name, seconds, self._stop), name="ui-enroll", daemon=True)
        self._publish(snap)
        self._thread.start()

    def cancel(self) -> dict[str, Any]:
        """Stop recording early: what was recorded is processed if it is long enough."""
        self._stop.set()
        return self.snapshot()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- worker -------------------------------------------------------------------

    def _set(self, state: str, message: str = "", **fields: Any) -> None:
        with self._lock:
            self._state, self._message = state, message
            for key, value in fields.items():
                setattr(self, "_" + key, value)
            if state != "recording":
                self._level = 0.0
            snap = self._snapshot_locked()
        self._publish(snap)

    def _on_chunk(self, samples: np.ndarray) -> None:
        if not samples.size:
            return
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        level = normalise_level(rms)
        now = time.monotonic()
        with self._lock:
            self._elapsed += samples.size / TARGET_RATE
            self._level = max(level, self._level * 0.85)  # fast attack, gentle decay
            due = now - self._last_pub >= PUBLISH_EVERY_S
            if due:
                self._last_pub = now
            snap = self._snapshot_locked() if due else None
        if snap is not None:
            self._publish(snap)

    def _run(self, name: str, seconds: float, stop: threading.Event) -> None:
        try:
            embedder = self._embedder_factory()
            if stop.is_set():
                self._set("idle", "Cancelled.")
                return
            self._set("recording", "Speak now.", elapsed=0.0)
            audio = np.asarray(self._record_fn(seconds, self._on_chunk, stop), dtype=np.float32)
            got_s = audio.size / TARGET_RATE
            if stop.is_set() and got_s < MIN_SECONDS:
                self._set("idle", f"Cancelled. At least {MIN_SECONDS:.0f} s are needed.")
                return
            if got_s <= 0:
                raise AudioError("nothing was recorded from the microphone")
            self._set("processing", "Learning the voice ...", elapsed=got_s)
            result = self._enroll_fn(audio, name, self._store_factory(), embedder)
            del audio  # never kept
            self._finish(result)
        except (AudioError, SpeakerError, ops.OpsError, ProfileError, SecureStorageError,
                ValueError) as exc:
            self._set("error", str(exc))
        except Exception as exc:  # the worker must always end in a visible state
            log.exception("Voice enrolment failed")
            self._set("error", f"{type(exc).__name__}: {exc}")

    def _finish(self, result: ops.EnrollResult) -> None:
        verb = "Created" if result.created else "Added voice to"
        message = f"{verb} {result.profile.name} from {result.speech_s:.0f} s of speech."
        if result.speech_s < ops.ENROLL_SUGGESTED_SPEECH_S:
            message += (f" That is a little short: recognition is more reliable with at least "
                        f"{ops.ENROLL_SUGGESTED_SPEECH_S:.0f} s. Add more voice to improve it.")
        self._set("done", message, created=result.created, speech_s=result.speech_s)
