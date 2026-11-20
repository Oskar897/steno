from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from steno import llm
from steno.config import LlmConfig
from steno.transcript import Segment, SessionWriter

CFG = LlmConfig()


def seg(seq, t, speaker, text):
    return Segment(seq, t, t + 3, "2026-01-01T10:00:00", "mic", speaker, text, "sv")


SEGMENTS = [
    seg(1, 5.0, "Alice", "Jag skickar rapporten till er på fredag."),
    seg(2, 70.0, "Bob", "Okej, då tar jag budgeten, den är klar nästa vecka."),
    seg(3, 130.0, "Alice", "Vi måste fundera på lokalen också."),
]


class Recorder:
    """subprocess.run stand-in that records the call and answers with a canned reply."""

    def __init__(self, reply="", returncode=0, stderr="", as_json=True, raises=None):
        self.reply, self.returncode, self.stderr = reply, returncode, stderr
        self.as_json, self.raises = as_json, raises
        self.calls: list[tuple[list[str], dict]] = []
        self.system_prompts: list[str] = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if "--system-prompt-file" in cmd:  # the temp dir is gone after the call: read it now
            path = Path(cmd[cmd.index("--system-prompt-file") + 1])
            self.system_prompts.append(path.read_text(encoding="utf-8"))
        if self.raises:
            raise self.raises
        out = json.dumps({"type": "result", "is_error": False, "result": self.reply})
        return subprocess.CompletedProcess(
            cmd, self.returncode, out if self.as_json else self.reply, self.stderr
        )


# --------------------------------------------------------------------------- the CLI call


def test_transcript_goes_on_stdin_and_never_into_argv():
    rec = Recorder("ok")
    out = llm.run_claude("SYSTEM PROMPT", "[00:05] Alice: hemligt innehåll", CFG, rec)
    assert out == "ok"
    cmd, kw = rec.calls[0]
    assert kw["input"] == "[00:05] Alice: hemligt innehåll"
    assert not any("hemligt" in a for a in cmd)
    assert cmd[1:3] == ["-p", "--model"] and cmd[3] == "haiku"
    assert ["--tools", ""] == cmd[cmd.index("--tools") : cmd.index("--tools") + 2]  # no tools
    assert "--no-session-persistence" in cmd
    assert rec.system_prompts == ["SYSTEM PROMPT"]
    # the user's own CLAUDE.md, hooks and MCP servers stay out of the call
    assert ["--setting-sources", ""] == cmd[cmd.index("--setting-sources") :][:2]
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert kw["timeout"] == CFG.timeout_s
    assert Path(kw["cwd"]).name.startswith("steno-llm-")  # an empty temp dir, not the project


def test_model_and_effort_come_from_the_config():
    rec = Recorder("ok")
    llm.run_claude("s", "t", LlmConfig(model="sonnet", effort=""), rec)
    cmd = rec.calls[0][0]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and "--effort" not in cmd


def test_plain_text_output_is_accepted():
    assert llm.run_claude("s", "t", CFG, Recorder("  hello \n", as_json=False)) == "hello"


@pytest.mark.parametrize(
    "rec, needle",
    [
        (Recorder(returncode=1, stderr="boom\nlogin required"), "login required"),
        (Recorder(raises=FileNotFoundError()), "install Claude Code"),
        (Recorder(raises=subprocess.TimeoutExpired("claude", 5)), "did not answer"),
    ],
)
def test_cli_failures_become_llm_errors(rec, needle):
    with pytest.raises(llm.LlmError, match=needle):
        llm.run_claude("s", "t", CFG, rec)


def test_error_flag_in_json_output_is_reported():
    def run(cmd, **kw):
        out = json.dumps({"is_error": True, "result": "rate limited"})
        return subprocess.CompletedProcess(cmd, 0, out, "")

    with pytest.raises(llm.LlmError, match="rate limited"):
        llm.run_claude("s", "t", CFG, run)


def test_prompts_exist_and_get_the_language_filled_in():
    for name in ("summary", "todo", "fix"):
        text = llm.load_prompt(name, "sv")
        assert text.strip() and "{language}" not in text
    assert "Swedish" in llm.load_prompt("summary", "sv")
    assert "language of the transcript" in llm.load_prompt("summary", "auto")
    assert "xx" in llm.language_phrase("xx")  # unknown codes are passed through


# --------------------------------------------------------------------------- JSON handling


def test_parse_json_array_tolerates_fences_and_chatter():
    assert llm.parse_json_array('[{"a": 1}]') == [{"a": 1}]
    assert llm.parse_json_array('```json\n[1, 2]\n```') == [1, 2]
    assert llm.parse_json_array('Here you go: [{"a": 1}] hope it helps') == [{"a": 1}]
    assert llm.parse_json_array("[]") == []
    for bad in ("no json here", '{"a": 1}', "[1, 2"):
        with pytest.raises(llm.LlmError):
            llm.parse_json_array(bad)


def test_normalise_ignores_case_punctuation_and_spacing():
    assert llm.normalise("  Jag  skickar,\nrapporten! ") == "jag skickar rapporten"
    assert llm.normalise("Åsa—Örjan") == "åsa örjan"
    assert llm.normalise("") == ""


def test_parse_clock():
    assert llm.parse_clock("01:10") == 70.0
    assert llm.parse_clock("1:02:03") == 3723.0
    assert llm.parse_clock(12) == 12.0
    assert llm.parse_clock("soon") is None and llm.parse_clock(None) is None


# --------------------------------------------------------------------------- todo


def item(**kw):
    base = {"owner": "Alice", "task": "Skicka rapporten", "due": "fredag", "confidence": 0.9,
            "evidence_quote": "Jag skickar rapporten till er på fredag", "t": "00:05"}
    return {**base, **kw}


def test_item_with_verbatim_evidence_is_kept_and_timed_from_the_transcript():
    kept, dropped = llm.filter_todos([item(t="09:99")], SEGMENTS)
    assert dropped == [] and len(kept) == 1
    assert kept[0].t == 5.0  # the model's timestamp is ignored: the quote's line decides
    assert kept[0].owner == "Alice" and kept[0].due == "fredag"


def test_evidence_match_ignores_case_and_punctuation():
    quote = "JAG SKICKAR RAPPORTEN till er, på fredag!"
    kept, _ = llm.filter_todos([item(evidence_quote=quote)], SEGMENTS)
    assert len(kept) == 1


def test_item_without_evidence_in_the_transcript_is_dropped():
    invented = item(evidence_quote="Jag fixar allt före midsommar", task="Fixa allt")
    paraphrase = item(evidence_quote="Alice skickar rapporten på fredag")
    empty = item(evidence_quote="")
    no_task = item(task=" ")
    kept, dropped = llm.filter_todos([invented, paraphrase, empty, no_task, "junk"], SEGMENTS)
    assert kept == [] and len(dropped) == 5
    assert dropped[0]["reason"] == "evidence not in transcript"


def test_quote_may_span_two_lines_and_a_missing_owner_becomes_tbd():
    two = [seg(1, 10.0, "Alice", "Vi borde boka"), seg(2, 14.0, "Bob", "rummet, jag fixar det.")]
    kept, dropped = llm.filter_todos(
        [item(owner="", evidence_quote="boka rummet jag fixar det", task="Boka rummet", due=None)],
        two,
    )
    assert len(kept) == 1 and kept[0].owner == "TBD" and kept[0].due is None
    assert kept[0].t == 10.0 and dropped == []


def test_evidence_must_match_whole_words():
    # "skickar rapp" is inside "skickar rapporten" but is not a quote of it
    kept, dropped = llm.filter_todos([item(evidence_quote="skickar rapp")], SEGMENTS)
    assert kept == [] and dropped[0]["reason"] == "evidence not in transcript"
    kept, _ = llm.filter_todos([item(evidence_quote="rapporten till er")], SEGMENTS)
    assert len(kept) == 1


def test_confidence_is_clamped_and_defaults_when_garbage():
    kept, _ = llm.filter_todos([item(confidence=7), item(confidence="high")], SEGMENTS)
    assert [k.confidence for k in kept] == [1.0, 0.5]


def test_person_filter_and_markdown_grouping():
    kept, _ = llm.filter_todos(
        [
            item(),
            item(owner="Bob", task="Ta budgeten", due="nästa vecka",
                 evidence_quote="då tar jag budgeten, den är klar nästa vecka"),
            item(owner="TBD", task="Fundera på lokalen",
                 evidence_quote="Vi måste fundera på lokalen också", due=None),
        ],
        SEGMENTS,
    )
    assert [i.owner for i in llm.filter_person(kept, " bob ")] == ["Bob"]
    md = llm.render_todo_md(kept)
    assert md.index("## Alice") < md.index("## Bob") < md.index("## TBD")  # TBD last
    assert "- [ ] Ta budgeten (due: nästa vecka) - 01:10" in md
    assert "> då tar jag budgeten" in md
    assert "_No explicit commitments found._" in llm.render_todo_md([])


def make_session(tmp_path):
    w = SessionWriter.create(root=tmp_path / "s", sources=["mic"], now=datetime(2026, 1, 1, 10))
    for s in SEGMENTS:
        w.append(t_start=s.t_start, t_end=s.t_end, source="mic", speaker=s.speaker, text=s.text,
                 lang="sv")
    w.close()
    return w.dir


def test_extract_todos_writes_files_and_only_trusts_the_transcript(tmp_path):
    session = make_session(tmp_path)
    reply = json.dumps([item(), item(evidence_quote="en helt påhittad mening", task="Påhitt")])
    rec = Recorder(reply)
    items, dropped, kind = llm.extract_todos(session, CFG, runner=rec)
    assert kind == "live" and dropped == 1 and [i.task for i in items] == ["Skicka rapporten"]
    data = json.loads((session / "todo.json").read_text(encoding="utf-8"))
    assert data == [items[0].as_dict()]
    assert set(data[0]) == {"owner", "task", "due", "confidence", "evidence_quote", "t"}
    assert "Skicka rapporten" in (session / "todo.md").read_text(encoding="utf-8")
    stdin = rec.calls[0][1]["input"]
    assert "[00:05] Alice: Jag skickar rapporten" in stdin
    assert "summary" not in stdin.lower()  # the to-do list never comes from a summary


def test_extract_todos_person_filter(tmp_path):
    session = make_session(tmp_path)
    reply = json.dumps([item(), item(owner="Bob", task="Ta budgeten",
                                     evidence_quote="då tar jag budgeten")])
    items, _, _ = llm.extract_todos(session, CFG, person="Bob", runner=Recorder(reply))
    assert [i.owner for i in items] == ["Bob"]
    data = json.loads((session / "todo.json").read_text(encoding="utf-8"))
    assert [d["owner"] for d in data] == ["Bob"]
    assert (session / "todo.md").read_text(encoding="utf-8").startswith("# To-do: Bob")


def test_summary_uses_the_final_transcript_when_it_exists(tmp_path):
    session = make_session(tmp_path)
    rec = Recorder("## Översikt\nKort.")
    path, kind = llm.summarize(session, CFG, rec)
    assert kind == "live" and path.read_text(encoding="utf-8") == (
        "## Översikt\nKort.\n\n_Text used: live transcript (transcript.jsonl)._\n")
    assert "Alice: Jag skickar" in rec.calls[0][1]["input"]

    final = [seg(1, 5.0, "Anna (final)", "Slutlig text.")]
    from dataclasses import replace

    (session / "final.jsonl").write_text(
        "".join(replace(s, final=True).to_json() + "\n" for s in final), encoding="utf-8"
    )
    rec2 = Recorder("ok")
    _, kind2 = llm.summarize(session, CFG, rec2)
    assert kind2 == "final" and "Anna (final): Slutlig text." in rec2.calls[0][1]["input"]


def test_summary_rejects_empty_answers_and_empty_sessions(tmp_path):
    session = make_session(tmp_path)
    with pytest.raises(llm.LlmError, match="empty"):
        llm.summarize(session, CFG, Recorder("   "))
    empty = SessionWriter.create(root=tmp_path / "e", now=datetime(2026, 1, 2))
    empty.close()
    with pytest.raises(llm.LlmError, match="no transcript"):
        llm.summarize(empty.dir, CFG, Recorder("x"))


# --------------------------------------------------------------------------- fix


def test_edit_distance():
    assert llm.edit_distance("kitten", "sitting") == 3
    assert llm.edit_distance("", "abc") == 3 and llm.edit_distance("same", "same") == 0


TEXTS = {1: "Vi använder kubernetes för tjänsterna", 2: "Ja"}


@pytest.mark.parametrize(
    "edit, ok, reason",
    [
        ({"id": 1, "old": "kubernetes", "new": "Kubernetes"}, True, ""),
        ({"id": 1, "old": "kubernets", "new": "Kubernetes"}, False, "old not in line"),
        ({"id": 9, "old": "a", "new": "b"}, False, "unknown id"),
        ({"id": 1, "old": "kubernetes för tjänsterna", "new": "ett helt annat påstående"}, False,
         "edit too large"),
        ({"id": 1, "old": "kubernetes", "new": "kubernetes"}, False, "no change"),
        ({"id": 1, "old": "", "new": "x"}, False, "empty old/new"),
        ({"id": 1, "old": "kubernetes", "new": ""}, False, "empty old/new"),
        ({"id": "x", "old": "a", "new": "b"}, False, "bad id"),
        ("junk", False, "not an object"),
        ({"id": 2, "old": "Ja", "new": "Nej"}, False, "edit too large"),  # 100 % of a short line
    ],
)
def test_validate_edit(edit, ok, reason):
    assert llm.validate_edit(edit, TEXTS, 0.3) == (ok, reason)


def test_apply_edits_changes_text_logs_rejections_and_drops_word_times():
    segs = [
        Segment(1, 0, 3, "w", "mic", "A", TEXTS[1], "sv", words=[[0.0, 1.0, "Vi"]]),
        Segment(2, 3, 4, "w", "mic", "A", TEXTS[2], "sv", words=[[3.0, 3.5, "Ja"]]),
    ]
    edits = [
        {"id": 1, "old": "kubernetes", "new": "Kubernetes"},
        {"id": 1, "old": "tjänsterna", "new": "tjänsterna."},  # applies to the edited text
        {"id": 2, "old": "Ja", "new": "Nej"},
        {"id": 7, "old": "x", "new": "y"},
    ]
    out, report = llm.apply_edits(segs, edits, 0.3)
    assert out[0].text == "Vi använder Kubernetes för tjänsterna." and out[0].words is None
    assert out[1].text == "Ja" and out[1].words == [[3.0, 3.5, "Ja"]]  # untouched
    assert report["applied"] == 2 and report["proposed"] == 4
    assert [r["reason"] for r in report["rejected"]] == ["edit too large", "unknown id"]
    assert segs[0].text == TEXTS[1]  # the input is not mutated


def test_llm_fix_end_to_end_with_a_mocked_model():
    segs = [Segment(i, i * 3.0, i * 3.0 + 2, "w", "mic", "A", t, "sv", final=True)
            for i, t in enumerate(["Vi kör postgress", "Det är bra"], start=1)]
    reply = json.dumps([{"id": 1, "old": "postgress", "new": "PostgreSQL"},
                        {"id": 2, "old": "Det är bra", "new": "Detta är utmärkt fantastiskt"}])
    rec = Recorder(reply)
    out, report = llm.llm_fix(segs, CFG, rec)
    assert [s.text for s in out] == ["Vi kör PostgreSQL", "Det är bra"]
    assert report["applied"] == 1 and len(report["rejected"]) == 1
    sent = rec.calls[0][1]["input"]
    assert "[1] Vi kör postgress" in sent and "[2] Det är bra" in sent


def test_llm_fix_survives_a_failing_model():
    segs = [Segment(1, 0, 2, "w", "mic", "A", "text", "sv")]
    out, report = llm.llm_fix(segs, CFG, Recorder(returncode=1, stderr="down"))
    assert out == segs and report["failed_batches"] == 1 and report["applied"] == 0
    out2, report2 = llm.llm_fix(segs, CFG, Recorder("not json"))
    assert out2 == segs and report2["failed_batches"] == 1


def test_llm_fix_batches_long_transcripts():
    long_text = "ord " * 600
    segs = [Segment(i, i * 5.0, i * 5.0 + 4, "w", "mic", "A", long_text, "sv") for i in range(1, 7)]
    rec = Recorder("[]")
    llm.llm_fix(segs, CFG, rec)
    assert len(rec.calls) >= 2  # split into several requests


def test_claude_runs_without_a_console_window(monkeypatch):
    seen = {}

    def runner(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result": "ok"}', stderr="")

    assert llm.run_claude("sys", "text", LlmConfig(), runner=runner) == "ok"
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert seen["startupinfo"].wShowWindow == 0
