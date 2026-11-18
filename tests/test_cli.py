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


def test_show_prints_live_and_final_transcripts(capsys):
    from dataclasses import replace

    w = make_session()
    w.close()
    assert cli.main(["show"]) == 0
    assert "**[00:01] Me:** hej" in capsys.readouterr().out
    assert cli.main(["show", "--final"]) == 1  # not finalized yet
    assert "steno finalize" in capsys.readouterr().err
    final = replace(transcript.read_segments(w.dir)[0], speaker="Alice", final=True)
    (w.dir / "final.jsonl").write_text(final.to_json() + "\n", encoding="utf-8")
    assert cli.main(["show", w.session_id, "--final"]) == 0
    out = capsys.readouterr().out
    assert out == "**[00:01] Alice:** hej\n"


def test_summary_and_todo_commands_use_the_llm_module(monkeypatch, capsys):
    import json

    from steno import llm

    w = make_session()
    w.close()
    calls = []

    def fake_run(system, text, cfg, runner=None):
        calls.append((system, text))
        if "commitments" in system:
            return json.dumps([{"owner": "Remote", "task": "Svara", "due": None,
                                "confidence": 0.8, "evidence_quote": "tjena", "t": "00:03"}])
        return "## Översikt\nKort."

    monkeypatch.setattr(llm, "run_claude", fake_run)
    assert cli.main(["summary"]) == 0
    assert "## Översikt" in capsys.readouterr().out
    assert (w.dir / "summary.md").is_file()
    assert cli.main(["todo", "--person", "Remote"]) == 0
    assert "- [ ] Svara" in capsys.readouterr().out
    assert json.loads((w.dir / "todo.json").read_text(encoding="utf-8"))[0]["owner"] == "Remote"
    assert all("[00:01] Me: hej" in text for _, text in calls)


def test_summary_reports_llm_errors_without_a_traceback(monkeypatch, capsys):
    from steno import llm

    w = make_session()
    w.close()

    def boom(*a, **k):
        raise llm.LlmError("the Claude CLI failed")

    monkeypatch.setattr(llm, "run_claude", boom)
    assert cli.main(["summary"]) == 1
    assert "the Claude CLI failed" in capsys.readouterr().err


def test_finalize_without_audio_fails_cleanly(capsys):
    w = make_session()
    w.close()
    assert cli.main(["finalize"]) == 1
    assert "keep-audio" in capsys.readouterr().err
    assert not (w.dir / "final.jsonl").exists()


def test_todo_person_and_all_are_mutually_exclusive():
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["todo", "--person", "Bob", "--all"])
