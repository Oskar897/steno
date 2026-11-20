"""Voice enrolment from the microphone in the desktop app (People view).

A fake recorder (synthetic tones), a fake embedder and the energy VAD stand in for the
microphone and the models; the real HTTP server and ``ops.enroll_audio`` are used.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from steno import ops, paths
from steno.audio import AudioError
from steno.profiles import ProfileStore
from steno.speakers import SpeakerError
from steno.ui.enroll import MAX_SECONDS, MIN_SECONDS

from .synth import EnergyVad, FakeEmbedder, tone
from .test_ui import fake_engine_factory, make_ui, wait_for  # noqa: F401  (make_ui is a fixture)

CHUNK_S = 0.5


class FakeMic:
    """record_fn: emits ``length_s`` of tone in chunks, then (optionally) waits to be stopped."""

    def __init__(self, length_s: float = 12.0, *, hold: bool = False, amp: float = 0.3):
        self.length_s = length_s
        self.hold = hold
        self.amp = amp
        self.requested: list[float] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, seconds, on_chunk, stop):
        self.requested.append(seconds)
        self.started.set()
        parts = []
        for _ in range(int(self.length_s / CHUNK_S)):
            if stop.is_set():
                break
            chunk = tone(CHUNK_S, self.amp)
            parts.append(chunk)
            on_chunk(chunk)
        if self.hold:
            while not (stop.is_set() or self.release.is_set()):
                stop.wait(0.02)
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def fake_enroll(audio, name, store, embedder):
    return ops.enroll_audio(audio, name, store, embedder, vad_factory=EnergyVad)


def deps(mic, **extra):
    return {"enroller_deps": {"record_fn": mic, "embedder_factory": FakeEmbedder,
                              "enroll_fn": fake_enroll, **extra}}


def state(c) -> dict:
    return c.get("/api/people/enroll")[1]["enroll"]


def wait_state(c, want: str) -> dict:
    wait_for(lambda: state(c)["state"] == want)
    return state(c)


# --------------------------------------------------------------------------- happy path


def test_enroll_creates_a_person_and_reports_progress(make_ui):  # noqa: F811
    mic = FakeMic(12.0)
    server, c = make_ui(**deps(mic))
    q = server.app.bus.subscribe()
    assert state(c)["state"] == "idle"

    status, data = c.post("/api/people/enroll", {"name": "  Alice  ", "seconds": 30})
    assert status == 200 and data["enroll"]["name"] == "Alice"
    done = wait_state(c, "done")
    assert done["created"] is True and 11 < done["speech_s"] < 13
    assert "little short" in done["message"]  # < 30 s of speech: suggest adding more
    assert mic.requested == [30.0]

    profile = ProfileStore().find_by_name("Alice")
    assert profile is not None and len(profile.embeddings) >= 5
    assert [p["name"] for p in c.get("/api/people")[1]["people"]] == ["Alice"]

    events = []
    while not q.empty():
        name, ev = q.get_nowait()
        if name == "enroll":
            events.append(ev)
    states = [e["state"] for e in events]
    assert states[0] == "preparing" and states[-1] == "done"
    assert {"recording", "processing"} <= set(states)
    rec = [e for e in events if e["state"] == "recording"]
    assert max(e["level"] for e in rec) > 0.5  # the loud tone shows on the meter
    assert max(e["elapsed_s"] for e in rec) > 0
    assert all(e["level"] == 0.0 for e in events if e["state"] in ("processing", "done"))


def test_add_voice_extends_an_existing_person(make_ui):  # noqa: F811
    _, c = make_ui(**deps(FakeMic(31.0)))
    assert c.post("/api/people/enroll", {"name": "Alice"})[0] == 200
    first = wait_state(c, "done")
    assert first["created"] and "little short" not in first["message"]
    before = len(ProfileStore().find_by_name("Alice").embeddings)

    mic2 = FakeMic(12.0)
    _, c2 = make_ui(**deps(mic2))
    status, _ = c2.post("/api/people/enroll", {"name": "alice", "existing": True})
    assert status == 200
    again = wait_state(c2, "done")
    assert again["created"] is False and again["name"] == "Alice"  # stored spelling kept
    assert "Added voice to" in again["message"]
    assert len(ProfileStore().find_by_name("Alice").embeddings) > before
    assert len(ProfileStore().list()) == 1
    assert mic2.requested == [30.0]


def test_enroll_never_writes_audio_to_disk(make_ui):  # noqa: F811
    _, c = make_ui(**deps(FakeMic(12.0)))
    c.post("/api/people/enroll", {"name": "Alice"})
    wait_state(c, "done")
    root = paths.data_dir()
    leftovers = [p for p in root.rglob("*")
                 if p.suffix.lower() in (".wav", ".flac", ".mp3", ".ogg", ".npy")]
    assert leftovers == []


# --------------------------------------------------------------------------- busy and conflicts


def test_second_enrolment_and_meeting_recording_are_refused_while_one_runs(make_ui):  # noqa: F811
    mic = FakeMic(12.0, hold=True)
    _, c = make_ui(**deps(mic))
    assert c.post("/api/people/enroll", {"name": "Alice"})[0] == 200
    assert mic.started.wait(5)
    wait_for(lambda: state(c)["state"] == "recording")

    status, data = c.post("/api/people/enroll", {"name": "Bob"})
    assert status == 409 and "already running" in data["error"]
    status, data = c.post("/api/record/start", {"mic": True, "loopback": False})
    assert status == 409 and "voice recording" in data["error"]

    mic.release.set()
    wait_state(c, "done")
    assert c.post("/api/people/enroll", {"name": "Bob"})[0] == 200  # free again
    wait_state(c, "done")


def test_enrolment_is_refused_while_a_meeting_is_recorded(make_ui):  # noqa: F811
    server, c = make_ui(engine_factory=fake_engine_factory(tone(30.0, 0.3), realtime=True),
                        **deps(FakeMic(12.0)))
    assert c.post("/api/record/start", {"mic": True, "loopback": False})[0] == 200
    wait_for(lambda: server.app.recorder.active)
    status, data = c.post("/api/people/enroll", {"name": "Alice"})
    assert status == 409 and "meeting is being recorded" in data["error"]
    assert state(c)["state"] == "idle"
    assert ProfileStore().find_by_name("Alice") is None
    c.post("/api/record/stop")


# --------------------------------------------------------------------------- cancel


def test_finish_early_processes_what_was_recorded(make_ui):  # noqa: F811
    _, c = make_ui(**deps(FakeMic(12.0, hold=True)))
    c.post("/api/people/enroll", {"name": "Alice", "seconds": 60})
    wait_for(lambda: state(c)["state"] == "recording" and state(c)["elapsed_s"] >= 11)
    status, data = c.post("/api/people/enroll/cancel")
    assert status == 200 and "enroll" in data
    done = wait_state(c, "done")
    assert done["created"] and ProfileStore().find_by_name("Alice") is not None


def test_cancel_when_too_little_was_recorded_creates_nothing(make_ui):  # noqa: F811
    _, c = make_ui(**deps(FakeMic(3.0, hold=True)))
    c.post("/api/people/enroll", {"name": "Alice"})
    wait_for(lambda: state(c)["state"] == "recording" and state(c)["elapsed_s"] >= 2.9)
    c.post("/api/people/enroll/cancel")
    idle = wait_state(c, "idle")
    assert "Cancelled" in idle["message"]
    assert ProfileStore().find_by_name("Alice") is None
    assert c.post("/api/people/enroll/cancel")[0] == 200  # nothing runs: harmless


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("asked, used", [(1, MIN_SECONDS), (9999, MAX_SECONDS), (60, 60.0),
                                         (None, 30.0)])
def test_seconds_are_clamped(make_ui, asked, used):  # noqa: F811
    mic = FakeMic(11.0)
    _, c = make_ui(**deps(mic))
    body = {"name": "Alice"} if asked is None else {"name": "Alice", "seconds": asked}
    assert c.post("/api/people/enroll", body)[0] == 200
    wait_state(c, "done")
    assert mic.requested == [used]


def test_start_validates_the_request(make_ui):  # noqa: F811
    mic = FakeMic(12.0)
    _, c = make_ui(**deps(mic))
    assert c.post("/api/people/enroll", {})[0] == 400
    assert c.post("/api/people/enroll", {"name": "   "})[0] == 400
    assert c.post("/api/people/enroll", {"name": 5})[0] == 400
    assert c.post("/api/people/enroll", {"name": "x" * 200})[0] == 400
    assert c.post("/api/people/enroll", {"name": "Alice", "seconds": "soon"})[0] == 400
    assert c.post("/api/people/enroll", {"name": "Alice", "seconds": True})[0] == 400
    assert c.post("/api/people/enroll", {"name": "Alice", "existing": "yes"})[0] == 400
    status, data = c.post("/api/people/enroll", {"name": "Nobody", "existing": True})
    assert status == 404 and "no such person" in data["error"]
    assert not mic.requested and state(c)["state"] == "idle"

    c.post("/api/people/enroll", {"name": "Alice"})
    wait_state(c, "done")
    status, data = c.post("/api/people/enroll", {"name": "ALICE", "existing": False})
    assert status == 409 and "already has" in data["error"]
    # auth and routing: enroll does not collide with the per-person routes
    assert c.post("/api/people/enroll", {"name": "Bob"}, token=None)[0] == 401
    assert c.post("/api/people/enroll/nope", {})[0] == 404


# --------------------------------------------------------------------------- failures


def test_no_speech_ends_in_a_readable_error(make_ui):  # noqa: F811
    _, c = make_ui(**deps(FakeMic(12.0, amp=0.0)))
    c.post("/api/people/enroll", {"name": "Alice"})
    err = wait_state(c, "error")
    assert "no speech" in err["message"]
    assert ProfileStore().find_by_name("Alice") is None
    assert c.post("/api/people/enroll", {"name": "Alice"})[0] == 200  # an error does not block
    wait_state(c, "error")


def test_microphone_and_model_failures_are_reported(make_ui):  # noqa: F811
    def no_mic(seconds, on_chunk, stop):
        raise AudioError("No default input device (microphone) found")

    _, c = make_ui(**deps(no_mic))
    c.post("/api/people/enroll", {"name": "Alice"})
    assert "microphone" in wait_state(c, "error")["message"]

    def no_model():
        raise SpeakerError("model download failed")

    _, c2 = make_ui(**deps(FakeMic(12.0), embedder_factory=no_model))
    c2.post("/api/people/enroll", {"name": "Alice"})
    assert "model download failed" in wait_state(c2, "error")["message"]


def test_unexpected_errors_still_end_in_the_error_state(make_ui):  # noqa: F811
    def boom(audio, name, store, embedder):
        raise RuntimeError("kaboom")

    _, c = make_ui(**deps(FakeMic(12.0), enroll_fn=boom))
    c.post("/api/people/enroll", {"name": "Alice"})
    assert "kaboom" in wait_state(c, "error")["message"]
