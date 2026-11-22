"""Recording into an existing meeting folder, and deleting meetings, subfolders and projects
without ever touching the user's own files (fake sessions and folders under tmp_path only)."""

from __future__ import annotations

import json
import os
import time

import pytest

from steno import projects

from .test_ui import LINES, burst_audio, fake_engine_factory, make_session, make_ui, wait_for

__all__ = ["make_ui"]  # fixture re-export


def _club(tmp_path):
    """A project linked to a folder, with two subfolders that are existing folders."""
    root = tmp_path / "Club"
    root.mkdir()
    p = projects.create_project("Club", root)
    for name in ("1-1", "Board"):
        folder = root / "Meetings" / name
        folder.mkdir(parents=True)
        p = projects.add_type(p.id, name, folder)
    return p


def _own_meeting_folder(p, name="2026-11-22 1-1 Alex"):
    folder = p.path / "Meetings" / "1-1" / name
    folder.mkdir(parents=True)
    (folder / "01 Agenda.md").write_text("# Agenda\n", encoding="utf-8")
    (folder / "02 Notes.md").write_text("my notes\n", encoding="utf-8")
    return folder


def test_record_into_an_existing_folder_keeps_the_users_files(tmp_path):
    p = _club(tmp_path)
    folder = _own_meeting_folder(p)
    kind, title, path = projects.check_existing_folder(p, folder)
    assert kind.name == "1-1" and title == "1-1 Alex" and path == folder.resolve()

    w = make_session("2026-11-22T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, folder=folder)
    assert m.folder == folder.resolve() and m.title == "1-1 Alex"
    assert projects.type_of(w.dir) == "1-1"
    assert sorted(f.name for f in folder.iterdir()) == [
        projects.MEETING_FILE, "01 Agenda.md", "02 Notes.md", "transcript.md"]
    assert projects.meeting_title(w.dir) == "1-1 Alex"  # never "Meeting": no auto title
    assert [x.session for x in projects.list_meetings(p)] == [w.dir.name]

    # a second recording cannot take the same folder
    w2 = make_session("2026-11-22T11:00:00", LINES)
    with pytest.raises(projects.ProjectError, match="already holds"):
        projects.create_meeting(p, w2.dir, folder=folder)

    projects.delete_meeting(w.dir)
    assert not w.dir.exists()
    assert sorted(f.name for f in folder.iterdir()) == ["01 Agenda.md", "02 Notes.md"]


def test_an_existing_folder_must_be_a_meeting_folder_of_the_project(tmp_path):
    p = _club(tmp_path)
    with pytest.raises(projects.ProjectError, match="not found"):
        projects.check_existing_folder(p, tmp_path / "nope")
    with pytest.raises(projects.ProjectError, match="subfolder itself"):
        projects.check_existing_folder(p, p.path / "Meetings" / "1-1")
    (p.path / "Steno").mkdir()  # an older project's Steno/ is a place for meetings too
    with pytest.raises(projects.ProjectError, match="subfolder itself"):
        projects.check_existing_folder(p, p.path / "Steno")
    elsewhere = p.path / "Notes" / "2026-11-22 Idea"
    elsewhere.mkdir(parents=True)
    with pytest.raises(projects.ProjectError, match="subfolders"):
        projects.check_existing_folder(p, elsewhere)
    clash = _own_meeting_folder(p, "2026-11-22 Clash")
    (clash / "summary.md").write_text("mine\n", encoding="utf-8")
    with pytest.raises(projects.ProjectError, match="own summary.md"):
        projects.check_existing_folder(p, clash)
    assert (clash / "summary.md").read_text(encoding="utf-8") == "mine\n"


def test_a_hand_made_folder_with_the_same_name_is_used_instead_of_a_copy(tmp_path):
    p = _club(tmp_path)
    folder = _own_meeting_folder(p, "2026-11-22 Weekly")
    w = make_session("2026-11-22T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Weekly", "1-1")
    assert m.folder == folder  # not "2026-11-22 Weekly (2)"
    assert (folder / "01 Agenda.md").is_file() and (folder / "transcript.md").is_file()
    # a folder that is already a meeting gets a sibling as before
    w2 = make_session("2026-11-22T11:00:00", LINES)
    m2 = projects.create_meeting(p, w2.dir, "Weekly", "1-1")
    assert m2.folder.name == "2026-11-22 Weekly (2)"


@pytest.mark.skipif(os.name != "nt", reason="hidden files are a Windows attribute")
def test_the_meeting_marker_is_hidden_and_can_be_rewritten(tmp_path):
    import ctypes

    p = _club(tmp_path)
    w = make_session("2026-11-22T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Weekly", "1-1")
    marker = m.folder / projects.MEETING_FILE
    assert ctypes.windll.kernel32.GetFileAttributesW(str(marker)) & 0x2
    m2 = projects.rename_meeting(w.dir, "Renamed")  # rewrites the hidden marker
    assert json.loads((m2.folder / projects.MEETING_FILE).read_text())["title"] == "Renamed"


def test_delete_a_subfolder_deletes_its_meetings_but_not_the_users_files(tmp_path):
    p = _club(tmp_path)
    own = _own_meeting_folder(p)
    w1 = make_session("2026-11-22T10:00:00", LINES)
    projects.create_meeting(p, w1.dir, folder=own)
    w2 = make_session("2026-11-22T11:00:00", LINES)
    m2 = projects.create_meeting(p, w2.dir, "Plain", "1-1")
    w3 = make_session("2026-11-22T12:00:00", LINES)
    m3 = projects.create_meeting(p, w3.dir, "Board meeting", "Board")

    assert projects.delete_type(p.id, "1-1") == 2
    assert not w1.dir.exists() and not w2.dir.exists() and not m2.folder.exists()
    assert sorted(f.name for f in own.iterdir()) == ["01 Agenda.md", "02 Notes.md"]
    assert (p.path / "Meetings" / "1-1").is_dir()  # kept: it still holds the user's folder
    assert [t.name for t in projects.get_project(p.id).types] == ["Board"]
    assert w3.dir.exists() and m3.folder.exists()  # other subfolders untouched


def test_delete_a_project_with_its_meetings_leaves_the_project_folder(tmp_path):
    p = _club(tmp_path)
    own = _own_meeting_folder(p)
    w1 = make_session("2026-11-22T10:00:00", LINES)
    projects.create_meeting(p, w1.dir, folder=own)
    w2 = make_session("2026-11-22T11:00:00", LINES)
    projects.create_meeting(p, w2.dir, "Root meeting")
    w3 = make_session("2026-11-22T12:00:00", LINES)
    projects.create_meeting(p, w3.dir, "Board meeting", "Board")
    (p.path / "README.md").write_text("project file\n", encoding="utf-8")

    assert projects.delete_project(p.id, meetings=True) == 3
    assert projects.list_projects() == []
    assert not any(w.dir.exists() for w in (w1, w2, w3))
    assert p.path.is_dir() and (p.path / "README.md").is_file()
    assert sorted(f.name for f in own.iterdir()) == ["01 Agenda.md", "02 Notes.md"]
    assert not (p.path / "Steno").exists()  # only Steno's own files were in it
    assert (p.path / "Meetings" / "Board").is_dir()  # a picked folder is never deleted
    assert list((p.path / "Meetings" / "Board").iterdir()) == []  # only Steno's files went


def test_forget_a_project_without_meetings_deletes_nothing(tmp_path):
    p = _club(tmp_path)
    w = make_session("2026-11-22T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Weekly", "1-1")
    assert projects.delete_project(p.id, meetings=False) == 0
    assert projects.list_projects() == [] and w.dir.exists() and m.folder.exists()


def test_existing_folder_and_delete_api(make_ui, tmp_path):
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    pid = c.post("/api/projects", {"path": str(tmp_path), "name": "Club", "create": True})[1][
        "project"]["id"]
    assert c.post(f"/api/projects/{pid}/types", {"types": [{"name": "1-1"}]})[0] == 200
    p = projects.get_project(pid)
    own = _own_meeting_folder(p)

    status, res = c.post(f"/api/projects/{pid}/folder", {"folder": str(own)})
    assert status == 200 and res["type"] == "1-1" and res["title"] == "1-1 Alex"
    assert c.post(f"/api/projects/{pid}/folder", {"folder": str(tmp_path)})[0] == 400

    c.post("/api/record/start", {"mic": True, "loopback": False, "project": pid,
                                 "folder": str(own), "auto_title": True})
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "recording")
    assert c.post(f"/api/projects/{pid}/types/delete", {"name": "1-1"})[0] == 409  # recording
    assert c.post(f"/api/projects/{pid}/delete", {"meetings": True})[0] == 409
    time.sleep(0.3)
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")
    meetings = c.get(f"/api/projects/{pid}/meetings")[1]["meetings"]
    assert len(meetings) == 1 and meetings[0]["title"] == "1-1 Alex"
    assert (own / "transcript.md").is_file() and (own / "01 Agenda.md").is_file()

    status, res = c.post(f"/api/projects/{pid}/types/delete", {"name": "1-1"})
    assert status == 200 and res["deleted"] == 1
    assert sorted(f.name for f in own.iterdir()) == ["01 Agenda.md", "02 Notes.md"]
    status, res = c.post(f"/api/projects/{pid}/delete", {"meetings": True})
    assert status == 200 and c.get("/api/projects")[1]["projects"] == []


def test_delete_all_inbox_meetings_api(make_ui, tmp_path):
    _, c = make_ui()
    w1 = make_session("2026-11-22T10:00:00", LINES)
    w2 = make_session("2026-11-22T11:00:00", LINES)
    projects.set_archived(w2.dir, True)  # archived: hidden in the app, so it stays
    status, res = c.post("/api/inbox/delete", {})
    assert status == 200 and res["deleted"] == 1
    assert not w1.dir.exists() and w2.dir.exists()


def test_rename_and_move_keep_the_name_of_the_users_own_folder(tmp_path):
    p = _club(tmp_path)
    own = _own_meeting_folder(p, "Q4 prep")
    w = make_session("2026-11-22T10:00:00", LINES)
    projects.create_meeting(p, w.dir, folder=own)
    m = projects.rename_meeting(w.dir, "Quarterly")
    assert m.folder == own.resolve() and projects.meeting_title(w.dir) == "Quarterly"
    m = projects.move_meeting(w.dir, p, "Board")
    assert m.folder.name == "Q4 prep" and m.folder.parent.name == "Board"
    assert (m.folder / "01 Agenda.md").is_file()
    projects.move_to_inbox(w.dir)  # only Steno's files leave; the user's folder stays
    assert sorted(f.name for f in m.folder.iterdir()) == ["01 Agenda.md", "02 Notes.md"]
