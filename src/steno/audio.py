"""Audio capture: microphone and WASAPI loopback as two independent streams.

Every source pushes ``AudioChunk`` objects (16 kHz mono float32) tagged with its
name ("mic" / "loopback") onto one shared queue. Timestamps are in seconds on the
session clock. A source ends by pushing a chunk with ``eof=True``.

WASAPI loopback delivers *no* callbacks while nothing is playing. ``GapFiller``
turns such gaps into explicit zeros so the stream stays continuous and the VAD
sees silence (which is what closes an utterance).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

TARGET_RATE = 16000
SOURCE_MIC = "mic"
SOURCE_LOOPBACK = "loopback"


class AudioError(RuntimeError):
    """Capture could not be set up (missing extra, no device, device busy)."""


@dataclass(frozen=True)
class AudioChunk:
    source: str
    samples: np.ndarray  # float32 mono, 16 kHz
    t_start: float  # seconds on the session clock
    eof: bool = False


class AudioSource(Protocol):
    """A producer of ``AudioChunk``s. ``stop()`` must end the stream with an EOF chunk."""

    name: str

    def start(self, sink: queue.Queue[AudioChunk], t0: float) -> None:
        """Begin pushing chunks. ``t0`` is the session's ``time.monotonic()`` origin."""
        ...

    def stop(self) -> None: ...


# --------------------------------------------------------------------------- gap filling


class GapFiller:
    """Decides how many zero samples to insert so a stream tracks wall-clock time."""

    def __init__(
        self, sample_rate: int, start: float, idle_s: float = 0.25, tolerance_s: float = 0.1
    ):
        self.sr = sample_rate
        self.idle_s = idle_s
        self.tolerance_s = tolerance_s
        self.last_end = start

    def on_chunk(self, n_samples: int, now: float) -> int:
        """Zeros to insert *before* a chunk of ``n_samples`` that arrived at ``now``."""
        gap = (now - n_samples / self.sr) - self.last_end
        self.last_end = now
        return round(gap * self.sr) if gap > self.tolerance_s else 0

    def on_idle(self, now: float) -> int:
        """Zeros to insert because no data arrived for a while."""
        gap = now - self.last_end
        if gap < self.idle_s:
            return 0
        self.last_end = now
        return round(gap * self.sr)


# --------------------------------------------------------------------------- devices


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_rate: int
    is_loopback: bool
    is_default_input: bool = False
    is_default_output_loopback: bool = False


def _import_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise AudioError(
            "Audio capture needs the 'audio' extra (PyAudioWPatch, Windows only): "
            "uv sync --extra audio"
        ) from exc
    return pyaudio


def list_devices() -> list[DeviceInfo]:
    """Enumerate devices, flagging the ones Steno would use by default."""
    pyaudio = _import_pyaudio()
    p = pyaudio.PyAudio()
    try:
        host_apis = {
            i: p.get_host_api_info_by_index(i)["name"] for i in range(p.get_host_api_count())
        }
        default_in = _default_input_index(p, pyaudio)
        try:
            default_loop = p.get_default_wasapi_loopback()["index"]
        except Exception:
            default_loop = None
        out = []
        for d in p.get_device_info_generator():
            idx = d["index"]
            out.append(
                DeviceInfo(
                    index=idx,
                    name=d["name"],
                    host_api=host_apis.get(d["hostApi"], "?"),
                    max_input_channels=int(d["maxInputChannels"]),
                    max_output_channels=int(d["maxOutputChannels"]),
                    default_rate=int(d["defaultSampleRate"]),
                    is_loopback=bool(d.get("isLoopbackDevice", False)),
                    is_default_input=idx == default_in,
                    is_default_output_loopback=idx == default_loop,
                )
            )
        return out
    finally:
        p.terminate()


def _default_input_index(p, pyaudio) -> int | None:
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        idx = wasapi["defaultInputDevice"]
        if idx >= 0:
            return idx
    except Exception:
        pass
    try:
        return p.get_default_input_device_info()["index"]
    except Exception:
        return None


# --------------------------------------------------------------------------- live capture


class CaptureSource:
    """One PyAudioWPatch input stream (mic, or loopback of the default output)."""

    def __init__(self, name: str, *, chunk_ms: int = 30):
        if name not in (SOURCE_MIC, SOURCE_LOOPBACK):
            raise ValueError(f"unknown capture source: {name!r}")
        self.name = name
        self._chunk_ms = chunk_ms
        self._pa = None
        self._stream = None
        self._raw: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self.device_name = ""

    # -- device selection ---------------------------------------------------------

    def _pick_device(self, pyaudio, p) -> dict:
        try:
            if self.name == SOURCE_LOOPBACK:
                return p.get_default_wasapi_loopback()
            idx = _default_input_index(p, pyaudio)
            if idx is None:
                raise AudioError("No default input device (microphone) found")
            return p.get_device_info_by_index(idx)
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"No usable {self.name} device: {exc}") from exc

    # -- lifecycle ----------------------------------------------------------------

    def start(self, sink: queue.Queue[AudioChunk], t0: float) -> None:
        pyaudio = _import_pyaudio()
        try:
            import soxr
        except ImportError as exc:
            raise AudioError("Resampling needs the 'audio' extra (soxr)") from exc

        self._pa = pyaudio.PyAudio()
        dev = self._pick_device(pyaudio, self._pa)
        self.device_name = dev["name"]
        rate = int(dev["defaultSampleRate"])
        channels = max(1, int(dev["maxInputChannels"]))
        raw = self._raw

        def callback(in_data, frame_count, time_info, status):
            raw.put((in_data, time.monotonic()))
            return (None, pyaudio.paContinue)

        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=dev["index"],
                frames_per_buffer=max(1, rate * self._chunk_ms // 1000),
                stream_callback=callback,
            )
        except Exception as exc:
            self._pa.terminate()
            self._pa = None
            raise AudioError(f"Could not open {self.name} device '{dev['name']}': {exc}") from exc

        started = time.monotonic()
        self._thread = threading.Thread(
            target=self._work,
            args=(sink, soxr, rate, channels, started, started - t0),
            name=f"capture-{self.name}",
            daemon=True,
        )
        self._thread.start()
        log.info("%s: capturing '%s' (%d Hz, %d ch)", self.name, dev["name"], rate, channels)

    def _work(self, sink, soxr, rate: int, channels: int, started: float, origin: float) -> None:
        resampler = None
        if rate != TARGET_RATE:
            resampler = soxr.ResampleStream(rate, TARGET_RATE, 1, dtype="float32")
        filler = GapFiller(TARGET_RATE, started)
        emitted = 0

        def emit(samples: np.ndarray) -> None:
            nonlocal emitted
            if samples.size:
                sink.put(AudioChunk(self.name, samples, origin + emitted / TARGET_RATE))
                emitted += samples.size

        while True:
            try:
                item = self._raw.get(timeout=0.1)
            except queue.Empty:
                zeros = filler.on_idle(time.monotonic())
                if zeros:
                    emit(np.zeros(zeros, dtype=np.float32))
                continue
            if item is None:
                break
            data, now = item
            mono = np.frombuffer(data, dtype=np.float32).reshape(-1, channels).mean(axis=1)
            if resampler is not None:
                mono = resampler.resample_chunk(mono.astype(np.float32, copy=False))
            zeros = filler.on_chunk(len(mono), now)
            if zeros:
                emit(np.zeros(zeros, dtype=np.float32))
            emit(np.asarray(mono, dtype=np.float32))

        if resampler is not None:
            emit(np.asarray(resampler.resample_chunk(np.zeros(0, np.float32), last=True),
                            dtype=np.float32))
        sink.put(AudioChunk(self.name, np.zeros(0, np.float32), origin + emitted / TARGET_RATE,
                            eof=True))

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:  # pragma: no cover - device unplugged etc.
                log.exception("Error closing %s stream", self.name)
            self._stream = None
        if self._thread is not None:
            self._raw.put(None)
            self._thread.join(timeout=5)
            self._thread = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None


def create_capture_sources(*, mic: bool = True, loopback: bool = True) -> list[CaptureSource]:
    sources = []
    if mic:
        sources.append(CaptureSource(SOURCE_MIC))
    if loopback:
        sources.append(CaptureSource(SOURCE_LOOPBACK))
    return sources


# --------------------------------------------------------------------------- offline source


class ArraySource:
    """Replays an in-memory 16 kHz mono array as a stream (files, tests)."""

    def __init__(
        self,
        name: str,
        audio: np.ndarray,
        *,
        chunk_s: float = 0.1,
        realtime: bool = False,
        t_offset: float = 0.0,
    ):
        self.name = name
        self._audio = np.asarray(audio, dtype=np.float32)
        self._chunk = max(1, int(chunk_s * TARGET_RATE))
        self._realtime = realtime
        self._t_offset = t_offset
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, sink: queue.Queue[AudioChunk], t0: float) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(sink,), name=f"array-{self.name}", daemon=True
        )
        self._thread.start()

    def _run(self, sink: queue.Queue[AudioChunk]) -> None:
        pos = 0
        while pos < len(self._audio) and not self._stop.is_set():
            part = self._audio[pos : pos + self._chunk]
            sink.put(AudioChunk(self.name, part, self._t_offset + pos / TARGET_RATE))
            pos += len(part)
            if self._realtime:
                time.sleep(len(part) / TARGET_RATE)
        sink.put(AudioChunk(self.name, np.zeros(0, np.float32),
                            self._t_offset + pos / TARGET_RATE, eof=True))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


class StreamRecorder:
    """Writes one stream's 16 kHz mono audio to ``audio-<stream>.flac`` (``--keep-audio``).

    Raw audio is sensitive: it stays in the session directory under the user data
    dir, never in the repository.
    """

    def __init__(self, session_dir, stream: str):
        try:
            import soundfile
        except ImportError as exc:
            raise AudioError("Keeping audio needs the 'audio' extra (soundfile)") from exc
        self.path = Path(session_dir) / f"audio-{stream}.flac"
        self._file = soundfile.SoundFile(
            str(self.path), mode="w", samplerate=TARGET_RATE, channels=1,
            format="FLAC", subtype="PCM_16",
        )

    def write(self, samples: np.ndarray) -> None:
        self._file.write(np.clip(samples, -1.0, 1.0))

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()


def record_seconds(source: AudioSource, seconds: float) -> np.ndarray:
    """Capture ``seconds`` of one live source into an array (used by ``steno enroll``)."""
    sink: queue.Queue[AudioChunk] = queue.Queue()
    source.start(sink, time.monotonic())
    parts: list[np.ndarray] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                chunk = sink.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk.samples.size:
                parts.append(chunk.samples)
            if chunk.eof:
                break
    finally:
        source.stop()
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def load_audio_file(path: str) -> np.ndarray:
    """Decode any audio file to 16 kHz mono float32 with PyAV (a faster-whisper dependency).

    Not faster_whisper.audio.decode_audio: its call into PyAV breaks on newer PyAV releases.
    """
    try:
        import av
    except ImportError as exc:
        raise AudioError("Decoding audio files needs the 'asr' extra (PyAV)") from exc
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
    parts: list[np.ndarray] = []

    def collect(frames) -> None:
        for frame in frames or []:
            parts.append(frame.to_ndarray().reshape(-1))

    with av.open(path) as container:
        for frame in container.decode(audio=0):
            collect(resampler.resample(frame))
        collect(resampler.resample(None))  # flush
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0
