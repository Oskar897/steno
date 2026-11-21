"""Projects, meeting folders and their API (fake sessions and folders under tmp_path only)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest

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
    assert m.folder.name == "2026-01-01 Kickoff" and m.folder.parent == a.steno_dir
    text = (m.folder / "transcript.md").read_text(encoding="utf-8")
    assert text.startswith("# Kickoff") and "Hello there everyone" in text
    assert "**recording**" in (a.steno_dir / "index.md").read_text(encoding="utf-8")
    assert projects.meeting_folder(writer.dir) == m.folder
    assert projects.project_of(writer.dir).id == a.id

    (writer.dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
    projects.sync(writer.dir)
    assert (m.folder / "summary.md").is_file()

    r = projects.rename_meeting(writer.dir, "Planning")
    assert r.folder.name == "2026-01-01 Planning" and not m.folder.exists()
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


def test_meeting_types_put_meetings_in_their_own_folders(tmp_path):
    p = projects.add_project(tmp_path, name="Club", create=True)
    for bad in ("..", "../x", "C:/x", "/abs", "Steno", "a/b/c/d/e"):
        with pytest.raises(projects.ProjectError):
            projects.set_types(p.id, [{"name": "X", "folder": bad}])
    with pytest.raises(projects.ProjectError):
        projects.set_types(p.id, [{"name": "A", "folder": "x"}, {"name": "a", "folder": "y"}])
    p = projects.set_types(p.id, [{"name": "Board", "folder": r"Meetings\Board"},
                                  {"name": "1-1"}])
    assert [(t.name, t.folder) for t in p.types] == [("Board", "Meetings/Board"),
                                                    ("1-1", "Meetings/1-1")]

    w1 = make_session("2026-11-20T10:00:00", LINES)
    m1 = projects.create_meeting(p, w1.dir, "Lobby days", "board")
    assert m1.folder == p.path / "Meetings" / "Board" / "2026-11-20 Lobby days"
    assert (m1.folder / ".gitignore").is_file()
    assert projects.type_of(w1.dir) == "Board" and projects.project_of(w1.dir).id == p.id
    w2 = make_session("2026-11-21T10:00:00", LINES)
    projects.create_meeting(p, w2.dir, "Plain")  # no type: Steno/ as before
    assert projects.type_of(w2.dir) is None
    assert [m.session for m in projects.list_meetings(p)] == [w1.dir.name, w2.dir.name]
    index = (p.steno_dir / "index.md").read_text(encoding="utf-8")
    assert "`Meetings/Board/2026-11-20 Lobby days/` (Board)" in index
    with pytest.raises(projects.ProjectError):
        projects.create_meeting(p, w2.dir, "x", "Nope")

    # a type with meetings cannot be dropped or re-pointed; an empty one can
    with pytest.raises(projects.ProjectError):
        projects.set_types(p.id, [{"name": "1-1"}])
    p = projects.set_types(p.id, [{"name": "Styrelse", "folder": "Meetings/Board"}])
    assert projects.type_of(w1.dir) == "Styrelse"

    mv = projects.move_meeting(w2.dir, p, "Styrelse")
    assert mv.folder.parent == p.path / "Meetings" / "Board"
    mv = projects.move_meeting(w1.dir, p)  # back to no type
    assert mv.folder.parent == p.steno_dir and projects.type_of(w1.dir) is None
    projects.delete_meeting(w2.dir)
    assert not (p.path / "Meetings" / "Board" / "2026-11-21 Plain").exists()
    assert (p.path / "Meetings" / "Board").is_dir()


def test_meeting_types_api(make_ui, tmp_path):
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    pid = c.post("/api/projects", {"path": str(tmp_path), "name": "Club",
                                   "create": True})[1]["project"]["id"]
    assert c.post(f"/api/projects/{pid}/types", {"types": "Board"})[0] == 400
    assert c.post(f"/api/projects/{pid}/types", {"types": [{"name": "B", "folder": ".."}]})[0] \
        == 400
    res = c.post(f"/api/projects/{pid}/types", {"types": [{"name": "Board"}]})[1]
    assert res["types"] == [{"name": "Board", "folder": "Meetings/Board"}]
    assert c.get("/api/projects")[1]["projects"][0]["types"] == res["types"]
    assert c.post("/api/record/start", {"mic": True, "loopback": False, "project": pid,
                                        "type": "Nope"})[0] == 400

    c.post("/api/record/start", {"mic": True, "loopback": False, "project": pid,
                                 "type": "Board", "title": "Lobby days"})
    rec = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "recording"
                   and r)
    assert rec["type"] == "Board"
    time.sleep(0.2)
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")
    m = c.get(f"/api/projects/{pid}/meetings")[1]["meetings"][0]
    assert m["type"] == "Board" and "Board" in m["folder"]
    sid = m["session"]
    assert c.get(f"/api/sessions/{sid}")[1]["type"] == "Board"
    assert c.post(f"/api/meetings/{sid}/move", {"project": pid, "type": None})[1]["type"] is None
    assert c.get(f"/api/projects/{pid}/meetings")[1]["meetings"][0]["type"] is None


# --------------------------------------------------------------------------- moving around


def _club(tmp_path):
    p = projects.add_project(tmp_path, name="Club", create=True)
    return projects.set_types(p.id, [{"name": "Board"}, {"name": "Dev team"}])


def test_move_to_the_inbox_and_back(tmp_path):
    p = _club(tmp_path)
    w = make_session("2026-11-20T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Lobby days", "Board")
    (w.dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
    projects.sync(w.dir)
    assert (m.folder / "summary.md").is_file() and projects.inbox_sessions() == []

    out = projects.move_to_inbox(w.dir)
    assert not m.folder.exists() and out.title == "Lobby days"
    assert (p.path / "Meetings" / "Board").is_dir()  # the subfolder itself stays
    assert projects.meeting_folder(w.dir) is None and projects.project_of(w.dir) is None
    assert projects.meeting_title(w.dir) == "Lobby days"
    assert [s.id for s in projects.inbox_sessions()] == [w.dir.name]
    assert (w.dir / "summary.md").read_text(encoding="utf-8") == "# Summary\n"  # nothing lost
    assert projects.list_meetings(p) == []
    assert "Lobby days" not in (p.steno_dir / "index.md").read_text(encoding="utf-8")
    assert projects.move_to_inbox(w.dir).folder == w.dir  # already there: nothing happens

    back = projects.move_meeting(w.dir, p, "Dev team")
    assert back.folder == p.path / "Meetings" / "Dev team" / "2026-11-20 Lobby days"
    assert (back.folder / "summary.md").is_file() and "Hello there" in (
        back.folder / "transcript.md").read_text(encoding="utf-8")
    assert projects.inbox_sessions() == [] and projects.type_of(w.dir) == "Dev team"


def test_move_to_the_inbox_keeps_a_folder_with_other_files(tmp_path):
    p = _club(tmp_path)
    w = make_session("2026-11-20T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Notes")
    (w.dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
    projects.sync(w.dir)
    (m.folder / "mine.txt").write_text("my own notes", encoding="utf-8")
    (m.folder / "summary.md").write_text("# Summary, edited by hand\n", encoding="utf-8")

    projects.move_to_inbox(w.dir)
    assert (m.folder / "mine.txt").read_text(encoding="utf-8") == "my own notes"
    assert "edited by hand" in (m.folder / "summary.md").read_text(encoding="utf-8")
    assert not (m.folder / projects.MEETING_FILE).exists()  # no longer a meeting
    assert projects.list_meetings(p) == []
    assert [s.id for s in projects.inbox_sessions()] == [w.dir.name]


def test_move_between_subfolders_and_root_handles_name_clashes(tmp_path):
    p = _club(tmp_path)
    one = make_session("2026-11-20T10:00:00", LINES)
    two = make_session("2026-11-20T11:00:00", LINES)
    a = projects.create_meeting(p, one.dir, "Sync", "Board")
    b = projects.create_meeting(p, two.dir, "Sync", "Dev team")
    assert a.folder.name == b.folder.name == "2026-11-20 Sync"

    moved = projects.move_meeting(two.dir, p, "Board")  # same name already in Board
    assert moved.folder == p.path / "Meetings" / "Board" / "2026-11-20 Sync (2)"
    assert a.folder.is_dir() and projects.type_of(two.dir) == "Board"
    assert not b.folder.exists()
    assert projects.move_meeting(two.dir, p, "Board").folder == moved.folder  # same place

    root = projects.move_meeting(one.dir, p)
    assert root.folder.parent == p.steno_dir and projects.type_of(one.dir) is None
    sub = projects.move_meeting(one.dir, p, "Dev team")
    assert sub.folder.parent == p.path / "Meetings" / "Dev team"
    assert sorted(m.session for m in projects.list_meetings(p)) == sorted(
        [one.dir.name, two.dir.name])


def test_meeting_move_api_to_inbox_and_back(make_ui, tmp_path):
    _, c = make_ui()
    pid = c.post("/api/projects", {"path": str(tmp_path), "name": "Club",
                                   "create": True})[1]["project"]["id"]
    c.post(f"/api/projects/{pid}/types", {"types": [{"name": "Board"}]})
    w = make_session("2026-11-20T10:00:00", LINES)
    sid = w.dir.name
    assert c.post(f"/api/meetings/{sid}/move", {"project": pid, "type": "Board"})[0] == 200
    folder = Path(c.get(f"/api/projects/{pid}/meetings")[1]["meetings"][0]["folder"])
    assert folder.is_dir()

    assert c.post(f"/api/meetings/{sid}/move", {"project": "inbox", "type": "Board"})[0] == 400
    status, res = c.post(f"/api/meetings/{sid}/move", {"project": "inbox"})
    assert status == 200 and res["project"] == "inbox" and not folder.exists()
    assert c.get(f"/api/projects/{pid}/meetings")[1]["meetings"] == []
    inbox = c.get("/api/projects/inbox/meetings")[1]["meetings"]
    assert [m["session"] for m in inbox] == [sid]
    assert c.get("/api/projects")[1]["inbox"] == 1
    assert c.get(f"/api/sessions/{sid}")[1]["project"] is None
    assert c.post(f"/api/meetings/{sid}/move", {"project": "inbox"})[0] == 200  # no-op

    assert c.post(f"/api/meetings/{sid}/move", {"project": pid})[0] == 200
    assert c.get("/api/projects")[1]["inbox"] == 0
    assert c.get(f"/api/sessions/{sid}")[1]["project"] == pid
    assert c.post("/api/meetings/nosuch/move", {"project": "inbox"})[0] == 404


def test_folder_to_open_is_resolved_from_ids_and_names(tmp_path, monkeypatch):
    from steno import paths

    p = _club(tmp_path)
    assert projects.folder_to_open("project", p.id) == p.path
    assert projects.folder_to_open("inbox") == paths.sessions_dir()

    # a subfolder that is not there yet: the nearest existing parent inside the project
    assert projects.folder_to_open("type", p.id, "Board") == p.path
    (p.path / "Meetings").mkdir()
    assert projects.folder_to_open("type", p.id, "Board") == p.path / "Meetings"
    (p.path / "Meetings" / "Board").mkdir()
    assert projects.folder_to_open("type", p.id, "Board") == p.path / "Meetings" / "Board"
    assert projects.folder_to_open("type", p.id, "board") == p.path / "Meetings" / "Board"
    assert projects.folder_to_open("type", p.id, None) == p.path  # no subfolder: the project

    inbox_session = make_session("2026-11-20T10:00:00", LINES)
    assert projects.folder_to_open("meeting", inbox_session.dir.name) == inbox_session.dir
    m = projects.create_meeting(p, inbox_session.dir, "Lobby days", "Board")
    assert projects.folder_to_open("meeting", inbox_session.dir.name) == m.folder

    for kind, ref, mtype in [
        ("type", p.id, "Nope"), ("project", "0" * 12, None), ("project", "../..", None),
        ("project", None, None), ("project", str(tmp_path), None), ("meeting", "..", None),
        ("meeting", "no-such-session", None), ("meeting", str(m.folder), None),
        ("bogus", p.id, None), ("", p.id, None),
    ]:
        assert projects.folder_to_open(kind, ref, mtype) is None, (kind, ref, mtype)

    # a subfolder that would leave the project is refused even if the registry says so
    escaping = projects.Project(p.id, p.name, p.path,
                                (projects.MeetingType("Out", "../elsewhere"),))
    monkeypatch.setattr(projects, "get_project", lambda pid: escaping)
    assert projects.folder_to_open("type", p.id, "Out") is None
