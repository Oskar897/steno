"""Projects as labels, subfolders as any folder, folderless meetings, Save to... and the API
around them (fake sessions and folders under tmp_path only)."""

from __future__ import annotations

import json
import re
import shutil
import time

import pytest

from steno import paths, projects

from .test_ui import LINES, burst_audio, fake_engine_factory, make_session, make_ui, wait_for

__all__ = ["make_ui"]  # fixture re-export


def _dir(tmp_path, *parts):
    d = tmp_path.joinpath(*parts)
    d.mkdir(parents=True)
    return d


def _club(tmp_path, name="Club", sub="Board", folder="drive"):
    """A project (a label) with one subfolder that is an existing folder."""
    drive = _dir(tmp_path, folder)
    p = projects.add_type(projects.create_project(name).id, sub, drive)
    return p, drive


def _link(session_dir):
    return json.loads((session_dir / projects.LINK_NAME).read_text(encoding="utf-8"))


def _names(folder):
    return sorted(f.name for f in folder.iterdir())


# --------------------------------------------------------------------------- the project


def test_a_project_is_a_label_that_writes_no_files(tmp_path):
    p = projects.create_project("Chess Club")
    assert re.fullmatch(r"[0-9a-f]{12}", p.id) and p.path is None and p.types == ()
    assert p.steno_dir is None and not p.archived
    assert [f.name for f in tmp_path.iterdir()] == ["data"]  # only Steno's own registry
    assert [f.name for f in (tmp_path / "data").iterdir()] == [projects.REGISTRY_NAME]
    assert projects.create_project("Other").id != p.id

    with pytest.raises(projects.ProjectError, match="already a project"):
        projects.create_project("chess club")  # names are unique, case-insensitive
    with pytest.raises(projects.ProjectError, match="name"):
        projects.create_project("  ")
    assert [x.name for x in projects.list_projects()] == ["Chess Club", "Other"]


def test_a_project_can_be_linked_to_an_existing_folder(tmp_path):
    folder = _dir(tmp_path, "thesis")
    p = projects.create_project("Thesis", folder)
    assert p.path == folder.resolve() and p.steno_dir == folder.resolve() / "Steno"
    assert list(folder.iterdir()) == []  # linking writes nothing into it

    with pytest.raises(projects.ProjectError, match="not found"):
        projects.create_project("X", tmp_path / "nope")
    with pytest.raises(projects.ProjectError, match="full path"):
        projects.create_project("X", "relative/dir")
    inside = _dir(tmp_path, "data", "sessions")
    for bad in (paths.data_dir(), inside):
        with pytest.raises(projects.ProjectError, match="own storage"):
            projects.create_project("X", bad)
    assert [x.name for x in projects.list_projects()] == ["Thesis"]


def test_rename_link_unlink_and_archive_a_project(tmp_path):
    folder = _dir(tmp_path, "root")
    p = projects.create_project("Club", folder)
    p = projects.set_types(p.id, [{"name": "Board", "folder": "Meetings/Board"}])
    old_folder = p.type_dir(p.types[0])
    assert old_folder == folder.resolve() / "Meetings" / "Board" and old_folder.is_dir()

    other = projects.create_project("Other")
    with pytest.raises(projects.ProjectError, match="already a project"):
        projects.rename_project(p.id, "other")
    assert projects.rename_project(p.id, "Club 2").name == "Club 2"
    assert folder.is_dir()  # the label changes, no folder is renamed

    p = projects.set_project_folder(p.id, None)  # unlink: relative subfolders stay where they are
    assert p.path is None and p.types[0].folder == str(old_folder)
    p = projects.set_project_folder(p.id, folder)
    assert p.path == folder.resolve()
    assert projects.set_project_archived(other.id, True).archived is True
    assert projects.set_project_archived(other.id, False).archived is False


def test_set_types_takes_relative_folders_in_a_linked_project_and_absolute_ones(tmp_path):
    root = _dir(tmp_path, "root")
    outside = _dir(tmp_path, "outside")
    p = projects.create_project("Club", root)
    p = projects.set_types(p.id, [{"name": "Board", "folder": "Meetings/Board"},
                                  {"name": "Drive", "folder": str(outside)}])
    assert (root / "Meetings" / "Board").is_dir()
    assert p.types[1].folder == str(outside.resolve())

    label = projects.create_project("Label")  # no linked folder: a relative one has no home
    with pytest.raises(projects.ProjectError, match="pick a folder"):
        projects.set_types(label.id, [{"name": "X", "folder": "rel"}])
    with pytest.raises(projects.ProjectError, match="already the subfolder"):
        projects.set_types(label.id, [{"name": "Y", "folder": str(outside)}])


# --------------------------------------------------------------------------- subfolders


def test_add_type_takes_an_existing_folder_or_creates_one(tmp_path):
    p = projects.create_project("Club")
    board = _dir(tmp_path, "Board meetings")
    p = projects.add_type(p.id, None, board)  # named after the folder
    assert [(t.name, t.folder) for t in p.types] == [("Board meetings", str(board.resolve()))]
    p = projects.add_type(p.id, "Dev", _dir(tmp_path, "dev-folder"))
    assert [t.name for t in p.types] == ["Board meetings", "Dev"]

    base = _dir(tmp_path, "base")
    p = projects.add_type(p.id, "External", base, create=True)  # makes base/External
    assert (base / "External").is_dir()
    assert p.types[2].folder == str((base / "External").resolve())
    with pytest.raises(projects.ProjectError, match="name"):
        projects.add_type(p.id, None, base, create=True)
    with pytest.raises(projects.ProjectError, match="already has a subfolder"):
        projects.add_type(p.id, "dev", _dir(tmp_path, "unused"))
    assert len(projects.get_project(p.id).types) == 3


def test_a_folder_belongs_to_one_subfolder_only(tmp_path):
    p, drive = _club(tmp_path)
    other = projects.create_project("Other")
    for call in (
        lambda: projects.add_type(other.id, "Mine", drive),
        lambda: projects.add_type(p.id, "Again", drive),  # also not twice in one project
        lambda: projects.add_type(other.id, drive.name, drive.parent, create=True),
        lambda: projects.set_types(other.id, [{"name": "M", "folder": str(drive)}]),
    ):
        with pytest.raises(projects.ProjectError, match="already the subfolder"):
            call()
    assert projects.get_project(other.id).types == ()


def test_a_subfolder_must_be_a_real_folder_outside_steno_storage(tmp_path):
    p = projects.create_project("Club")
    with pytest.raises(projects.ProjectError, match="not found"):
        projects.add_type(p.id, "Gone", tmp_path / "gone")
    with pytest.raises(projects.ProjectError, match="full path"):
        projects.add_type(p.id, "Rel", "relative")
    inside = _dir(tmp_path, "data", "sessions")
    for bad in (paths.data_dir(), inside):
        with pytest.raises(projects.ProjectError, match="own storage"):
            projects.add_type(p.id, "Data", bad)
    with pytest.raises(projects.ProjectError, match="no such project"):
        projects.add_type("0" * 12, "X", tmp_path)
    assert projects.get_project(p.id).types == ()


def test_rename_type_changes_the_name_only(tmp_path):
    p, drive = _club(tmp_path)
    w = make_session("2026-11-20T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Lobby", "Board")
    p = projects.add_type(p.id, "Dev", _dir(tmp_path, "dev"))

    p = projects.rename_type(p.id, "board", "Styrelse")  # found case-insensitively
    assert [t.name for t in p.types] == ["Styrelse", "Dev"]
    assert p.types[0].folder == str(drive.resolve())
    assert projects.type_of(w.dir) == "Styrelse" and m.folder.is_dir()
    assert [x.session for x in projects.list_meetings(p)] == [w.dir.name]
    with pytest.raises(projects.ProjectError, match="already has a subfolder"):
        projects.rename_type(p.id, "Styrelse", "dev")
    with pytest.raises(projects.ProjectError, match="no subfolder"):
        projects.rename_type(p.id, "Nope", "X")
    with pytest.raises(projects.ProjectError, match="name"):
        projects.rename_type(p.id, "Dev", " ")
    p = projects.rename_type(p.id, "Styrelse", "styrelse")  # only the case changes: fine
    assert p.types[0].name == "styrelse"


def test_set_type_folder_is_refused_while_the_old_folder_holds_meetings(tmp_path):
    p, drive = _club(tmp_path)
    new = _dir(tmp_path, "new")
    w = make_session("2026-11-20T10:00:00", LINES)
    projects.create_meeting(p, w.dir, "Lobby", "Board")
    with pytest.raises(projects.ProjectError, match="still holds meetings"):
        projects.set_type_folder(p.id, "Board", new)
    assert projects.get_project(p.id).types[0].folder == str(drive.resolve())

    projects.delete_meeting(w.dir)
    p = projects.set_type_folder(p.id, "Board", new)
    assert p.types[0].folder == str(new.resolve())

    p = projects.add_type(p.id, "Dev", _dir(tmp_path, "dev"))
    with pytest.raises(projects.ProjectError, match="already the subfolder"):
        projects.set_type_folder(p.id, "Dev", new)
    assert projects.set_type_folder(p.id, "Board", new).types[0].folder == str(new.resolve())
    with pytest.raises(projects.ProjectError, match="not found"):
        projects.set_type_folder(p.id, "Board", tmp_path / "nope")
    with pytest.raises(projects.ProjectError, match="no subfolder"):
        projects.set_type_folder(p.id, "Nope", new)


def test_meetings_follow_a_subfolder_folder_that_was_moved_in_explorer(tmp_path):
    p, drive = _club(tmp_path)
    w = make_session("2026-11-20T10:00:00", LINES)
    projects.create_meeting(p, w.dir, "Lobby", "Board")
    moved = tmp_path / "drive-moved"
    shutil.move(str(drive), str(moved))  # the user moves the folder, then picks it again
    p = projects.set_type_folder(p.id, "Board", moved)
    assert [m.session for m in projects.list_meetings(p)] == [w.dir.name]
    assert projects.project_of(w.dir).id == p.id  # fails: None, the meeting is in the inbox too
    assert projects.inbox_sessions() == []


# --------------------------------------------------------------------------- folderless


def test_a_meeting_without_a_subfolder_is_kept_in_steno_only(tmp_path):
    p = projects.create_project("Club", _dir(tmp_path, "root"))
    w = make_session("2026-11-20T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Plain talk")
    assert m.folder is None and m.title == "Plain talk"
    assert _link(w.dir) == {"title": "Plain talk", "project": p.id}
    assert sorted(f.name for f in tmp_path.iterdir()) == ["data", "root"]
    assert list((tmp_path / "root").iterdir()) == []  # no Steno/ folder, no files
    assert projects.meeting_folder(w.dir) is None and projects.sync(w.dir) is None
    assert projects.project_of(w.dir).id == p.id and projects.type_of(w.dir) is None
    assert projects.inbox_sessions() == []
    assert [x.session for x in projects.list_meetings(p)] == [w.dir.name]
    assert [x.session for x in projects.all_meetings(p)] == [w.dir.name]

    r = projects.rename_meeting(w.dir, "Renamed")  # keeps the project
    assert r.folder is None and projects.meeting_title(w.dir) == "Renamed"
    assert _link(w.dir) == {"title": "Renamed", "project": p.id}
    assert projects.project_of(w.dir).id == p.id
    assert projects.list_meetings(p)[0].title == "Renamed"


def test_a_missing_subfolder_folder_falls_back_to_a_folderless_meeting(tmp_path):
    p, drive = _club(tmp_path)
    shutil.rmtree(drive)  # the drive went offline
    assert not projects.type_available(p, p.types[0])
    w = make_session("2026-11-20T10:00:00", LINES)
    m = projects.create_meeting(p, w.dir, "Lobby", "Board")
    assert m.folder is None and not drive.exists()  # never recreated
    assert _link(w.dir) == {"title": "Lobby", "project": p.id}
    assert projects.project_of(w.dir).id == p.id and projects.type_of(w.dir) is None

    w2 = make_session("2026-11-21T10:00:00", LINES)
    projects.create_meeting(p, w2.dir, "Other")
    with pytest.raises(projects.ProjectError, match="missing"):
        projects.move_meeting(w2.dir, p, "Board")
    assert not drive.exists() and projects.meeting_folder(w2.dir) is None
    assert projects.get_project(p.id).types[0].name == "Board"  # the subfolder is kept


def test_move_between_folderless_and_a_subfolder_keeps_the_users_files(tmp_path):
    p, drive = _club(tmp_path)
    w = make_session("2026-11-20T10:00:00", LINES)
    projects.create_meeting(p, w.dir, "Weekly")
    (w.dir / "summary.md").write_text("# S\n", encoding="utf-8")

    m = projects.move_meeting(w.dir, p, "Board")
    assert m.folder == drive.resolve() / "2026-11-20 Weekly"
    assert _names(m.folder) == [projects.MEETING_FILE, "summary.md", "transcript.md"]
    assert projects.type_of(w.dir) == "Board" and projects.project_of(w.dir).id == p.id
    (m.folder / "agenda.md").write_text("mine", encoding="utf-8")

    back = projects.move_meeting(w.dir, p)  # no subfolder: Steno's files leave
    assert back.folder is None and projects.meeting_folder(w.dir) is None
    assert _names(m.folder) == ["agenda.md"]  # the user's own file keeps the folder
    assert _link(w.dir) == {"title": "Weekly", "project": p.id}
    assert projects.project_of(w.dir).id == p.id and projects.inbox_sessions() == []
    assert projects.type_meetings(p, p.types[0]) == []
    assert (w.dir / "summary.md").read_text(encoding="utf-8") == "# S\n"  # the session has all

    again = projects.move_meeting(w.dir, p, "Board")  # the old name is taken: a sibling
    assert again.folder.name == "2026-11-20 Weekly (2)"
    projects.move_meeting(w.dir, p)
    assert not again.folder.exists()  # nothing of the user's was in it: the folder goes
    assert _names(m.folder) == ["agenda.md"]


# --------------------------------------------------------------------------- Save to...


def test_save_to_an_existing_folder_keeps_its_name_and_files(tmp_path):
    p, drive = _club(tmp_path)
    own = _dir(drive, "2026-11-22 Retro")
    (own / "agenda.md").write_text("mine", encoding="utf-8")
    w = make_session("2026-11-22T10:00:00", LINES)
    projects.rename_meeting(w.dir, "Retrospective")  # an inbox meeting with a title

    project, m = projects.save_to_folder(w.dir, own)
    assert project.id == p.id and m.folder == own.resolve() and m.title == "Retrospective"
    assert _names(own) == [projects.MEETING_FILE, "agenda.md", "transcript.md"]
    assert json.loads((own / projects.MEETING_FILE).read_text(encoding="utf-8"))["own_folder"]
    assert projects.project_of(w.dir).id == p.id and projects.type_of(w.dir) == "Board"
    assert projects.inbox_sessions() == []
    r = projects.rename_meeting(w.dir, "Renamed")
    assert r.folder == own.resolve() and own.is_dir()  # the folder keeps its name

    # a meeting that sits in another project's folder leaves it for the new one
    q, drive2 = _club(tmp_path, "Other", "Dev", "drive2")
    w2 = make_session("2026-11-23T10:00:00", LINES)
    m2 = projects.create_meeting(q, w2.dir, "Elsewhere", "Dev")
    own2 = _dir(drive, "2026-11-23 Other")
    project, m = projects.save_to_folder(w2.dir, own2)
    assert project.id == p.id and not m2.folder.exists() and drive2.is_dir()
    assert projects.project_of(w2.dir).id == p.id and "transcript.md" in _names(own2)


def test_save_to_refuses_folders_that_are_not_meeting_folders_of_a_subfolder(tmp_path):
    p, drive = _club(tmp_path)
    w = make_session("2026-11-22T10:00:00", LINES)
    stray = _dir(tmp_path, "stray", "2026-11-22 Idea")
    with pytest.raises(projects.ProjectError, match="lies directly"):
        projects.save_to_folder(w.dir, stray)

    blocked = _dir(drive, "2026-11-23 Blocked")
    (blocked / "transcript.md").write_text("mine", encoding="utf-8")
    with pytest.raises(projects.ProjectError, match="own transcript.md"):
        projects.save_to_folder(w.dir, blocked)
    assert (blocked / "transcript.md").read_text(encoding="utf-8") == "mine"

    with pytest.raises(projects.ProjectError, match="subfolder itself"):
        projects.save_to_folder(w.dir, drive)
    with pytest.raises(projects.ProjectError, match="not found"):
        projects.save_to_folder(w.dir, tmp_path / "nope")

    own = _dir(drive, "2026-11-24 Taken")
    projects.save_to_folder(w.dir, own)
    w2 = make_session("2026-11-25T10:00:00", LINES)
    with pytest.raises(projects.ProjectError, match="already holds"):
        projects.save_to_folder(w2.dir, own)
    assert projects.meeting_folder(w2.dir) is None and _names(own) == [
        projects.MEETING_FILE, "transcript.md"]


# --------------------------------------------------------------------------- deleting


def test_delete_type_without_meetings_keeps_them_in_the_project_and_the_files(tmp_path):
    p, drive = _club(tmp_path)
    w1 = make_session("2026-11-22T10:00:00", LINES)
    m1 = projects.create_meeting(p, w1.dir, "One", "Board")
    own = _dir(drive, "2026-11-22 Own")
    (own / "agenda.md").write_text("mine", encoding="utf-8")
    w2 = make_session("2026-11-22T11:00:00", LINES)
    projects.create_meeting(p, w2.dir, None, "Board", folder=own)

    assert projects.delete_type(p.id, "Board", meetings=False) == 0
    p = projects.get_project(p.id)
    assert p.types == ()
    assert w1.dir.exists() and w2.dir.exists() and projects.inbox_sessions() == []
    for w, title in ((w1, "One"), (w2, "Own")):
        assert projects.project_of(w.dir).id == p.id and projects.meeting_folder(w.dir) is None
        assert _link(w.dir) == {"title": title, "project": p.id}
    assert sorted(x.session for x in projects.all_meetings(p)) == [w1.dir.name, w2.dir.name]
    assert drive.is_dir() and (m1.folder / "transcript.md").is_file()  # the files stay
    assert not (m1.folder / projects.MEETING_FILE).exists()  # only Steno's marker went
    assert _names(own) == ["agenda.md", "transcript.md"]

    with pytest.raises(projects.ProjectError, match="no subfolder"):
        projects.delete_type(p.id, "Board")


def test_delete_project_without_meetings_sends_them_to_the_inbox(tmp_path):
    p, drive = _club(tmp_path)
    w1 = make_session("2026-11-22T10:00:00", LINES)
    m1 = projects.create_meeting(p, w1.dir, "In a folder", "Board")
    w2 = make_session("2026-11-22T11:00:00", LINES)
    projects.create_meeting(p, w2.dir, "Folderless")
    assert projects.inbox_sessions() == []

    assert projects.delete_project(p.id, meetings=False) == 0
    assert projects.list_projects() == []
    assert sorted(s.id for s in projects.inbox_sessions()) == [w1.dir.name, w2.dir.name]
    assert (m1.folder / "transcript.md").is_file() and w2.dir.exists()  # nothing was deleted
    assert projects.meeting_title(w2.dir) == "Folderless"


def test_delete_project_with_meetings_deletes_folderless_ones_too(tmp_path):
    p, drive = _club(tmp_path)
    own = _dir(drive, "2026-11-22 Own")
    (own / "agenda.md").write_text("mine", encoding="utf-8")
    w1 = make_session("2026-11-22T10:00:00", LINES)
    projects.create_meeting(p, w1.dir, "x", "Board", folder=own)
    w2 = make_session("2026-11-22T11:00:00", LINES)
    projects.create_meeting(p, w2.dir, "Folderless")
    assert projects.delete_project(p.id, meetings=True) == 2
    assert not w1.dir.exists() and not w2.dir.exists() and projects.list_projects() == []
    assert _names(own) == ["agenda.md"] and drive.is_dir()


def _old_project(tmp_path):
    """An older project: a folder with a Steno/ that holds Steno's generated files."""
    p = projects.add_project(_dir(tmp_path, "Old"))
    steno = p.path / "Steno"
    steno.mkdir()
    (steno / "CLAUDE.md").write_text("<!-- steno:generated -->\nold notes for agents\n",
                                     encoding="utf-8")
    (steno / "index.md").write_text("# Meetings in Old\n\n", encoding="utf-8")
    (steno / ".gitignore").write_text(projects._STENO_GITIGNORE, encoding="utf-8")
    return p, steno


def test_deleting_an_older_project_cleans_its_steno_folder(tmp_path):
    p, steno = _old_project(tmp_path)
    (p.path / "README.md").write_text("mine", encoding="utf-8")
    assert projects.delete_project(p.id, meetings=False) == 0
    assert not steno.exists() and _names(p.path) == ["README.md"]


def test_deleting_an_older_project_leaves_the_users_own_files_in_steno(tmp_path):
    p, steno = _old_project(tmp_path)
    (steno / "notes.md").write_text("mine", encoding="utf-8")
    projects.delete_project(p.id, meetings=False)
    assert _names(steno) == ["notes.md"]  # the generated files went, the folder stays

    p, steno = _old_project(_dir(tmp_path, "again"))
    (steno / "CLAUDE.md").write_text("my own CLAUDE.md", encoding="utf-8")  # no Steno marker
    projects.delete_project(p.id, meetings=False)
    assert _names(steno) == ["CLAUDE.md"]
    assert (steno / "CLAUDE.md").read_text(encoding="utf-8") == "my own CLAUDE.md"


def test_an_older_project_with_a_meeting_in_steno_keeps_its_files_until_it_is_deleted(tmp_path):
    p, steno = _old_project(tmp_path)
    w = make_session("2026-11-20T10:00:00", LINES)
    folder = _dir(steno, "2026-11-20 Old meeting")
    (folder / projects.MEETING_FILE).write_text(json.dumps(
        {"session": w.dir.name, "title": "Old meeting"}), encoding="utf-8")
    (w.dir / projects.LINK_NAME).write_text(json.dumps(
        {"folder": str(folder), "title": "Old meeting"}), encoding="utf-8")
    assert projects.project_of(w.dir).id == p.id and projects.type_of(w.dir) is None
    assert [m.session for m in projects.list_meetings(p)] == [w.dir.name]

    projects.delete_project(p.id, meetings=False)  # the meeting still lives there: all stays
    assert (steno / "CLAUDE.md").is_file() and (steno / "index.md").is_file() and folder.is_dir()
    assert [s.id for s in projects.inbox_sessions()] == [w.dir.name]

    p, steno = _old_project(_dir(tmp_path, "second"))
    w2 = make_session("2026-11-21T10:00:00", LINES)
    folder2 = _dir(steno, "2026-11-21 Old meeting")
    (folder2 / projects.MEETING_FILE).write_text(json.dumps(
        {"session": w2.dir.name, "title": "Old meeting"}), encoding="utf-8")
    (w2.dir / projects.LINK_NAME).write_text(json.dumps(
        {"folder": str(folder2), "title": "Old meeting"}), encoding="utf-8")
    assert projects.delete_project(p.id, meetings=True) == 1
    assert not w2.dir.exists() and not steno.exists() and p.path.is_dir()


# --------------------------------------------------------------------------- API


def test_project_endpoints_create_update_and_archive(make_ui, tmp_path):
    _, c = make_ui()
    linked = _dir(tmp_path, "linked")
    assert c.post("/api/projects", {"name": "Club", "folder": str(tmp_path / "nope")})[0] == 400
    assert c.post("/api/projects", {"name": 5})[0] == 400
    assert c.post("/api/projects", {"name": "Club", "folder": 7})[0] == 400
    status, res = c.post("/api/projects", {"name": "Club", "folder": str(linked)})
    assert status == 200
    info = res["project"]
    pid = info["id"]
    assert re.fullmatch(r"[0-9a-f]{12}", pid) and info["name"] == "Club"
    assert info["path"] == str(linked.resolve()) and info["path_missing"] is False
    assert info["archived"] is False and info["types"] == [] and info["last_at"] is None
    assert info["meetings"] == 0 and info["all_meetings"] == 0
    assert c.post("/api/projects", {"name": "club"})[0] == 400  # the name is taken
    label = c.post("/api/projects", {"name": "Label"})[1]["project"]
    assert label["path"] == "" and label["path_missing"] is False

    upd = f"/api/projects/{pid}/update"
    assert c.post(upd, {"name": "Club 2"})[1]["project"]["name"] == "Club 2"
    assert c.post(upd, {"name": " "})[0] == 400
    assert c.post(upd, {"name": "label"})[0] == 400
    assert c.post(upd, {"archived": "yes"})[0] == 400
    assert c.post(upd, {"folder": str(tmp_path / "nope")})[0] == 400
    assert c.post(upd, {"folder": None})[1]["project"]["path"] == ""  # unlinked
    assert c.post(upd, {"folder": str(linked)})[1]["project"]["path"] == str(linked.resolve())
    assert c.post("/api/projects/" + "0" * 12 + "/update", {})[0] == 404
    assert c.post("/api/projects/zzz/update", {})[0] == 404

    done = c.post(upd, {"archived": True})[1]["project"]
    assert done["archived"] is True and done["name"] == "Club 2"
    assert {p["id"]: p["archived"] for p in c.get("/api/projects")[1]["projects"]} == {
        pid: True, label["id"]: False}
    assert c.post(upd, {"archived": False})[1]["project"]["archived"] is False

    linked.rmdir()  # the linked folder is gone: reported, the project stays
    listed = {p["id"]: p for p in c.get("/api/projects")[1]["projects"]}
    assert listed[pid]["path_missing"] is True


def test_subfolder_endpoints(make_ui, tmp_path):
    _, c = make_ui()
    pid = c.post("/api/projects", {"name": "Club"})[1]["project"]["id"]
    other = c.post("/api/projects", {"name": "Other"})[1]["project"]["id"]
    base = _dir(tmp_path, "drive")
    sub = f"/api/projects/{pid}/subfolders"

    assert c.post(sub, {})[0] == 400  # no folder
    assert c.post(sub, {"folder": 3})[0] == 400
    assert c.post(sub, {"folder": str(base), "create": "yes"})[0] == 400
    assert c.post(sub, {"folder": str(base), "create": True})[0] == 400  # a new one needs a name
    assert c.post(sub, {"folder": str(tmp_path / "nope")})[0] == 400
    assert c.post("/api/projects/" + "0" * 12 + "/subfolders", {"folder": str(base)})[0] == 404

    status, res = c.post(sub, {"folder": str(base), "name": "Board", "create": True})
    assert status == 200 and (base / "Board").is_dir()
    assert res["project"]["types"] == [{
        "name": "Board", "folder": str((base / "Board").resolve()), "missing": False,
        "meetings": 0}]
    existing = _dir(tmp_path, "Dev folder")
    assert c.post(sub, {"folder": str(existing)})[1]["project"]["types"][1]["name"] == "Dev folder"
    assert c.post(sub, {"folder": str(existing), "name": "Again"})[0] == 400  # folder taken
    assert c.post(f"/api/projects/{other}/subfolders", {"folder": str(existing)})[0] == 400
    assert c.post(sub, {"folder": str(_dir(tmp_path, "x")), "name": "board"})[0] == 400

    ren = f"{sub}/rename"
    status, res = c.post(ren, {"name": "board", "new_name": "Styrelse"})
    assert status == 200 and [t["name"] for t in res["project"]["types"]] == [
        "Styrelse", "Dev folder"]
    assert c.post(ren, {"name": "Nope", "new_name": "X"})[0] == 400
    assert c.post(ren, {"name": "Styrelse"})[0] == 400
    assert c.post(ren, {"new_name": "X"})[0] == 400
    assert c.post(ren, {"name": "Styrelse", "new_name": "dev folder"})[0] == 400

    chg = f"{sub}/folder"
    moved = _dir(tmp_path, "moved")
    status, res = c.post(chg, {"name": "Styrelse", "folder": str(moved)})
    assert status == 200 and res["project"]["types"][0]["folder"] == str(moved.resolve())
    assert c.post(chg, {})[0] == 400
    assert c.post(chg, {"name": "Styrelse"})[0] == 400
    assert c.post(chg, {"name": "Nope", "folder": str(moved)})[0] == 400
    assert c.post(chg, {"name": "Styrelse", "folder": str(tmp_path / "nope")})[0] == 400
    w = make_session("2026-11-22T10:00:00", LINES)
    projects.create_meeting(projects.get_project(pid), w.dir, "Weekly", "Styrelse")
    status, res = c.post(chg, {"name": "Styrelse", "folder": str(base)})
    assert status == 400 and "meetings" in res["error"]
    listed = c.get("/api/projects")[1]["projects"][0]
    assert listed["types"][0]["meetings"] == 1 and listed["meetings"] == 1

    td = f"/api/projects/{pid}/types/delete"
    assert c.post(td, {})[0] == 400
    assert c.post(td, {"name": 3})[0] == 400
    assert c.post(td, {"name": "Nope"})[0] == 404
    assert c.post(td, {"name": "Styrelse", "meetings": "no"})[0] == 400
    status, res = c.post(td, {"name": "Styrelse", "meetings": False})  # the meeting stays
    assert status == 200 and res["deleted"] == 0
    kept = c.get(f"/api/projects/{pid}/meetings")[1]["meetings"]
    assert [(m["session"], m["folder"], m["type"]) for m in kept] == [(w.dir.name, "", None)]
    assert (moved / "2026-11-22 Weekly" / "transcript.md").is_file()  # the folder keeps its files
    info = c.get("/api/projects")[1]["projects"][0]
    assert [t["name"] for t in info["types"]] == ["Dev folder"] and info["all_meetings"] == 1
    assert c.post(td, {"name": "Dev folder"})[1]["deleted"] == 0  # meetings default: true
    assert existing.is_dir()  # the folder itself is never deleted


def test_save_endpoint_and_the_session_folder(make_ui, tmp_path):
    _, c = make_ui()
    pid = c.post("/api/projects", {"name": "Club"})[1]["project"]["id"]
    drive = _dir(tmp_path, "drive")
    assert c.post(f"/api/projects/{pid}/subfolders", {"folder": str(drive), "name": "Board"})[0] \
        == 200
    own = _dir(drive, "2026-11-22 Retro")
    (own / "agenda.md").write_text("mine", encoding="utf-8")
    w = make_session("2026-11-22T10:00:00", LINES)
    sid = w.dir.name
    assert c.get(f"/api/sessions/{sid}")[1]["folder"] == ""

    sv = f"/api/meetings/{sid}/save"
    assert c.post(sv, {})[0] == 400
    assert c.post(sv, {"folder": str(tmp_path / "nope")})[0] == 400
    assert c.post(sv, {"folder": str(_dir(tmp_path, "stray"))})[0] == 400
    assert c.post("/api/meetings/nosuch/save", {"folder": str(own)})[0] == 404
    status, res = c.post(sv, {"folder": str(own)})
    assert status == 200 and res["folder"] == str(own.resolve())
    assert res["project"] == pid and res["type"] == "Board"
    assert (own / "transcript.md").is_file() and (own / "agenda.md").is_file()
    session = c.get(f"/api/sessions/{sid}")[1]
    assert session["folder"] == str(own.resolve()) and session["project"] == pid
    listed = c.get(f"/api/projects/{pid}/meetings")[1]["meetings"]
    assert [m["folder"] for m in listed] == [str(own.resolve())]


def test_recording_into_a_missing_subfolder_falls_back_with_a_notice(make_ui, tmp_path):
    _, c = make_ui(engine_factory=fake_engine_factory(burst_audio()))
    p, drive = _club(tmp_path)
    shutil.rmtree(drive)
    info = c.get("/api/projects")[1]["projects"][0]
    assert info["types"][0]["missing"] is True and info["path_missing"] is False

    body = {"mic": True, "loopback": False, "project": p.id, "type": "Board"}
    assert c.post("/api/record/start", {**body, "title": "Weekly"})[0] == 200
    rec = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "recording"
                   and r)
    assert rec["type"] == "Board" and "missing" in rec["notice"]
    sid = rec["session"]
    assert c.post(f"/api/meetings/{sid}/save", {"folder": str(drive)})[0] == 409  # recording
    time.sleep(0.3)
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")

    meetings = c.get(f"/api/projects/{p.id}/meetings")[1]["meetings"]
    assert [(m["title"], m["folder"], m["type"]) for m in meetings] == [("Weekly", "", None)]
    assert not drive.exists()  # the folder was not recreated
    assert c.get(f"/api/sessions/{sid}")[1]["project"] == p.id

    drive.mkdir()  # the drive is back: Save to... an existing folder in it
    own = _dir(drive, "2026-11-22 Weekly")
    status, res = c.post(f"/api/meetings/{sid}/save", {"folder": str(own)})
    assert status == 200 and (own / "transcript.md").is_file() and res["type"] == "Board"

    assert c.post("/api/record/start", {**body, "title": "Second"})[0] == 200
    rec = wait_for(lambda: (r := c.get("/api/status")[1]["recorder"])["state"] == "recording"
                   and r)
    assert rec["notice"] is None  # the folder is there: no notice
    time.sleep(0.3)
    c.post("/api/record/stop")
    wait_for(lambda: c.get("/api/status")[1]["recorder"]["state"] == "idle")
    folders = {m["title"]: m["folder"] for m in c.get(
        f"/api/projects/{p.id}/meetings")[1]["meetings"]}
    assert folders["Second"].startswith(str(drive.resolve()))
