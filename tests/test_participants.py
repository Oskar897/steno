"""participants.json: reading, writing and the finalize side (all names and voices are fake)."""

from __future__ import annotations

import json

from steno import finalize as fz
from steno import participants
from steno.participants import Participants
from steno.profiles import ProfileStore

from .synth import voice_vector
from .test_finalize import make_session
from .test_finalize_refined import FAST, add_windows, no_audio_deps

A1, B2 = "0a1b2c3d", "4e5f6a7b"


def test_round_trip_cleans_the_content(tmp_path):
    assert participants.read(tmp_path) == Participants()  # no file: everybody
    assert not participants.exists(tmp_path)
    written = participants.write(tmp_path, Participants((A1, A1, "not-an-id", B2), 4))
    assert written == Participants((A1, B2), 4)
    assert participants.read(tmp_path) == written
    data = json.loads((tmp_path / participants.NAME).read_text(encoding="utf-8"))
    assert data == {"v": 1, "profiles": [A1, B2], "count": 4}  # ids and a number: no biometrics


def test_damaged_or_odd_files_read_as_empty(tmp_path):
    path = tmp_path / participants.NAME
    for text in ("", "[1, 2]", "{broken", json.dumps({"profiles": "x", "count": "3"})):
        path.write_text(text, encoding="utf-8")
        assert participants.read(tmp_path) == Participants()
    path.write_text(json.dumps({"profiles": [A1], "count": 0}), encoding="utf-8")
    assert participants.read(tmp_path) == Participants((A1,), None)  # 0 is not a count
    path.write_text(json.dumps({"profiles": [A1], "count": True}), encoding="utf-8")
    assert participants.read(tmp_path).count is None


def test_update_adds_removes_and_sets_the_count(tmp_path):
    assert participants.update(tmp_path, add=A1).profiles == (A1,)
    assert participants.update(tmp_path, add=A1).profiles == (A1,)  # no duplicates
    cur = participants.update(tmp_path, add=B2, count=3)
    assert cur == Participants((A1, B2), 3)
    assert participants.update(tmp_path, remove=A1) == Participants((B2,), 3)  # count stays
    assert participants.update(tmp_path, clear_count=True) == Participants((B2,), None)


def test_signature_changes_with_the_file(tmp_path):
    assert participants.signature(tmp_path) is None
    participants.write(tmp_path, Participants((A1,)))
    first = participants.signature(tmp_path)
    participants.update(tmp_path, add=B2)
    assert participants.signature(tmp_path) not in (None, first)


def test_restrict_keeps_everybody_without_a_list():
    class P:
        def __init__(self, id):
            self.id = id

    people = [P(A1), P(B2)]
    assert participants.restrict(people, Participants()) == people
    assert [p.id for p in participants.restrict(people, Participants((B2,)))] == [B2]
    assert participants.restrict(people, Participants((), 3)) == people  # a count alone: all


# --------------------------------------------------------------------------- finalize


def two_voice_session(tmp_path):
    """Session with voice 0 (named by hand in the live run) and voice 1 (an anonymous cluster)."""
    session = make_session(tmp_path)
    (session / "audio-mic.flac").unlink()
    add_windows(session, [(0.5, 8.5, 0), (11.5, 18.5, 1), (21.5, 27.5, 0)])
    return session


def speaker_names(session):
    return {s.speaker for s in fz.read_final(session)}


def enrol(name, voice):
    return ProfileStore().create(name, [voice_vector(voice, 0.05, s) for s in range(4)])


def test_finalize_matches_every_profile_without_a_participant_list(tmp_path):
    session = two_voice_session(tmp_path)
    enrol("Bob", 1)
    fz.finalize_session(session, FAST, ProfileStore(), deps=no_audio_deps())
    assert "Bob" in speaker_names(session)


def test_finalize_only_matches_the_participants(tmp_path):
    session = two_voice_session(tmp_path)
    bob = enrol("Bob", 1)
    dave = enrol("Dave", 5)
    participants.write(session, Participants((dave.id,)))  # Bob is not in this meeting
    fz.finalize_session(session, FAST, ProfileStore(), deps=no_audio_deps())
    assert "Bob" not in speaker_names(session)
    participants.update(session, add=bob.id)
    fz.finalize_session(session, FAST, ProfileStore(), deps=no_audio_deps())
    assert "Bob" in speaker_names(session)


def final_meta(session):
    return json.loads((session / "final.json").read_text(encoding="utf-8"))


def test_finalize_uses_the_expected_count_unless_one_is_given(tmp_path):
    session = two_voice_session(tmp_path)
    participants.write(session, Participants((), 1))
    fz.finalize_session(session, FAST, ProfileStore(), deps=no_audio_deps())
    assert final_meta(session)["num_speakers"] == 1
    fz.finalize_session(session, FAST, ProfileStore(), deps=no_audio_deps(), num_speakers=2)
    assert final_meta(session)["num_speakers"] == 2  # an explicit number wins


def test_finalize_without_a_file_has_no_forced_count(tmp_path):
    session = two_voice_session(tmp_path)
    fz.finalize_session(session, FAST, ProfileStore(), deps=no_audio_deps())
    assert final_meta(session)["num_speakers"] is None
