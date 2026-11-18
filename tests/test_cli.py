from __future__ import annotations

from datetime import datetime

from steno import cli, transcript
from steno.transcript import SessionWriter


def make_session():
    w = SessionWriter.create(model="fake", sources=["mic"], now=datetime(2026, 1, 1, 10, 0, 0))
    w.append(t_start=1, t_end=2, source="mic", speaker="Me", text="hej", lang="sv")
    w.append(t_start=3, t_end=4, source="loopback", speaker="Remote", text="tjena", lang="sv")
    return w


def test_new_prints_only_unseen_and_advances(capsys):
    w = make_session()
    assert cli.main(["new"]) == 0
    assert capsys.readouterr().out == "**[00:01] Me:** hej\n**[00:03] Remote:** tjena\n"
    assert cli.main(["new"]) == 0
    assert capsys.readouterr().out == ""  # nothing new

    w.append(t_start=9, t_end=10, source="mic", speaker="Me", text="mer", lang="sv")
    assert cli.main(["new", "--peek"]) == 0
    assert capsys.readouterr().out == "**[00:09] Me:** mer\n"
    assert cli.main(["new"]) == 0  # peek did not advance
    assert capsys.readouterr().out == "**[00:09] Me:** mer\n"
    w.close()


def test_new_with_explicit_session_and_consumer(capsys):
    w = make_session()
    w.close()
    assert cli.main(["new", "--session", w.session_id, "--consumer", "other"]) == 0
    assert "hej" in capsys.readouterr().out
    assert transcript.read_cursor(w.dir, "other") == 2
    assert transcript.read_cursor(w.dir, "claude") == 0


def test_new_without_sessions_is_quiet_on_stdout(capsys):
    assert cli.main(["new"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no such session" in captured.err


def test_sessions_listing(capsys):
    w = make_session()
    w.close()
    assert cli.main(["sessions"]) == 0
    out = capsys.readouterr().out
    assert w.session_id in out and "2 segments" in out and "ended" in out


def test_record_with_nothing_enabled_fails(capsys):
    assert cli.main(["record", "--no-mic", "--no-loopback"]) == 2


def test_bad_config_reports_error(tmp_path, capsys):
    bad = tmp_path / "c.toml"
    bad.write_text("[asr]\ncpu_threads = 'x'\n", encoding="utf-8")
    assert cli.main(["--config", str(bad), "record"]) == 2
    assert "config error" in capsys.readouterr().err
