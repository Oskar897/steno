"""``steno new --since``: the stateless "only what is new" mode (position travels in a token)."""

from __future__ import annotations

from datetime import datetime

import pytest

from steno import cli, projects, transcript
from steno.transcript import SessionWriter, TokenError


def make(texts=("hej alla", "nu börjar vi"), *, at=datetime(2026, 1, 1, 10, 0, 0)):
    w = SessionWriter.create(model="fake", sources=["mic"], now=at)
    for i, text in enumerate(texts):
        add(w, text, i * 5.0)
    return w


def add(w, text, t):
    w.append(t_start=t, t_end=t + 2.0, source="mic", speaker="Me", text=text, lang="sv")


def revise(w, seq, text):
    w.append_revisions([(seq, text, {})], model="m")


def run(capsys, *argv):
    code = cli.main(["new", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def token_of(out: str) -> str:
    last = out.rstrip("\n").splitlines()[-1]
    assert last.startswith("next: ")
    return last.removeprefix("next: ")


def test_token_round_trip_and_validation():
    t = transcript.format_token("20261121-195539", 42, 7)
    assert t == "20261121-195539:42:7"
    assert transcript.parse_token(t) == ("20261121-195539", 42, 7)
    bad_tokens = ["", "start", "abc", "a:b:c", "x:1", "x:1:2:3", "../x:1:2", "x:-1:2", "x:1:",
                  ":1:2"]
    for bad in bad_tokens:
        with pytest.raises(TokenError):
            transcript.parse_token(bad)


def test_since_start_gives_header_all_lines_and_a_token(capsys):
    w = make()
    code, out, _ = run(capsys, "--since", "start")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "# Meeting: Meeting"
    assert "state: recording live" in lines[1] and "2026-01-01 10:00" in lines[1]
    assert f"session: {w.session_id}" in out
    assert "**[00:00] Me:** hej alla" in out and "**[00:05] Me:** nu börjar vi" in out
    assert token_of(out) == f"{w.session_id}:2:0"
    w.close()


def test_since_token_gives_only_new_lines(capsys):
    w = make()
    _, out, _ = run(capsys, "--since", "start")
    tok = token_of(out)
    _, out, _ = run(capsys, "--since", tok)
    assert "(nothing new)" in out and "hej alla" not in out
    assert token_of(out) == tok  # unchanged: safe to repeat

    add(w, "tack för idag", 20.0)
    _, out, _ = run(capsys, "--since", tok)
    assert "tack för idag" in out and "hej alla" not in out and "nu börjar" not in out
    assert token_of(out) == f"{w.session_id}:3:0"
    # the same token gives the same answer: nothing was stored
    _, again, _ = run(capsys, "--since", tok)
    assert again == out
    w.close()


def test_corrections_after_a_revision_and_new_token_moves_rev(capsys):
    w = make(("hej alla", "nu börjar vi"))
    _, out, _ = run(capsys, "--since", "start")
    tok = token_of(out)
    revise(w, 2, "nu börjar vi mötet")
    add(w, "sista punkten", 30.0)
    _, out, _ = run(capsys, "--since", tok)
    assert out.index("sista punkten") < out.index("Corrections") < out.index("nu börjar vi mötet")
    assert "hej alla" not in out
    tok2 = token_of(out)
    assert tok2 == f"{w.session_id}:3:1"
    _, out, _ = run(capsys, "--since", tok2)
    assert "(nothing new)" in out and "Corrections" not in out
    w.close()


def test_a_revision_that_only_changes_punctuation_is_not_a_correction(capsys):
    w = make(("hej alla",))
    tok = token_of(run(capsys, "--since", "start")[1])
    revise(w, 1, "Hej, alla.")
    _, out, _ = run(capsys, "--since", tok)
    assert "Corrections" not in out and "(nothing new)" in out
    assert token_of(out) == f"{w.session_id}:1:1"  # the revision is consumed silently
    w.close()


def test_a_new_meeting_is_announced_and_delivered_from_its_start(capsys):
    old = make(("gammalt", "möte"), at=datetime(2026, 1, 1, 10, 0, 0))
    old.close()
    tok = token_of(run(capsys, "--since", "start")[1])
    assert tok.startswith(old.session_id)

    new = make(("nytt möte", "börjar"), at=datetime(2026, 1, 2, 9, 0, 0))
    _, out, _ = run(capsys, "--since", tok)
    assert f"session: {new.session_id}" in out
    assert f"your token was for meeting {old.session_id}" in out and "different meeting" in out
    assert "nytt möte" in out and "börjar" in out and "gammalt" not in out
    assert token_of(out) == f"{new.session_id}:2:0"
    new.close()


def test_since_writes_no_cursor_files(capsys):
    w = make()
    revise(w, 1, "hej alla där")
    _, out, _ = run(capsys, "--since", "start")
    run(capsys, "--since", token_of(out))
    assert list((w.dir / "cursors").iterdir()) == []
    w.close()


def test_since_does_not_disturb_the_stored_consumer_cursor(capsys):
    w = make()
    run(capsys, "--since", "start")
    assert cli.main(["new"]) == 0
    assert "hej alla" in capsys.readouterr().out  # the consumer cursor still starts at 0
    assert transcript.read_cursor(w.dir, "claude") == 2
    w.close()


def test_malformed_token_is_a_clear_error(capsys):
    make().close()
    code, out, err = run(capsys, "--since", "garbage")
    assert code == 2 and out == ""
    assert "not a valid token" in err and "next:" in err


def test_since_cannot_be_combined_with_consumer_or_peek(capsys):
    make().close()
    assert run(capsys, "--since", "start", "--consumer", "x")[0] == 2
    assert run(capsys, "--since", "start", "--peek")[0] == 2


def test_no_meetings_says_so_and_offers_start(capsys):
    code, out, _ = run(capsys, "--since", "start")
    assert code == 0 and "no meeting found" in out and token_of(out) == "start"


def test_explicit_session_and_finished_state(capsys):
    a = make(at=datetime(2026, 1, 1, 10, 0, 0))
    a.close()
    b = make(("annat",), at=datetime(2026, 1, 2, 10, 0, 0))
    b.close()
    _, out, _ = run(capsys, "--since", "start", "--session", a.session_id)
    assert "hej alla" in out and "annat" not in out and "state: finished" in out
    assert run(capsys, "--since", "start", "--session", "nope")[1].startswith("(no meeting")


def test_project_scopes_latest_and_header_names_project_type_and_title(tmp_path, capsys,
                                                                       monkeypatch):
    alpha_dir, beta_dir = tmp_path / "Alpha", tmp_path / "Beta"
    boards = alpha_dir / "Boards"
    boards.mkdir(parents=True)
    beta_dir.mkdir()
    a = projects.create_project("Alpha", alpha_dir)
    a = projects.add_type(a.id, "Board", boards)
    b = projects.create_project("Beta", beta_dir)
    wa = make(("alpha text",), at=datetime(2026, 1, 1, 10, 0, 0))
    projects.create_meeting(a, wa.dir, "Kickoff", mtype="Board")
    wb = make(("beta text",), at=datetime(2026, 1, 2, 10, 0, 0))  # globally the latest
    projects.create_meeting(b, wb.dir, "Retro")  # no subfolder: kept in Steno only

    monkeypatch.chdir(a.path)
    _, out, _ = run(capsys, "--since", "start")
    assert "alpha text" in out and "beta text" not in out
    assert "# Meeting: Kickoff" in out and "project: Alpha | type: Board" in out

    monkeypatch.chdir(boards)  # inside a subfolder's folder counts as the project too
    assert "alpha text" in run(capsys, "--since", "start")[1]

    monkeypatch.chdir(tmp_path)  # outside any project: globally latest
    assert "beta text" in run(capsys, "--since", "start")[1]
    _, out, _ = run(capsys, "--since", "start", "--project", str(b.path))
    assert "beta text" in out and "project: Beta" in out
    code, _, err = run(capsys, "--since", "start", "--project", str(tmp_path))
    assert code == 1 and "not inside a Steno project" in err
    wa.close()
    wb.close()


def test_a_subfolder_outside_the_project_folder_scopes_to_the_project(tmp_path, capsys,
                                                                       monkeypatch):
    drive = tmp_path / "drive"  # e.g. a Google Drive folder: not inside any project folder
    drive.mkdir()
    p = projects.create_project("Club")  # a label without a linked folder
    p = projects.add_type(p.id, "Board", drive)
    w = make(("drive text",), at=datetime(2026, 1, 1, 10, 0, 0))
    projects.create_meeting(p, w.dir, "Lobby", mtype="Board")
    other = make(("other text",), at=datetime(2026, 1, 2, 10, 0, 0))  # globally the latest

    monkeypatch.chdir(drive)
    _, out, _ = run(capsys, "--since", "start")
    assert "drive text" in out and "other text" not in out and "project: Club" in out
    w.close()
    other.close()


def test_project_without_meetings_does_not_fall_back_to_another_project(tmp_path, capsys,
                                                                        monkeypatch):
    a = projects.add_project(tmp_path, name="Alpha", create=True)
    other = make()
    other.close()
    monkeypatch.chdir(a.path)
    _, out, _ = run(capsys, "--since", "start")
    assert "no meeting found in project Alpha" in out and token_of(out) == "start"


def test_finished_meeting_points_to_its_summary(tmp_path, capsys, monkeypatch):
    root = tmp_path / "Proj"
    meetings = root / "Meetings"
    meetings.mkdir(parents=True)
    p = projects.create_project("Proj", root)
    p = projects.add_type(p.id, "Weekly", meetings)
    w = make()
    w.close()
    m = projects.create_meeting(p, w.dir, "Planning", mtype="Weekly")
    (w.dir / "summary.md").write_text("# s\n", encoding="utf-8")
    projects.sync(w.dir)
    monkeypatch.chdir(p.path)
    _, out, _ = run(capsys, "--since", "start")
    assert f"has summary.md in Meetings/{m.folder.name}/" in out
    _, out2, _ = run(capsys, "--since", token_of(out))
    assert "has summary.md" not in out2  # only on the first delivery


def test_a_meeting_kept_in_steno_has_no_folder_to_point_to(tmp_path, capsys, monkeypatch):
    p = projects.create_project("Proj", tmp_path)
    w = make()
    w.close()
    projects.create_meeting(p, w.dir, "Planning")  # no subfolder: no files, no summary hint
    (w.dir / "summary.md").write_text("# s\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _, out, _ = run(capsys, "--since", "start")
    assert "project: Proj" in out and "has summary.md" not in out


def test_a_folder_meeting_of_a_project_without_a_folder_gets_its_hint(tmp_path, capsys,
                                                                       monkeypatch):
    drive = tmp_path / "drive"
    drive.mkdir()
    p = projects.create_project("Club")  # a label only: no linked folder
    p = projects.add_type(p.id, "Board", drive)
    w = make()
    w.close()
    m = projects.create_meeting(p, w.dir, "Lobby", mtype="Board")
    (w.dir / "summary.md").write_text("# s\n", encoding="utf-8")
    projects.sync(w.dir)
    code, out, _ = run(capsys, "--since", "start", "--session", w.session_id)
    assert code == 0 and f"has summary.md in {m.folder.name}/" in out
