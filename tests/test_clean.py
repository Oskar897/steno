from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import pytest

from steno import clean, cli, labels, llm
from steno.config import LlmConfig
from steno.transcript import Segment, SessionWriter, read_segments
from tests.test_llm import Recorder

CFG = LlmConfig()

# Invented lines, fake names.
LINES = [
    ("Anna", "vi kör allting i en dokker kontejner och sen deployar vi på fredag"),
    ("Per", "jag jag tror att Karin kommer på tisdag klockan 14"),
    ("Anna", "det är ett problem med rapporten"),
    ("Per", "ja det stämmer"),
    ("Anna", "modellen over fit på träningsdatan"),
    ("Per", "det är inte klart"),
]


def make_session(tmp_path, lines=LINES):
    w = SessionWriter.create(root=tmp_path / "s", sources=["mic"], now=datetime(2026, 1, 1, 10))
    for i, (speaker, text) in enumerate(lines):
        w.append(t_start=i * 5.0, t_end=i * 5.0 + 4, source="mic", speaker=speaker, text=text,
                 lang="sv")
    w.close()
    return w.dir


def reply(*edits):
    return json.dumps([{"id": i, "old": o, "new": n} for i, o, n in edits])


def seg(seq, text, speaker="A"):
    return Segment(seq, seq * 5.0, seq * 5.0 + 4, "2026-01-01T10:00:00", "mic", speaker, text, "sv")


# --------------------------------------------------------------------------- chunking


def test_chunks_keep_order_whole_lines_and_the_size_limit():
    segs = [seg(i, "ord " * 20) for i in range(1, 11)]  # about 90 characters rendered each
    chunks = clean.chunk_segments(segs, 300)
    assert [s.seq for c in chunks for s in c] == list(range(1, 11))
    assert len(chunks) > 1
    for c in chunks:
        assert sum(len(clean.render_line(s)) + 1 for s in c) <= 300
    assert clean.chunk_segments([], 300) == []


def test_a_line_longer_than_the_limit_gets_its_own_chunk():
    segs = [seg(1, "kort"), seg(2, "lång " * 100), seg(3, "kort")]
    chunks = clean.chunk_segments(segs, 100)
    assert [[s.seq for s in c] for c in chunks] == [[1], [2], [3]]


def test_lines_are_sent_with_id_and_speaker():
    assert clean.render_line(seg(41, "hej", "Anna")) == "[41] Anna: hej"


# --------------------------------------------------------------------------- validation

TEXTS = {i: t for i, (_, t) in enumerate(LINES, start=1)}
KNOWN = {w.casefold() for t in TEXTS.values() for w in clean._words(t)}


def check(seg_id, old, new, max_edit=0.3, texts=TEXTS):
    edit = {"id": seg_id, "old": old, "new": new}
    return clean.validate_edit(edit, texts, {**TEXTS, **texts}, KNOWN, max_edit)


@pytest.mark.parametrize(
    "edit",
    [
        (1, "dokker kontejner", "Docker container"),  # phonetic English term
        (1, "fredag", "fredag."),  # punctuation
        (1, "vi", "Vi"),  # casing, first occurrence
        (2, "jag jag", "Jag"),  # stutter duplicate
        (5, "over fit", "overfit"),  # merged words
        ("3", "problem", "problem,"),  # id as a string
        ("[3]", "rapporten", "rapporten."),  # id as the model saw it
    ],
)
def test_valid_edits(edit):
    assert check(*edit) == (True, "")


@pytest.mark.parametrize(
    "edit, reason",
    [
        ((9, "a", "b"), "unknown id"),
        (("x", "a", "b"), "bad id"),
        ((True, "a", "b"), "bad id"),
        ((3, "rapp", "rapport"), "old not in line"),  # only whole words match
        ((3, "kubernetes", "Kubernetes"), "old not in line"),
        ((3, "problem", "problem"), "no change"),
        ((3, "problem", ""), "empty old/new"),
        ((3, "problem", "pro\nblem"), "new spans lines"),
        ((2, "14", "15"), "changes a number or a guarded word"),
        ((2, "klockan 14", "klockan fjorton"), "changes a number or a guarded word"),
        ((2, "tisdag", "fredag"), "changes a number or a guarded word"),
        ((6, "inte klart", "klart"), "changes a number or a guarded word"),
        ((4, "ja", "nej"), "changes a number or a guarded word"),
        ((2, "Karin", "Kalle"), "changes a name"),
        ((3, "problem", "Stefan"), "introduces a name"),
        ((3, "ett problem", "problem"), "removes content"),
        ((4, "stämmer", "stämmer och jag håller med dig helt"), "adds content"),
        ((3, "problem", "hus"), "replaces a word with something unrelated"),
        ((3, "ett problem med rapporten", "en lösning för budgeten"), "edit too large"),
    ],
)
def test_rejected_edits(edit, reason):
    assert check(*edit) == (False, reason)


def test_malformed_edits_are_rejected():
    bad = ("junk", None, 3, {"id": 1}, {"old": "a", "new": "b"}, {"id": 1, "old": 1, "new": 2})
    for edit in bad:
        ok, _ = clean.validate_edit(edit, TEXTS, TEXTS, KNOWN, 0.3)
        assert not ok


def test_a_name_may_be_corrected_towards_a_name_in_the_transcript():
    # "karin" occurs in the transcript, so a mis-heard "Karen" may become "Karin"
    texts = {**TEXTS, 7: "sen ringde Karen"}
    ok, _ = check(7, "Karen", "Karin", texts=texts)
    assert ok
    # a lower-case mis-hearing may turn into a similar-looking name
    texts = {**TEXTS, 7: "sen ringde kareen"}
    assert check(7, "kareen", "Karin", texts=texts)[0]


def test_a_sentence_initial_capital_is_not_a_name_change():
    texts = {**TEXTS, 7: "det är bra. det går fort"}
    assert check(7, "det går", "Det går", texts=texts)[0]


def test_line_budget_covers_all_edits_of_a_line():
    texts = {**TEXTS, 7: "ett test av nätverket i kväll"}  # budget: 0.15 * 30 = 4.5 characters
    work = dict(texts)
    ok, bad = clean.apply_edits(
        {7}, work, dict(texts), KNOWN,
        [{"id": 7, "old": "kväll", "new": "kvällen"},  # 2 characters each
         {"id": 7, "old": "test", "new": "tester"},
         {"id": 7, "old": "nätverket", "new": "natverken"}],
        0.15,
    )
    assert work[7] == "ett tester av nätverket i kvällen"
    assert len(ok) == 2 and [r["reason"] for r in bad] == ["line changed too much"]


# --------------------------------------------------------------------------- applying


def test_apply_edits_maps_back_by_id_and_applies_in_order():
    original = {1: "vi kör dokker", 2: "ja"}
    work = dict(original)
    edits = [
        {"id": 1, "old": "dokker", "new": "Docker"},
        {"id": 1, "old": "Docker", "new": "Docker."},  # builds on the first edit
        {"id": 2, "old": "ja", "new": "nej"},  # guarded
        {"id": 3, "old": "x", "new": "y"},  # not in the chunk
    ]
    ok, bad = clean.apply_edits({1, 2}, work, original, set(), edits, 0.3)
    assert work == {1: "vi kör Docker.", 2: "ja"}
    assert [e["seq"] for e in ok] == [1, 1]
    assert [r["reason"] for r in bad] == ["changes a number or a guarded word", "unknown id"]


def test_an_edit_may_not_address_a_line_outside_its_chunk():
    original = {1: "vi kör dokker", 2: "och dokker igen"}
    work = dict(original)
    ok, bad = clean.apply_edits({1}, work, original, set(),
                                [{"id": 2, "old": "dokker", "new": "Docker"}], 0.3)
    assert ok == [] and bad[0]["reason"] == "unknown id" and work == original


# --------------------------------------------------------------------------- the run


GOOD = (
    (1, "dokker kontejner", "Docker container"),
    (1, "fredag", "fredag."),
    (2, "jag jag", "Jag"),
    (5, "over fit", "overfit"),
)
BAD = (
    (2, "14", "15"),
    (3, "problem", "Stefan"),
    (99, "x", "y"),
)


def test_clean_session_writes_clean_files_and_leaves_the_transcript_alone(tmp_path):
    session = make_session(tmp_path)
    before = (session / "transcript.jsonl").read_bytes()
    rec = Recorder(reply(*GOOD, *BAD))
    res = clean.clean_session(session, CFG, runner=rec)

    assert res.written and res.meta["applied"] == 4 and res.meta["rejected"] == 3
    assert res.meta["proposed"] == 7 and res.meta["changed_lines"] == 3
    assert res.meta["rejected_by_reason"] == {
        "changes a number or a guarded word": 1, "introduces a name": 1, "unknown id": 1}
    assert (session / "transcript.jsonl").read_bytes() == before

    cleaned = {s.seq: s for s in read_segments(session, name="clean.jsonl")}
    assert cleaned[1].text == "vi kör allting i en Docker container och sen deployar vi på fredag."
    assert cleaned[2].text == "Jag tror att Karin kommer på tisdag klockan 14"
    assert cleaned[3].text == LINES[2][1]  # untouched
    assert cleaned[5].text == "modellen overfit på träningsdatan"
    assert cleaned[1].speaker == "Anna" and cleaned[1].t_start == 0.0
    md = (session / "clean.md").read_text(encoding="utf-8")
    assert "**[00:00] Anna:** vi kör allting i en Docker container" in md
    meta = json.loads((session / "clean.json").read_text(encoding="utf-8"))
    assert meta["model"] == "haiku" and meta["source"] == "live" and meta["chunks"] == 1
    assert meta["edits"][0] == {"seq": 1, "old": "dokker kontejner", "new": "Docker container"}
    assert meta["rejected_edits"][0]["reason"] == "changes a number or a guarded word"


def test_only_text_with_ids_and_speakers_is_sent(tmp_path):
    session = make_session(tmp_path)
    rec = Recorder("[]")
    clean.clean_session(session, CFG, runner=rec)
    cmd, kw = rec.calls[0]
    assert kw["input"].splitlines()[0] == f"[1] Anna: {LINES[0][1]}"
    assert not any(LINES[0][1] in a for a in cmd)
    assert "Swedish" in rec.system_prompts[0] and "{language}" not in rec.system_prompts[0]
    assert "[]" in rec.system_prompts[0]


def test_dry_run_writes_nothing(tmp_path):
    session = make_session(tmp_path)
    res = clean.clean_session(session, CFG, dry_run=True, runner=Recorder(reply(*GOOD)))
    assert not res.written and res.meta["applied"] == 4
    assert not any((session / n).exists() for n in
                   ("clean.jsonl", "clean.md", "clean.json"))


def test_many_chunks_and_a_failing_one(tmp_path, monkeypatch):
    lines = [("Anna", f"rad {i} med dokker och mer text för att fylla ut raden ordentligt")
             for i in range(1, 9)]
    session = make_session(tmp_path, lines)
    monkeypatch.setattr(clean, "CHUNK_CHARS", 200)
    n_chunks = len(clean.chunk_segments(read_segments(session), 200))
    assert n_chunks >= 3

    class Flaky(Recorder):
        def __call__(self, cmd, **kw):
            self.reply = reply(*[(int(line[1 : line.index("]")]), "dokker", "Docker")
                                 for line in kw["input"].splitlines()])
            if not self.calls:
                self.returncode, self.stderr = 1, "down"
            else:
                self.returncode = 0
            return super().__call__(cmd, **kw)

    res = clean.clean_session(session, CFG, runner=Flaky())
    assert res.meta["chunks"] == n_chunks and res.meta["failed_chunks"] == 1
    texts = [s.text for s in read_segments(session, name="clean.jsonl")]
    assert sum("Docker" in t for t in texts) < len(texts)  # the failed chunk stays as it was
    assert sum("Docker" in t for t in texts) > 0
    assert "chunk(s) failed" in clean.summary_line(res.meta)


def test_when_every_chunk_fails_nothing_is_written(tmp_path):
    session = make_session(tmp_path)
    with pytest.raises(llm.LlmError, match="failed"):
        clean.clean_session(session, CFG, runner=Recorder(returncode=1, stderr="down"))
    assert not (session / "clean.jsonl").exists()
    with pytest.raises(llm.LlmError):
        clean.clean_session(session, CFG, runner=Recorder("no json here"))


def test_empty_session_is_refused(tmp_path):
    w = SessionWriter.create(root=tmp_path / "e", now=datetime(2026, 1, 2))
    w.close()
    with pytest.raises(llm.LlmError, match="no transcript"):
        clean.clean_session(w.dir, CFG, runner=Recorder("[]"))


def test_the_final_transcript_is_the_source_and_ids_are_its_seq(tmp_path):
    session = make_session(tmp_path)
    final = [replace(seg(7, "vi kör dokker idag", "Anna"), final=True),
             replace(seg(8, "det är bra", "Per"), final=True)]
    (session / "final.jsonl").write_text(
        "".join(s.to_json() + "\n" for s in final), encoding="utf-8")
    rec = Recorder(reply((7, "dokker", "Docker")))
    res = clean.clean_session(session, CFG, runner=rec)
    assert res.meta["source"] == "final"
    assert rec.calls[0][1]["input"].startswith("[7] Anna: vi kör dokker idag")
    cleaned = read_segments(session, name="clean.jsonl")
    assert [(s.seq, s.text) for s in cleaned] == [(7, "vi kör Docker idag"), (8, "det är bra")]
    assert all(s.final for s in cleaned)
    assert (session / "final.jsonl").read_text(encoding="utf-8").count("dokker") == 1


def test_changed_lines_lose_their_word_times(tmp_path):
    session = make_session(tmp_path, [("Anna", "vi kör dokker"), ("Anna", "det är bra")])
    final = [replace(seg(1, "vi kör dokker"), final=True, words=[[0.0, 0.5, "vi"]]),
             replace(seg(2, "det är bra"), final=True, words=[[5.0, 5.5, "det"]])]
    (session / "final.jsonl").write_text(
        "".join(s.to_json() + "\n" for s in final), encoding="utf-8")
    clean.clean_session(session, CFG, runner=Recorder(reply((1, "dokker", "Docker"))))
    first, second = read_segments(session, name="clean.jsonl")
    assert first.words is None and second.words == [[5.0, 5.5, "det"]]


# --------------------------------------------------------------------------- summary / to-do


def test_summary_and_todo_use_the_clean_text_and_say_so(tmp_path):
    session = make_session(tmp_path)
    clean.clean_session(session, CFG, runner=Recorder(reply(*GOOD)))

    rec = Recorder("## Översikt\nKort.")
    path, kind = llm.summarize(session, CFG, rec)
    assert kind == "clean"
    sent = rec.calls[0][1]["input"]
    assert "Docker container" in sent and "dokker" not in sent
    assert "Anna: vi kör" in sent
    text = path.read_text(encoding="utf-8")
    assert text.startswith("## Översikt\nKort.") and text.rstrip().endswith(
        "_Text used: clean transcript (clean.jsonl), model sonnet._")

    todo = Recorder(json.dumps([{
        "owner": "Anna", "task": "Deploya", "due": "fredag", "confidence": 0.8,
        "evidence_quote": "deployar vi på fredag.", "t": "00:00"}]))
    items, dropped, kind = llm.extract_todos(session, CFG, runner=todo)
    assert kind == "clean" and len(items) == 1 and dropped == 0  # quote is in the clean text
    assert "Docker container" in todo.calls[0][1]["input"]
    md = (session / "todo.md").read_text(encoding="utf-8")
    assert md.rstrip().endswith("_Text used: clean transcript (clean.jsonl), model sonnet._")


def test_without_clean_text_the_old_behaviour_stays_and_the_note_names_the_text(tmp_path):
    session = make_session(tmp_path)
    rec = Recorder("ok")
    path, kind = llm.summarize(session, CFG, rec)
    assert kind == "live" and "dokker" in rec.calls[0][1]["input"]
    assert path.read_text(encoding="utf-8").endswith("_Text used: live transcript "
                                                     "(transcript.jsonl), model sonnet._\n")


def test_stale_clean_text_is_ignored(tmp_path):
    session = make_session(tmp_path)
    clean.clean_session(session, CFG, runner=Recorder(reply(*GOOD)))
    assert llm.load_transcript(session)[1] == "clean"
    # a new final transcript: the clean text no longer belongs to the text it was made from
    final = [replace(seg(1, "helt ny text"), final=True)]
    (session / "final.jsonl").write_text(final[0].to_json() + "\n", encoding="utf-8")
    segs, kind = llm.load_transcript(session)
    assert kind == "final" and segs[0].text == "helt ny text"
    assert clean.status(session)["fresh"] is False


def test_current_speaker_names_are_used_with_the_clean_text(tmp_path):
    w = SessionWriter.create(root=tmp_path / "n", sources=["mic"], now=datetime(2026, 1, 1, 10))
    w.append(t_start=0, t_end=3, source="mic", speaker="Speaker 1", text="vi kör dokker",
             lang="sv", speaker_id="S1")
    w.close()
    clean.clean_session(w.dir, CFG, runner=Recorder(reply((1, "dokker", "Docker"))))
    labels.SessionLabels(w.dir).set_cluster_name("S1", "Anna", None)  # named after the clean-up
    segs, kind = llm.load_transcript(w.dir)
    assert kind == "clean" and (segs[0].speaker, segs[0].text) == ("Anna", "vi kör Docker")


# --------------------------------------------------------------------------- CLI


def test_cli_dry_run_prints_edits_and_writes_nothing(monkeypatch, capsys, tmp_path):
    w = SessionWriter.create(model="fake", sources=["mic"], now=datetime(2026, 1, 1, 10))
    w.append(t_start=1, t_end=2, source="mic", speaker="Me", text="vi kör dokker", lang="sv")
    w.close()
    sent = []

    def fake_run(system, text, cfg, runner=None):
        sent.append(text)
        return reply((1, "dokker", "Docker"), (1, "vi", "Jag"), (7, "x", "y"))

    monkeypatch.setattr(llm, "run_claude", fake_run)
    assert cli.main(["clean", "--dry-run"]) == 0
    cap = capsys.readouterr()
    assert "'dokker' -> 'Docker'" in cap.out and "1 of 3 edit(s) applied" in cap.out
    assert "rejected (unknown id)" in cap.out and "dry run" in cap.err
    assert sent == ["[1] Me: vi kör dokker"]
    assert not (w.dir / "clean.jsonl").exists()

    assert cli.main(["clean", w.session_id]) == 0
    assert "Cleaned" in capsys.readouterr().out
    assert read_segments(w.dir, name="clean.jsonl")[0].text == "vi kör Docker"


def test_cli_reports_llm_errors_without_a_traceback(monkeypatch, capsys):
    w = SessionWriter.create(model="fake", sources=["mic"], now=datetime(2026, 1, 1, 10))
    w.append(t_start=1, t_end=2, source="mic", speaker="Me", text="hej", lang="sv")
    w.close()

    def boom(*a, **k):
        raise llm.LlmError("the Claude CLI failed")

    monkeypatch.setattr(llm, "run_claude", boom)
    assert cli.main(["clean"]) == 1
    assert "the Claude CLI failed" in capsys.readouterr().err
    assert not (w.dir / "clean.jsonl").exists()


def test_ensure_clean_runs_the_clean_up_only_when_the_clean_text_is_missing(tmp_path):
    session = make_session(tmp_path)
    rec = Recorder(reply(*GOOD))
    note = clean.ensure_clean(session, CFG, runner=rec)
    assert note is not None and note.startswith("cleaned first:") and len(rec.calls) == 1
    assert llm.load_transcript(session)[1] == "clean"
    assert clean.ensure_clean(session, CFG, runner=rec) is None and len(rec.calls) == 1


def test_a_failed_clean_up_before_summary_is_reported_not_raised(tmp_path):
    session = make_session(tmp_path)
    note = clean.ensure_clean(session, CFG, runner=Recorder("no json here"))
    assert note is not None and "clean-up failed" in note and "live" in note
    assert llm.load_transcript(session)[1] == "live"
