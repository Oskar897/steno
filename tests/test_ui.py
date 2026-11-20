"""The web UI's HTTP API: security checks, endpoints, jobs, recording, live events.

Everything runs against a real server on 127.0.0.1 with a fake engine (synthetic audio,
fake ASR) and fake job runners. No audio devices, no real Claude CLI.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from steno import labels, llm, paths
from steno.audio import ArraySource
from steno.config import Config
from steno.engine import Engine
from steno.profiles import SessionEmbeddings
from steno.transcript import SessionWriter
from steno.ui.api import UiApp, default_runners
from steno.ui.events import EventBus
from steno.ui.recorder import normalise_level
from steno.ui.server import UiServer

from .synth import EnergyVad, FakeAsr, silence, timeline, voice_vector

# --------------------------------------------------------------------------- helpers


class Client:
    def __init__(self, server: UiServer):
        self.server = server

    def raw(self, method, path, body=None, *, token="ok", headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        hdrs = dict(headers or {})
        if token == "ok":
            hdrs["X-Steno-Token"] = self.server.token
        elif token:
            hdrs["X-Steno-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.putrequest(method, path, skip_host=host is not None)
        if host is not None:
            conn.putheader("Host", host)
        for k, v in hdrs.items():
            conn.putheader(k, v)
        if data is not None:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, resp, payload

    def call(self, method, path, body=None, **kw):
        status, _, payload = self.raw(method, path, body, **kw)
        return status, (json.loads(payload) if payload else None)

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, {} if body is None else body, **kw)


def wait_for(predicate, timeout=15.0, interval=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


@pytest.fixture
def make_ui():
    servers: list[UiServer] = []

    def make(**kw) -> tuple[UiServer, Client]:
        app = UiApp(kw.pop("cfg", Config()), **kw)
        server = UiServer(app)
        server.start()
        servers.append(server)
        return server, Client(server)

    yield make
    for server in servers:
        server.app.recorder.shutdown(drain=False)
        server.close()


def make_session(started: str, lines, *, model="fake-asr", close=True):
    """A finished session with fake lines: (t_start, t_end, speaker, speaker_id, text)."""
    writer = SessionWriter.create(
        model=model, sources=["mic"], now=datetime.fromisoformat(started))
    for t0, t1, speaker, sid, text in lines:
        writer.append(t_start=t0, t_end=t1, source="mic", speaker=speaker, text=text, lang="sv",
                      speaker_id=sid, provisional=sid is not None and sid.startswith("S"))
    if close:
        writer.close()
    return writer


LINES = [
    (0.0, 3.0, "Speaker 1", "S1", "Hello there everyone"),
    (3.5, 7.0, "Speaker 2", "S2", "Good morning, let us begin"),
    (7.5, 9.0, "Speaker 1", "S1", "First item on the list"),
]


class GatedRunner:
    """A fake job that blocks until released, reporting progress."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, session_dir, params, progress):
        self.calls.append((Path(session_dir).name, dict(params)))
        progress("working", 1, 4)
        self.started.set()
        assert self.release.wait(10)
        return "all done"


def fake_engine_factory(audio, *, realtime=True, record=None):
    def factory(cfg, keep_audio, mic, loopback):
        if record is not None:
            record.append({"keep_audio": keep_audio, "mic": mic, "loopback": loopback})
        src = ArraySource("mic", audio, realtime=realtime)
        return Engine(cfg, FakeAsr(), [src], vad_factory=EnergyVad, keep_audio=False)

    return factory


# --------------------------------------------------------------------------- security


def test_page_needs_token_and_assets_are_self_contained(make_ui):
    server, c = make_ui()
    status, _, body = c.raw("GET", "/", token=None)
    assert status == 403
    status, _, body = c.raw("GET", "/?token=wrong", token=None)
    assert status == 403
    status, resp, body = c.raw("GET", f"/?token={server.token}", token=None)
    assert status == 200 and b"Steno" in body
    csp = resp.getheader("Content-Security-Policy")
    assert "default-src 'none'" in csp and "script-src 'self'" in csp
    assert resp.getheader("Access-Control-Allow-Origin") is None

    assert c.raw("GET", "/mini", token=None)[0] == 403
    status, _, body = c.raw("GET", f"/mini?token={server.token}", token=None)
    assert status == 200 and b"mini.js" in body

    for name, ctype in (("app.js", "javascript"), ("style.css", "text/css"),
                        ("mini.js", "javascript"), ("mini.css", "text/css")):
        status, resp, body = c.raw("GET", f"/static/{name}", token=None)
        assert status == 200 and ctype in resp.getheader("Content-Type") and body
    assert c.raw("GET", "/static/../server.py", token=None)[0] == 404
    assert c.raw("GET", "/static/nope.js", token=None)[0] == 404

    # offline and private: the shipped frontend references no other origin
    static = Path(__file__).resolve().parents[1] / "src" / "steno" / "ui" / "static"
    for f in static.iterdir():
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://|//cdn|@import|url\(\s*['\"]?http", text), f.name


def test_api_needs_the_token(make_ui):
    _, c = make_ui()
    assert c.get("/api/status", token=None)[0] == 401
    assert c.get("/api/status", token="nope")[0] == 401
    assert c.get("/api/sessions", token="")[0] == 401
    assert c.call("POST", "/api/record/stop", {}, token=None)[0] == 401
    assert c.get("/api/events", token=None)[0] == 401
    status, data = c.get("/api/status")
    assert status == 200 and data["recorder"]["state"] == "idle"


def test_host_and_origin_checks(make_ui):
    server, c = make_ui()
    port = server.port
    assert c.get("/api/status", host="evil.example")[0] == 403
    assert c.get("/api/status", host=f"evil.example:{port}")[0] == 403
    assert c.get("/api/status", host="127.0.0.1")[0] == 403  # wrong or missing port
    assert c.get("/api/status", host=f"127.0.0.1:{port + 1}")[0] == 403
    assert c.get("/api/status", host=f"localhost:{port}")[0] == 200
    assert c.get("/api/status", host=f"127.0.0.1:{port}")[0] == 200
    status, _, _ = c.raw("GET", f"/?token={server.token}", token=None, host="evil.example")
    assert status == 403
    assert c.get("/api/status", headers={"Origin": "http://evil.example"})[0] == 403
    assert c.get("/api/status", headers={"Origin": f"http://127.0.0.1:{port}"})[0] == 200


def test_no_cors_and_strict_methods_and_content_type(make_ui):
    server, c = make_ui()
    status, resp, _ = c.raw("OPTIONS", "/api/status")
    assert status == 405 and resp.getheader("Access-Control-Allow-Origin") is None
    assert c.raw("PUT", "/api/status")[0] == 405
    status, resp, _ = c.raw("GET", "/api/status")
    assert resp.getheader("Access-Control-Allow-Origin") is None
    assert resp.getheader("Cache-Control") == "no-store"
    # a "simple" cross-site POST (text/plain) must not reach the handlers
    status, _, _ = c.raw("POST", "/api/record/stop", None, headers={"Content-Type": "text/plain"})
    assert status == 415
    assert c.get("/api/nothing")[0] == 404
    assert c.get("/api/sessions/..%2f..")[0] == 404


def test_server_binds_loopback_only(make_ui):
    server, _ = make_ui()
    assert server._httpd.server_address[0] == "127.0.0.1"
    assert len(server.token) >= 24
    assert server.token not in server.url.split("?")[0]


# --------------------------------------------------------------------------- sessions


def test_sessions_list_and_detail(make_ui):
    make_session("2026-01-01T10:00:00", LINES)
    make_session("2026-01-02T09:00:00", LINES[:1])
    _, c = make_ui()
    status, data = c.get("/api/sessions")
    assert status == 200
    ids = [s["id"] for s in data["sessions"]]
    assert ids == ["20260102-090000", "20260101-100000"]  # newest first
    first = data["sessions"][1]
    assert first["segments"] == 3 and not first["has_final"] and not first["has_audio"]

    status, d = c.get("/api/sessions/20260101-100000")
    assert status == 200 and d["view"] == "live" and d["llm_source"] == "live"
    assert [s["text"] for s in d["segments"]][0] == "Hello there everyone"
    by_id = {s["id"]: s for s in d["speakers"]}
    assert by_id["S1"]["segments"] == 2 and by_id["S1"]["seconds"] == 4.5
    assert by_id["S2"]["can_name"] and not by_id["S2"]["named"]
    assert d["summary"] is None and d["todo"] is None
    assert c.get("/api/sessions/20200101-000000")[0] == 404
    assert c.get("/api/sessions/20260101-100000?view=bogus")[0] == 400
    assert c.get("/api/sessions/20260101-100000?view=final")[0] == 404


def test_final_transcript_is_preferred_and_unnamed_final_speakers_can_be_named(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)
    final = [
        {"v": 1, "seq": 1, "t_start": 0.0, "t_end": 4.0, "wall": "x", "source": "mic",
         "speaker": "Speaker 1", "text": "Hello there everyone", "lang": "sv",
         "speaker_id": "S1", "final": True},
        {"v": 1, "seq": 2, "t_start": 4.0, "t_end": 6.0, "wall": "x", "source": "mic",
         "speaker": "Anna", "text": "Final text", "lang": "sv", "speaker_id": "P:1a2b3c4d",
         "final": True},
    ]
    (writer.dir / "final.jsonl").write_text(
        "\n".join(json.dumps(x) for x in final) + "\n", encoding="utf-8")
    (writer.dir / "summary.md").write_text("# Summary\n- point\n", encoding="utf-8")
    _, c = make_ui()
    status, d = c.get("/api/sessions/20260101-100000")
    assert d["view"] == "final" and d["has_final"] and d["llm_source"] == "final"
    assert [s["text"] for s in d["segments"]] == ["Hello there everyone", "Final text"]
    assert d["summary"].startswith("# Summary")
    assert d["speaker_space"] == "final"
    can = {s["id"]: s["can_name"] for s in d["speakers"]}
    assert can == {"S1": True, "P:1a2b3c4d": False}  # an enrolled person needs no name
    assert c.get("/api/sessions/20260101-100000?view=live")[1]["view"] == "live"
    listed = c.get("/api/sessions")[1]["sessions"][0]
    assert listed["has_final"] and listed["has_summary"]


def test_name_speaker_for_this_session_only(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)
    _, c = make_ui()
    sid = writer.dir.name
    status, res = c.post(f"/api/sessions/{sid}/name", {"speaker": "S2", "name": "  Bob ",
                                                       "save_profile": False})
    assert status == 200 and "this session only" in res["message"]
    d = c.get(f"/api/sessions/{sid}")[1]
    names = {s["id"]: s["name"] for s in d["speakers"]}
    assert names["S2"] == "Bob"
    assert {s["id"]: s["named"] for s in d["speakers"]}["S2"]
    assert c.get("/api/people")[1]["people"] == []  # nothing biometric was stored
    assert labels.SessionLabels(writer.dir).load()["clusters"]["S2"] == {"name": "Bob"}
    assert "**[00:03] Bob:**" in (writer.dir / "transcript.md").read_text(encoding="utf-8")

    assert c.post(f"/api/sessions/{sid}/name", {"speaker": "S9", "name": "Zed"})[0] == 400
    assert c.post(f"/api/sessions/{sid}/name", {"speaker": "me", "name": "Zed"})[0] == 400
    assert c.post(f"/api/sessions/{sid}/name", {"speaker": "S1", "name": "  "})[0] == 400
    assert c.post(f"/api/sessions/{sid}/name", {"speaker": "S1", "name": "x" * 200})[0] == 400
    assert c.post(f"/api/sessions/{sid}/name",
                  {"speaker": "S1", "name": "A", "save_profile": "yes"})[0] == 400
    assert c.post("/api/sessions/20200101-000000/name", {"speaker": "S1", "name": "A"})[0] == 404


def test_name_speaker_with_voice_profile_and_people_management(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)
    emb = SessionEmbeddings(writer.dir)
    for seq in (1, 2, 3):
        emb.add(seq, voice_vector(seq % 2, noise=0.05, seed=seq))
    emb.close()
    _, c = make_ui()
    sid = writer.dir.name

    status, res = c.post(f"/api/sessions/{sid}/name",
                         {"speaker": "Speaker 2", "name": "Carol", "save_profile": True})
    assert status == 200 and "created" in res["message"]
    people = c.get("/api/people")[1]["people"]
    assert [p["name"] for p in people] == ["Carol"]
    assert set(people[0]) == {"id", "name", "embeddings", "created", "updated"}  # no vectors
    pid = people[0]["id"]

    status, res = c.post(f"/api/people/{pid}/rename", {"name": "Caroline"})
    assert status == 200 and "1 session" in res["message"]
    d = c.get(f"/api/sessions/{sid}")[1]
    assert {s["id"]: s["name"] for s in d["speakers"]}["S2"] == "Caroline"
    assert c.post(f"/api/people/{pid}/rename", {"name": ""})[0] == 400
    assert c.post("/api/people/deadbeef/rename", {"name": "X"})[0] == 404
    assert c.post("/api/people/not-an-id/rename", {"name": "X"})[0] == 404

    assert c.call("DELETE", "/api/people/deadbeef")[0] == 404
    status, res = c.call("DELETE", f"/api/people/{pid}")
    assert status == 200 and "deleted" in res["message"]
    assert c.get("/api/people")[1]["people"] == []
    assert not list(paths.profiles_dir().glob("*.bin"))


def test_naming_with_profile_without_embeddings_reports_the_reason(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)
    _, c = make_ui()
    status, res = c.post(f"/api/sessions/{writer.dir.name}/name",
                         {"speaker": "S2", "name": "Dan", "save_profile": True})
    assert status == 400 and "embedded" in res["error"]


# --------------------------------------------------------------------------- jobs


def test_finalize_job_lifecycle_progress_and_busy(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)
    gate = GatedRunner()
    _, c = make_ui(runners={"finalize": gate, "summary": GatedRunner(), "todo": GatedRunner()})
    sid = writer.dir.name

    # the default finalize needs no kept audio; the thorough one does
    _, thorough = make_ui(cfg=dataclasses.replace(
        Config(), post=dataclasses.replace(Config().post, diarization="thorough")))
    assert thorough.post(f"/api/sessions/{sid}/jobs", {"kind": "finalize"})[0] == 400
    assert c.post(f"/api/sessions/{sid}/jobs", {"kind": "bogus"})[0] == 400
    assert c.post(f"/api/sessions/{sid}/jobs", {"kind": "finalize", "speakers": 0})[0] == 400
    assert c.post(f"/api/sessions/{sid}/jobs", {"kind": "finalize", "speakers": "3"})[0] == 400
    assert c.post("/api/sessions/20200101-000000/jobs", {"kind": "finalize"})[0] == 404

    status, res = c.post(f"/api/sessions/{sid}/jobs", {"kind": "finalize", "speakers": 3})
    assert status == 200 and res["job"]["state"] == "running"
    assert gate.started.wait(5)
    assert gate.calls == [(sid, {"speakers": 3})]
    st = c.get("/api/status")[1]
    assert st["job"]["kind"] == "finalize" and st["job"]["stage"] == "working"
    assert (st["job"]["done"], st["job"]["total"]) == (1, 4)

    status, res = c.post(f"/api/sessions/{sid}/jobs", {"kind": "summary"})
    assert status == 409 and "running" in res["error"]  # never two at once

    gate.release.set()
    wait_for(lambda: c.get("/api/status")[1]["job"] is None)
    job = c.get("/api/jobs")[1]["jobs"][0]
    assert job["state"] == "done" and job["message"] == "all done" and not job["error"]

    status, res = c.post(f"/api/sessions/{sid}/jobs", {"kind": "summary"})
    assert status == 200  # free again


def test_job_errors_are_reported_not_swallowed(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)

    def failing(session_dir, params, progress):
        raise RuntimeError("the Claude CLI is not installed")

    def crashing(session_dir, params, progress):
        raise KeyError("boom")

    _, c = make_ui(runners={"summary": failing, "todo": crashing})
    sid = writer.dir.name
    c.post(f"/api/sessions/{sid}/jobs", {"kind": "summary"})
    job = wait_for(lambda: next(
        (j for j in c.get("/api/jobs")[1]["jobs"] if j["state"] != "running"), None))
    assert job["state"] == "error" and job["error"] == "the Claude CLI is not installed"
    c.post(f"/api/sessions/{sid}/jobs", {"kind": "todo", "person": " Alice "})
    job = wait_for(lambda: next(
        (j for j in c.get("/api/jobs")[1]["jobs"]
         if j["kind"] == "todo" and j["state"] != "running"), None))
    assert job["state"] == "error" and "KeyError" in job["error"]
    assert job["params"] == {"person": "Alice"}


def test_default_runners_call_the_library(monkeypatch):
    from steno import finalize

    cfg = Config()
    calls = {}

    def fake_summarize(session_dir, llm_cfg):
        calls["summary"] = (Path(session_dir).name, llm_cfg.model)
        return Path(session_dir) / "summary.md", "final"

    def fake_todos(session_dir, llm_cfg, person=None):
        calls["todo"] = person
        return [object()], 2, "live"

    class Result:
        segments, speakers = 5, [("S1", "A", 3.0), ("S2", "B", 2.0)]

    def fake_finalize(session_dir, cfg_, store, *, num_speakers, progress):
        calls["finalize"] = num_speakers
        progress("stage", 1, 2)
        return Result()

    monkeypatch.setattr(llm, "summarize", fake_summarize)
    monkeypatch.setattr(llm, "extract_todos", fake_todos)
    monkeypatch.setattr(finalize, "finalize_session", fake_finalize)
    runners = default_runners(cfg, lambda: None)
    seen = []
    progress = lambda *a: seen.append(a)  # noqa: E731
    assert "final transcript" in runners["summary"](Path("x/20260101-100000"), {}, progress)
    assert calls["summary"] == ("20260101-100000", cfg.llm.model)
    assert "1 to-do" in runners["todo"](Path("x"), {"person": "Alice"}, progress)
    assert calls["todo"] == "Alice"
    assert "5 lines, 2 speaker" in runners["finalize"](Path("x"), {"speakers": 2}, progress)
    assert calls["finalize"] == 2 and ("stage", 1, 2) in seen


# --------------------------------------------------------------------------- recording


def burst_audio():
    return np.concatenate([timeline((0.5, 1.5, 0.3)), silence(1.5)])


def test_record_start_events_stop_and_session(make_ui):
    record: list[dict] = []
    server, c = make_ui(engine_factory=fake_engine_factory(burst_audio(), record=record))

    # subscribe to live events first, as the page does
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
    conn.request("GET", "/api/events", headers={"X-Steno-Token": server.token})
    resp = conn.getresponse()
    assert resp.status == 200 and "text/event-stream" in resp.getheader("Content-Type")

    def next_event():
        name, data = None, None
        while True:
            line = resp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and name is not None:
                return name, data

    assert next_event()[0] == "hello"

    status, res = c.post("/api/record/start", {"keep_audio": True, "mic": True,
                                               "loopback": False})
    assert status == 200 and res["recorder"]["state"] in ("starting", "recording")
    assert c.post("/api/record/start", {})[0] == 409  # already running
    assert c.post("/api/record/start", {"mic": "yes"})[0] == 400
    assert record == [{"keep_audio": True, "mic": True, "loopback": False}]

    seen_segment = None
    saw_recording = False
    deadline = time.monotonic() + 15
    while seen_segment is None and time.monotonic() < deadline:
        name, data = next_event()
        if name == "status" and data.get("recorder"):
            rec = data["recorder"]
            saw_recording |= rec["state"] == "recording" and rec["elapsed_s"] >= 0
        if name == "segment":
            seen_segment = data
    conn.close()
    assert seen_segment is not None, "no segment event arrived"
    assert seen_segment["segment"]["seq"] == 1
    assert seen_segment["segment"]["text"].startswith("amp")
    assert seen_segment["segment"]["speaker"] == "Me"
    assert saw_recording

    st = wait_for(lambda: (s := c.get("/api/status")[1])["recorder"]["speaker"] and s)
    assert st["recorder"]["state"] == "recording" and st["recorder"]["speaker"] == "Me"
    assert st["recorder"]["keep_audio"] is True and st["recorder"]["sources"] == ["mic"]
    session_id = st["recorder"]["session"]
    assert c.get("/api/sessions")[1]["sessions"][0]["recording"] is True
    live = c.get(f"/api/sessions/{session_id}")[1]
    assert live["recording"] and live["segments"][0]["seq"] == 1
    assert c.post(f"/api/sessions/{session_id}/jobs", {"kind": "finalize"})[0] in (400, 409)

    assert c.post("/api/record/stop")[0] == 200
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")
    assert c.post("/api/record/stop")[0] == 409
    sessions = c.get("/api/sessions")[1]["sessions"]
    assert sessions[0]["id"] == session_id and not sessions[0]["recording"]
    assert sessions[0]["ended_at"] and sessions[0]["segments"] >= 1


def test_level_meter_sees_audio(make_ui):
    server, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    c.post("/api/record/start", {"mic": True, "loopback": False})

    def level():
        rec = c.get("/api/status")[1]["recorder"]
        return rec["levels"].get("mic", {}).get("level", 0) > 0 and rec

    rec = wait_for(level)
    assert 0 < rec["levels"]["mic"]["level"] <= 1
    c.post("/api/record/stop")


def test_record_errors_are_shown(make_ui):
    def broken(cfg, keep_audio, mic, loopback):
        raise RuntimeError("No audio device found")

    _, c = make_ui(engine_factory=broken)
    assert c.post("/api/record/start", {"mic": False, "loopback": False})[0] == 409
    assert c.post("/api/record/start", {})[0] == 200
    rec = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "error" and r)
    assert rec["error"] == "No audio device found"
    assert c.post("/api/record/start", {})[0] == 200  # can retry after an error


def test_auto_finalize_after_recording_when_configured(make_ui):
    from dataclasses import replace

    cfg = Config()
    cfg = replace(cfg, post=replace(cfg.post, auto=True))
    ran = threading.Event()

    def runner(session_dir, params, progress):
        ran.set()
        return "ok"

    def factory(cfg_, keep_audio, mic, loopback):
        engine = Engine(cfg_, FakeAsr(), [ArraySource("mic", burst_audio())],
                        vad_factory=EnergyVad)
        return engine

    _, c = make_ui(cfg=cfg, engine_factory=factory, runners={"finalize": runner})
    c.post("/api/record/start", {"mic": True, "loopback": False, "keep_audio": True})
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "recording")
    time.sleep(0.5)
    c.post("/api/record/stop")
    assert ran.wait(10)


# --------------------------------------------------------------------------- units


def test_event_bus_drops_stalled_listeners_without_blocking():
    bus = EventBus(max_queue=2)
    slow, fast = bus.subscribe(), bus.subscribe()
    for i in range(3):
        bus.publish("x", i)
        fast.get_nowait()
    assert bus.listener_count == 1  # the full queue was dropped, publish never blocked
    assert slow.qsize() == 2


def test_normalise_level_scale():
    assert normalise_level(0.0) == 0.0
    assert normalise_level(1.0) == 1.0
    assert normalise_level(1e-6) == 0.0
    assert 0.45 < normalise_level(0.03) < 0.55  # about -30 dBFS


def test_clean_runner_and_session_payload(make_ui, monkeypatch):

    writer = make_session("2026-01-01T10:00:00", LINES)
    sid = "20260101-100000"
    _, c = make_ui()
    d = c.get(f"/api/sessions/{sid}")[1]
    assert d["clean"] is None and d["clean_source"] == "live" and d["llm_source"] == "live"

    def fake_run(system, text, cfg, runner=None):
        return json.dumps([{"id": 1, "old": "everyone", "new": "everybody"}])

    monkeypatch.setattr(llm, "run_claude", fake_run)
    runners = default_runners(Config(), lambda: None)
    seen = []
    msg = runners["clean"](writer.dir, {}, lambda *a: seen.append(a))
    assert "1 of 1 edit(s) applied" in msg and "live text" in msg
    assert seen and seen[0][0] == "waiting for Claude"

    d = c.get(f"/api/sessions/{sid}")[1]
    assert d["clean"]["fresh"] and d["clean"]["applied"] == 1 and d["llm_source"] == "clean"
    assert c.get("/api/sessions")[1]["sessions"][0]["has_clean"]
    assert d["view"] == "clean" and d["segments"][0]["text"] == "Hello there everybody"
    live = c.get(f"/api/sessions/{sid}?view=live")[1]
    assert live["segments"][0]["text"] == "Hello there everyone"  # the live view is the draft


def test_clean_is_a_job_kind(make_ui):
    writer = make_session("2026-01-01T10:00:00", LINES)
    _, c = make_ui(runners={"clean": lambda d, p, progress: "done"})
    status, res = c.post(f"/api/sessions/{writer.session_id}/jobs", {"kind": "clean"})
    assert status == 200 and res["job"]["kind"] == "clean"


def test_mini_strip_follows_the_recorder_focus_x_and_the_setting(monkeypatch):
    import threading
    import types

    from steno import settings
    from steno.ui import window

    calls = []
    mini = types.SimpleNamespace(show=lambda: calls.append("show"),
                                 hide=lambda: calls.append("hide"))
    rec = types.SimpleNamespace(state="idle", session_id=None)
    bridge = window.MiniBridge()
    bridge._main_hwnd = 111
    bridge._mini = mini
    front = {"hwnd": 222}  # some other app is in front
    monkeypatch.setattr(window, "prepare_mini", lambda w=None: True)

    def run():
        stop = threading.Event()
        t = threading.Thread(target=window.watch_mini, args=(mini, rec, stop, 0.01),
                             kwargs={"bridge": bridge, "foreground": lambda: front["hwnd"]},
                             daemon=True)
        t.start()
        return stop, t

    stop, t = run()
    try:
        rec.state = "recording"
        wait_for(lambda: calls == ["show"])
        front["hwnd"] = 111  # Steno itself in front: no strip
        wait_for(lambda: calls == ["show", "hide"])
        front["hwnd"] = 222
        wait_for(lambda: calls[-1] == "show")
        bridge.dismiss()  # x: gone for this recording, even with another app in front
        time.sleep(0.1)
        assert calls[-1] == "hide"
        rec.state = "stopping"
        time.sleep(0.05)
        rec.state = "recording"  # the next recording shows it again
        wait_for(lambda: calls[-1] == "show")
        rec.state = "stopping"  # Stop hides it at once
        wait_for(lambda: calls[-1] == "hide")
    finally:
        stop.set()
        t.join(timeout=2)

    settings.update({"mini_strip": False})  # switched off in Settings: never shown
    calls.clear()
    rec.state = "recording"
    stop, t = run()
    time.sleep(0.15)
    stop.set()
    t.join(timeout=2)
    assert calls == []


def test_a_new_recording_never_reports_the_previous_session(make_ui):
    """Pressing Record must not make the page open the last meeting while models load."""
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    c.post("/api/record/start", {"mic": True, "loopback": False})
    old = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "recording"
                   and r["session"])
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")
    time.sleep(1.1)  # session ids have one-second resolution
    started = c.post("/api/record/start", {"mic": True, "loopback": False})[1]["recorder"]
    assert started["session"] != old
    new = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "recording"
                   and r["session"])
    assert new != old
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")
