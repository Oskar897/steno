"""revisions.jsonl: writer, reader, the best-text view and the "only what is new" rule."""

from __future__ import annotations

import json

import pytest

from steno import cli, labels, transcript
from steno.transcript import SessionWriter


def make(tmp_path, texts=("hej alla", "nu börjar vi", "tack för idag")):
    w = SessionWriter.create(root=tmp_path / "sessions", model="fake", sources=["mic"])
    for i, text in enumerate(texts):
        w.append(t_start=i * 5.0, t_end=i * 5.0 + 3.0, source="mic", speaker="Me", text=text,
                 lang="sv", words=[[i * 5.0, i * 5.0 + 1.0, text.split()[0]]])
    return w


def revise(w, seq, text, **extra):
    return w.append_revisions([(seq, text, extra)], model="m")


def test_revision_is_appended_and_the_live_line_is_untouched(tmp_path):
    w = make(tmp_path)
    before = (w.dir / "transcript.jsonl").read_bytes()
    revised = revise(w, 2, "nu börjar vi med punkt ett", lang="sv", avg_logprob=-0.2,
                     words=[[5.0, 5.4, "nu"], [5.4, 6.0, "börjar"]])
    assert (w.dir / "transcript.jsonl").read_bytes() == before
    assert [s.seq for s in revised] == [2] and revised[0].text == "nu börjar vi med punkt ett"
    raw = (w.dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(raw[0])
    assert list(rec)[:5] == ["v", "rev", "seq", "wall", "text"]
    assert rec["rev"] == 1 and rec["seq"] == 2 and rec["model"] == "m" and rec["v"] == 1
    assert rec["words"] == [[5.0, 5.4, "nu"], [5.4, 6.0, "börjar"]]
    w.close()


def test_read_best_applies_the_newest_revision_per_line(tmp_path):
    w = make(tmp_path)
    revise(w, 1, "hej allihopa")
    revise(w, 1, "hej allihopa och välkomna")
    revise(w, 3, "tack för i dag", avg_logprob=-0.1)
    w.close()
    best = transcript.read_best(w.dir)
    assert [s.text for s in best] == [
        "hej allihopa och välkomna", "nu börjar vi", "tack för i dag"]
    assert [s.rev for s in best] == [2, None, 3]
    assert best[0].words == [[0.0, 1.0, "hej"]]  # no new words in the revision: draft's stay
    assert best[2].avg_logprob == -0.1
    assert [s.seq for s in best] == [1, 2, 3]  # same lines, same order, same speakers
    assert {s.speaker for s in best} == {"Me"}
    assert [s.seq for s in transcript.read_best(w.dir, after_seq=1)] == [2, 3]
    drafts = transcript.read_segments(w.dir)
    assert [s.text for s in drafts] == ["hej alla", "nu börjar vi", "tack för idag"]


def test_unknown_seq_and_empty_text_are_not_written(tmp_path):
    w = make(tmp_path)
    assert w.append_revisions([(99, "ghost", {}), (1, "   ", {})]) == []
    assert not (w.dir / "revisions.jsonl").exists()
    w.close()


def test_rev_is_not_a_json_field_of_a_segment(tmp_path):
    w = make(tmp_path)
    revise(w, 1, "hej")
    w.close()
    best = transcript.read_best(w.dir)[0]
    assert best.rev == 1 and '"rev"' not in best.to_json()
    assert transcript.Segment.from_dict(json.loads(best.to_json())).rev is None


def test_reader_ignores_partial_and_corrupt_revision_lines(tmp_path):
    w = make(tmp_path)
    revise(w, 1, "hej")
    w.close()
    path = w.dir / "revisions.jsonl"
    with path.open("ab") as fh:
        fh.write(b"not json\n")
        fh.write(b'{"v":1,"rev":2,"seq":2}\n')  # no text
        fh.write(b'{"v":1,"rev":3,"seq":3,"text":"par')  # partial line, no newline
    assert [r.rev for r in transcript.read_revisions(w.dir)] == [1]
    assert transcript.read_best(w.dir)[2].text == "tack för idag"


def test_old_sessions_without_revisions_read_as_before(tmp_path):
    w = make(tmp_path)
    w.close()
    assert transcript.read_revisions(w.dir) == []
    assert transcript.read_best(w.dir) == transcript.read_segments(w.dir)
    assert transcript.read_best(w.dir, name="final.jsonl") == []


def test_markdown_mirror_shows_the_best_text_and_keeps_appending(tmp_path):
    w = make(tmp_path)
    revise(w, 2, "nu börjar vi på riktigt")
    md = (w.dir / "transcript.md").read_text(encoding="utf-8")
    assert "nu börjar vi på riktigt" in md and "nu börjar vi\n" not in md
    assert md.count("**[") == 3
    w.append(t_start=20.0, t_end=22.0, source="mic", speaker="Me", text="sista raden", lang="sv")
    revise(w, 4, "sista raden alltså")
    w.close()
    md = (w.dir / "transcript.md").read_text(encoding="utf-8")
    assert md.count("**[") == 4 and "sista raden alltså" in md
    assert labels.regenerate_markdown(w.dir)  # offline regeneration also uses the best text
    assert "nu börjar vi på riktigt" in (w.dir / "transcript.md").read_text(encoding="utf-8")


def test_same_words_ignores_case_punctuation_and_spacing():
    assert transcript.same_words("Hej, alla!", "hej  alla")
    assert not transcript.same_words("hej alla", "hej alla där")
    assert transcript.same_words("API:et funkar.", "api et funkar")


# --------------------------------------------------------------------------- steno new


def get(w, consumer="claude", peek=False):
    return transcript.fetch_new_best(w.dir, consumer, peek=peek)


def texts(segs):
    return [s.text for s in segs]


def test_new_lines_arrive_once_with_the_best_text_known_at_that_time(tmp_path):
    w = make(tmp_path)
    revise(w, 1, "hej allihopa")  # refined before the consumer asks
    first = get(w)
    assert texts(first.new) == ["hej allihopa", "nu börjar vi", "tack för idag"]
    assert first.corrections == []
    again = get(w)
    assert not again and again.new == [] and again.corrections == []
    w.close()


def test_a_line_that_was_delivered_as_draft_comes_back_once_as_a_correction(tmp_path):
    w = make(tmp_path)
    assert len(get(w).new) == 3
    revise(w, 2, "nu börjar vi mötet")
    out = get(w)
    assert out.new == [] and texts(out.corrections) == ["nu börjar vi mötet"]
    assert out.corrections[0].seq == 2
    assert not get(w)  # delivered: never again
    w.close()


def test_a_revision_that_only_changes_case_or_punctuation_is_not_delivered(tmp_path):
    w = make(tmp_path)
    get(w)
    revise(w, 1, "Hej, alla.")
    assert not get(w)
    assert transcript.read_cursor_state(w.dir, "claude")[1] == 1  # but it is accounted for
    w.close()


def test_new_lines_and_corrections_in_one_call_and_no_duplicate_of_a_line_refined_early(tmp_path):
    w = make(tmp_path)
    get(w)
    w.append(t_start=20.0, t_end=22.0, source="mic", speaker="Me", text="fjärde raden", lang="sv")
    revise(w, 4, "fjärde raden i listan")  # new line, already refined: delivered once, refined
    revise(w, 1, "hej allihopa och välkomna")
    out = get(w)
    assert texts(out.new) == ["fjärde raden i listan"]
    assert texts(out.corrections) == ["hej allihopa och välkomna"]
    assert not get(w)
    w.close()


def test_a_second_revision_of_a_delivered_line_is_compared_with_what_was_shown(tmp_path):
    w = make(tmp_path)
    revise(w, 1, "hej allihopa")
    assert texts(get(w).new)[0] == "hej allihopa"  # delivered refined
    revise(w, 1, "Hej allihopa!")  # same words as shown: nothing to say
    assert not get(w)
    revise(w, 1, "hej allihopa och välkomna")
    assert texts(get(w).corrections) == ["hej allihopa och välkomna"]
    w.close()


def test_peek_changes_nothing_and_consumers_are_independent(tmp_path):
    w = make(tmp_path)
    assert len(get(w, peek=True).new) == 3
    assert len(get(w, peek=True).new) == 3
    assert len(get(w, "other").new) == 3
    assert len(get(w, "claude").new) == 3
    revise(w, 3, "tack för i dag allihopa")
    assert texts(get(w, peek=True).corrections) == ["tack för i dag allihopa"]
    assert texts(get(w, "claude").corrections) == ["tack för i dag allihopa"]
    assert texts(get(w, "other").corrections) == ["tack för i dag allihopa"]  # own cursor
    w.close()


def test_cursor_file_stays_compatible_with_old_readers_and_writers(tmp_path):
    w = make(tmp_path)
    get(w)
    path = w.dir / "cursors" / "claude.json"
    assert json.loads(path.read_text())["last_seq"] == 3  # plain last_seq, as before
    revise(w, 1, "hej allihopa och välkomna")
    get(w)
    assert json.loads(path.read_text()) == {"last_seq": 3, "last_rev": 1}
    assert transcript.read_cursor(w.dir, "claude") == 3  # the old API still answers
    path.write_text('{"last_seq": 2}\n')  # a cursor written before revisions existed
    assert transcript.read_cursor_state(w.dir, "claude") == (2, 0)
    transcript.write_cursor(w.dir, "claude", 3)  # an old-style writer keeps the rev position
    transcript.write_cursor(w.dir, "claude", 3, last_rev=1)
    transcript.write_cursor(w.dir, "claude", 3)
    assert transcript.read_cursor_state(w.dir, "claude") == (3, 1)
    w.close()


def test_the_plain_fetch_new_still_gives_drafts_and_leaves_the_rev_position(tmp_path):
    w = make(tmp_path)
    revise(w, 1, "hej allihopa")
    got = transcript.fetch_new(w.dir, "legacy")
    assert texts(got) == ["hej alla", "nu börjar vi", "tack för idag"]
    w.close()


def test_resolve_hook_is_applied_to_both_lists(tmp_path):
    w = make(tmp_path)
    get(w)
    revise(w, 1, "hej allihopa och välkomna")
    out = transcript.fetch_new_best(
        w.dir, "claude",
        resolve=lambda d, segs: [s.__class__(**{**s.__dict__, "speaker": "Anna"}) for s in segs])
    assert [s.speaker for s in out.corrections] == ["Anna"]
    w.close()


# --------------------------------------------------------------------------- CLI


def test_cli_new_prints_new_lines_then_marked_corrections(tmp_path, capsys, monkeypatch):
    root = transcript.paths.sessions_dir()
    w = SessionWriter.create(root=root, model="fake", sources=["mic"])
    for i, t in enumerate(["a b", "c d"]):
        w.append(t_start=i * 5.0, t_end=i * 5.0 + 2, source="mic", speaker="Me", text=t, lang="sv")
    assert cli.main(["new"]) == 0
    first = capsys.readouterr().out
    assert "Me:** a b" in first and "Corrections" not in first
    revise(w, 1, "a b better")
    w.append(t_start=30.0, t_end=32.0, source="mic", speaker="Me", text="e f", lang="sv")
    assert cli.main(["new"]) == 0
    out = capsys.readouterr().out
    assert out.index("e f") < out.index("Corrections") < out.index("a b better")
    assert cli.main(["new"]) == 0 and capsys.readouterr().out == ""
    w.close()


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", ""])
def test_invalid_consumer_names_are_still_refused(tmp_path, name):
    w = make(tmp_path)
    with pytest.raises(ValueError):
        transcript.fetch_new_best(w.dir, name)
    w.close()
