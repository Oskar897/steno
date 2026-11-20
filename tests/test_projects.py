"""Projects, meeting folders and their API (fake sessions and folders under tmp_path only)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from steno import projects
from steno.audio import AudioChunk
from steno.ui.recorder import _TapQueue

from .test_ui import LINES, burst_audio, fake_engine_factory, make_session, make_ui, wait_for

__all__ = ["make_ui"]  # fixture re-export


def test_create_project_sets_up_a_private_steno_folder(tmp_path):
    p = projects.add_project(tmp_path, name="Course: AI/ML", create=True)
    assert p.path == (tmp_path / "Course AI ML").resolve() and p.name == "Course AI ML"
    steno = p.path / "Steno"
    assert (steno / ".gitignore").read_text(encoding="utf-8").strip().endswith("*")
    assert "summary.md" in (steno / "CLAUDE.md").read_text(encoding="utf-8")
    assert [x.id for x in projects.list_projects()] == [p.id]
    assert projects.add_project(p.path).id == p.id  # adding again does not duplicate
    assert len(projects.list_projects()) == 1
    projects.remove_project(p.id)
    assert projects.list_projects() == [] and steno.is_dir()  # files stay


def test_own_claude_md_is_never_overwritten(tmp_path):
    (tmp_path / "Steno").mkdir()
    (tmp_path / "Steno" / "CLAUDE.md").write_text("my notes", encoding="utf-8")
    projects.add_project(tmp_path)
    assert (tmp_path / "Steno" / "CLAUDE.md").read_text(encoding="utf-8") == "my notes"


def test_meeting_folder_lifecycle(tmp_path):
    a = projects.add_project(tmp_path / ".", name="A", create=True)
    b = projects.add_project(tmp_path, name="B", create=True)
    writer = make_session("2026-01-01T10:30:00", LINES)
    m = projects.create_meeting(a, writer.dir, "Kickoff?")
    assert m.folder.name == "2026-01-01 10.30 Kickoff" and m.folder.parent == a.steno_dir
    text = (m.folder / "transcript.md").read_text(encoding="utf-8")
    assert text.startswith("# Kickoff") and "Hello there everyone" in text
    assert "**recording**" in (a.steno_dir / "index.md").read_text(encoding="utf-8")
    assert projects.meeting_folder(writer.dir) == m.folder
    assert projects.project_of(writer.dir).id == a.id

    (writer.dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
    projects.sync(writer.dir)
    assert (m.folder / "summary.md").is_file()

    r = projects.rename_meeting(writer.dir, "Planning")
    assert r.folder.name == "2026-01-01 10.30 Planning" and not m.folder.exists()
    assert projects.meeting_title(writer.dir) == "Planning"

    mv = projects.move_meeting(writer.dir, b)
    assert mv.folder.parent == b.steno_dir and (mv.folder / "summary.md").is_file()
    assert projects.list_meetings(a) == [] and len(projects.list_meetings(b)) == 1
    assert "Planning" in (b.steno_dir / "index.md").read_text(encoding="utf-8")
    assert projects.inbox_sessions() == []


def test_sessions_without_a_meeting_folder_are_the_inbox(tmp_path):
    w = make_session("2026-01-02T09:00:00", LINES)
    assert [s.id for s in projects.inbox_sessions()] == [w.dir.name]
    projects.rename_meeting(w.dir, "Old one")
    assert projects.meeting_title(w.dir) == "Old one"
    p = projects.add_project(tmp_path)
    projects.move_meeting(w.dir, p)
    assert projects.inbox_sessions() == [] and projects.meeting_title(w.dir) == "Old one"


def test_paused_audio_reaches_the_engine_as_silence():
    got = []

    class Sink:
        def put(self, chunk, *a, **k):
            got.append(chunk)

    paused = {"on": False}
    q = _TapQueue(Sink(), lambda *a: None, lambda: paused["on"])
    loud = np.full(160, 0.5, dtype=np.float32)
    q.put(AudioChunk("mic", loud, 0.0))
    paused["on"] = True
    q.put(AudioChunk("mic", loud, 0.01))
    assert got[0].samples.max() == 0.5 and got[1].samples.max() == 0.0
    assert got[1].t_start == 0.01 and loud.max() == 0.5  # clock kept, input untouched


def test_project_api_and_recording_into_a_project(make_ui, tmp_path):
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    assert c.post("/api/projects", {"path": str(tmp_path / "missing")})[0] == 400
    status, res = c.post("/api/projects", {"path": str(tmp_path), "name": "Thesis",
                                           "create": True})
    assert status == 200
    pid = res["project"]["id"]
    assert c.get("/api/projects")[1]["projects"][0]["name"] == "Thesis"

    c.post("/api/record/start", {"mic": True, "loopback": False, "project": pid,
                                 "title": "Weekly"})
    rec = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "recording"
                   and r)
    assert rec["project"] == pid
    assert c.post("/api/record/pause")[1]["recorder"]["paused"] is True
    assert c.post("/api/record/pause")[0] == 409
    assert c.post("/api/record/resume")[1]["recorder"]["paused"] is False
    time.sleep(0.3)
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")

    meetings = c.get(f"/api/projects/{pid}/meetings")[1]["meetings"]
    assert len(meetings) == 1 and meetings[0]["title"] == "Weekly"
    sid = meetings[0]["session"]
    folder = tmp_path / "Thesis" / "Steno" / meetings[0]["folder"].split("\\")[-1].split("/")[-1]
    assert (folder / "transcript.md").is_file()
    assert json.loads((folder / ".steno-meeting.json").read_text())["session"] == sid
    assert c.get(f"/api/sessions/{sid}")[1]["title"] == "Weekly"
    assert c.get(f"/api/sessions/{sid}/doc/summary")[0] == 404

    assert c.post(f"/api/meetings/{sid}/rename", {"title": "Weekly sync"})[1]["title"] == \
        "Weekly sync"
    other = c.post("/api/projects", {"path": str(tmp_path), "name": "Other",
                                     "create": True})[1]["project"]["id"]
    assert c.post(f"/api/meetings/{sid}/move", {"project": other})[0] == 200
    assert c.get(f"/api/projects/{pid}/meetings")[1]["meetings"] == []
    assert c.get(f"/api/projects/{other}/meetings")[1]["meetings"][0]["title"] == "Weekly sync"
    assert c.get("/api/projects/inbox/meetings")[1]["meetings"] == []

    # archive hides it from the counts but keeps everything; unarchive brings it back
    assert c.post(f"/api/meetings/{sid}/archive", {"archived": True})[1]["archived"] is True
    listed = c.get(f"/api/projects/{other}/meetings")[1]["meetings"]
    assert listed[0]["archived"] is True
    counts = {p["id"]: p["meetings"] for p in c.get("/api/projects")[1]["projects"]}
    assert counts[other] == 0
    assert c.post(f"/api/meetings/{sid}/archive", {"archived": "yes"})[0] == 400
    assert c.post(f"/api/meetings/{sid}/archive", {"archived": False})[1]["archived"] is False
    assert c.get(f"/api/projects/{other}/meetings")[1]["meetings"][0]["archived"] is False

    # delete removes the session and the meeting folder, nothing else
    moved = Path(c.get(f"/api/projects/{other}/meetings")[1]["meetings"][0]["folder"])
    assert moved.is_dir()
    assert c.raw("DELETE", f"/api/meetings/{sid}")[0] == 200
    assert not moved.exists() and moved.parent.is_dir()
    assert c.get(f"/api/sessions/{sid}")[0] == 404
    assert c.get(f"/api/projects/{other}/meetings")[1]["meetings"] == []
    assert c.raw("DELETE", f"/api/meetings/{sid}")[0] == 404
    assert c.raw("DELETE", "/api/meetings/..")[0] == 404

    assert c.raw("DELETE", f"/api/projects/{pid}")[0] == 200
    assert c.get(f"/api/projects/{pid}/meetings")[0] == 404
