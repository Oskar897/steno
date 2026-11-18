from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime

import pytest

from steno import paths, transcript
from steno.transcript import SessionWriter


def make_writer(**kw):
    return SessionWriter.create(
        model="fake", sources=["mic"], now=datetime(2026, 1, 1, 10, 0, 0), **kw
    )


def add(w, text="hej", t=0.0, source="mic", speaker="Me"):
    return w.append(t_start=t, t_end=t + 1, source=source, speaker=speaker, text=text, lang="sv")


def test_session_layout_and_id():
    w = make_writer()
    assert w.dir.parent == paths.sessions_dir()
    assert w.session_id == "20260101-100000"
    assert (w.dir / "cursors").is_dir()
    meta = json.loads((w.dir / "session.json").read_text(encoding="utf-8"))
    assert meta["started_at"] == "2026-01-01T10:00:00"
    assert meta["ended_at"] is None
    assert meta["model"] == "fake"
    assert meta["sources"] == ["mic"]
    w.close()


def test_session_id_collision_bumps_second():
    a, b = make_writer(), make_writer()
    assert a.session_id == "20260101-100000"
    assert b.session_id == "20260101-100001"
    a.close()
    b.close()


def test_jsonl_matches_format_spec():
    w = make_writer()
    add(w, "Ska vi ta det på fredag?", t=12.4, source="loopback", speaker="Remote")
    w.close()
    line = (w.dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[0]
    data = json.loads(line)
    assert list(data) == [
        "v", "seq", "t_start", "t_end", "wall", "source", "speaker", "text", "lang"
    ]
    assert data["v"] == 1 and data["seq"] == 1
    assert data["wall"] == "2026-01-01T10:00:12"
    assert data["text"] == "Ska vi ta det på fredag?"  # not \u-escaped
    assert line.startswith('{"v":1,"seq":1,')  # compact separators


def test_markdown_mirror_and_clock():
    w = make_writer()
    add(w, "första", t=12.0)
    add(w, "senare", t=3725.0, speaker="Remote")
    w.close()
    md = (w.dir / "transcript.md").read_text(encoding="utf-8")
    assert "**[00:12] Me:** första" in md
    assert "**[1:02:05] Remote:** senare" in md


def test_close_writes_ended_at_and_count():
    w = make_writer()
    add(w)
    w.close()
    w.close()  # idempotent
    meta = json.loads((w.dir / "session.json").read_text(encoding="utf-8"))
    assert meta["ended_at"] is not None
    assert meta["segments"] == 1
    with pytest.raises(ValueError):
        add(w)


def test_seq_strictly_increasing_across_threads():
    w = make_writer()

    def worker():
        for i in range(25):
            add(w, f"t{i}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    w.close()
    seqs = [s.seq for s in transcript.read_segments(w.dir)]
    assert seqs == list(range(1, 101))


def test_reader_ignores_trailing_partial_line():
    w = make_writer()
    add(w, "ett")
    add(w, "två")
    with (w.dir / "transcript.jsonl").open("ab") as fh:
        fh.write(b'{"v":1,"seq":3,"t_start":0,"t_end":1,"wall":"x"')  # torn write
    segs = transcript.read_segments(w.dir)
    assert [s.text for s in segs] == ["ett", "två"]
    w.close()


def test_reader_ignores_complete_json_without_newline():
    w = make_writer()
    add(w, "ett")
    s = transcript.Segment(2, 0, 1, "w", "mic", "Me", "ofullständig", "sv")
    with (w.dir / "transcript.jsonl").open("ab") as fh:
        fh.write(s.to_json().encode())  # no trailing newline yet
    assert [x.seq for x in transcript.read_segments(w.dir)] == [1]
    w.close()


def test_reader_skips_corrupt_middle_line():
    w = make_writer()
    add(w, "ett")
    with (w.dir / "transcript.jsonl").open("ab") as fh:
        fh.write(b"not json\n")
    add(w, "två")
    assert [s.text for s in transcript.read_segments(w.dir)] == ["ett", "två"]
    w.close()


def test_cursor_advance_and_peek():
    w = make_writer()
    add(w, "a")
    add(w, "b")
    assert transcript.read_cursor(w.dir, "claude") == 0

    peeked = transcript.fetch_new(w.dir, "claude", peek=True)
    assert [s.text for s in peeked] == ["a", "b"]
    assert transcript.read_cursor(w.dir, "claude") == 0  # peek does not advance

    got = transcript.fetch_new(w.dir, "claude")
    assert [s.text for s in got] == ["a", "b"]
    assert transcript.read_cursor(w.dir, "claude") == 2
    assert transcript.fetch_new(w.dir, "claude") == []

    add(w, "c")
    assert [s.text for s in transcript.fetch_new(w.dir, "claude")] == ["c"]
    # other consumers are independent
    assert len(transcript.fetch_new(w.dir, "other")) == 3
    w.close()


def test_cursor_file_format_and_no_temp_left():
    w = make_writer()
    add(w)
    transcript.fetch_new(w.dir, "claude")
    files = sorted(p.name for p in (w.dir / "cursors").iterdir())
    assert files == ["claude.json"]
    cursor = json.loads((w.dir / "cursors" / "claude.json").read_text(encoding="utf-8"))
    assert cursor == {"last_seq": 1}
    w.close()


def test_cursor_corrupt_file_treated_as_zero():
    w = make_writer()
    add(w)
    (w.dir / "cursors" / "claude.json").write_text("{broken", encoding="utf-8")
    assert transcript.read_cursor(w.dir, "claude") == 0
    w.close()


def test_invalid_consumer_name_rejected():
    w = make_writer()
    with pytest.raises(ValueError):
        transcript.read_cursor(w.dir, "../evil")
    w.close()


def test_render_markdown_compact():
    segs = [
        transcript.Segment(1, 5, 6, "w", "mic", "Me", "hej", "sv"),
        transcript.Segment(2, 7, 8, "w", "loopback", "Remote", "tjena", "sv"),
    ]
    assert transcript.render_markdown(segs) == "**[00:05] Me:** hej\n**[00:07] Remote:** tjena"


def test_list_and_resolve_sessions():
    assert transcript.list_sessions() == []
    assert transcript.resolve_session("latest") is None
    a = make_writer()
    add(a)
    a.close()
    b = SessionWriter.create(now=datetime(2026, 1, 2, 9, 0, 0))
    infos = transcript.list_sessions()
    assert [i.id for i in infos] == ["20260101-100000", "20260102-090000"]
    assert infos[0].segments == 1 and not infos[0].recording
    assert infos[1].recording
    assert transcript.resolve_session("latest") == b.dir
    assert transcript.resolve_session("20260101-100000") == a.dir
    assert transcript.resolve_session("nope") is None
    assert transcript.resolve_session("../x") is None
    b.close()


def test_optional_speaker_fields_roundtrip_and_stay_out_of_layer1_lines():
    w = make_writer()
    add(w, "plain")  # layer 1: no speaker fields
    w.append(t_start=2, t_end=3, source="mic", speaker="Speaker 1", text="hej", lang="sv",
             speaker_id="S1", speaker_conf=0.712345, provisional=True)
    w.append(t_start=4, t_end=5, source="mic", speaker="Alice", text="tja", lang="sv",
             speaker_id="P:1a2b3c4d", speaker_conf=0.9, provisional=False)
    w.close()
    lines = (w.dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert "speaker_id" not in lines[0]
    second = json.loads(lines[1])
    assert list(second)[-3:] == ["speaker_id", "speaker_conf", "provisional"]
    assert second["speaker_conf"] == 0.712 and second["provisional"] is True

    segs = transcript.read_segments(w.dir)
    assert segs[0].speaker_id is None and segs[0].provisional is None
    assert (segs[1].speaker_id, segs[1].speaker_conf, segs[1].provisional) == ("S1", 0.712, True)
    assert segs[2].speaker_id == "P:1a2b3c4d" and segs[2].provisional is False


def test_fetch_new_can_resolve_segments_for_display():
    w = make_writer()
    add(w, "a")
    w.close()
    seen = transcript.fetch_new(
        w.dir, "claude", resolve=lambda d, segs: [replace(s, speaker="X") for s in segs]
    )
    assert [s.speaker for s in seen] == ["X"]
    assert transcript.read_cursor(w.dir, "claude") == 1


def test_words_and_avg_logprob_roundtrip_as_optional_fields():
    w = make_writer()
    add(w, "plain")
    w.append(t_start=2, t_end=3, source="mic", speaker="Me", text="hej du", lang="sv",
             avg_logprob=-0.34567, words=[[2.004, 2.5, "hej"], [2.5, 2.91234, "du"]])
    w.close()
    lines = (w.dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert "words" not in lines[0] and "avg_logprob" not in lines[0]
    second = json.loads(lines[1])
    assert second["avg_logprob"] == -0.346
    assert second["words"] == [[2.0, 2.5, "hej"], [2.5, 2.91, "du"]]
    segs = transcript.read_segments(w.dir)
    assert segs[0].words is None and segs[0].avg_logprob is None
    assert segs[1].words == [[2.0, 2.5, "hej"], [2.5, 2.91, "du"]] and segs[1].avg_logprob == -0.346


def test_malformed_words_field_is_ignored_not_fatal():
    seg = transcript.Segment.from_dict(
        {"seq": 1, "t_start": 0, "t_end": 1, "wall": "w", "source": "mic", "speaker": "A",
         "text": "x", "lang": "sv", "words": [[0, 1]]}
    )
    assert seg.words is None


def test_read_segments_can_read_final_jsonl_and_flag_roundtrips(tmp_path):
    seg = transcript.Segment(1, 0.0, 1.0, "w", "mic", "A", "x", "sv", final=True)
    (tmp_path / "final.jsonl").write_text(seg.to_json() + "\n", encoding="utf-8")
    assert json.loads(seg.to_json())["final"] is True
    assert transcript.read_segments(tmp_path, name="final.jsonl")[0].final is True
    assert transcript.read_segments(tmp_path) == []  # the live file is a different one


def test_writer_set_meta_lands_in_session_json():
    w = make_writer()
    w.set_meta("stream_origins", {"mic": 0.12})
    w.close()
    meta = json.loads((w.dir / "session.json").read_text(encoding="utf-8"))
    assert meta["stream_origins"] == {"mic": 0.12}
