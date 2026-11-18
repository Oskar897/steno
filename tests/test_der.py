"""DER on hand-made toy cases (fake labels, no audio)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from steno import der

REF = [(0.0, 10.0, "A"), (10.0, 20.0, "B")]


def d(ref, hyp, **kw):
    kw.setdefault("collar", 0.0)
    return der.compute_der(ref, hyp, **kw)


def test_perfect_with_renamed_labels():
    r = d(REF, [(0.0, 10.0, "x"), (10.0, 20.0, "y")])
    assert r.der == pytest.approx(0.0)
    assert (r.n_ref, r.n_hyp) == (2, 2)
    assert r.mapping == {"x": "A", "y": "B"}
    assert r.ref_speech_s == pytest.approx(20.0)


def test_swapped_labels_cost_nothing_but_partial_swap_is_confusion():
    assert d(REF, [(0.0, 10.0, "B"), (10.0, 20.0, "A")]).der == pytest.approx(0.0)
    # last 2 s of A's turn attributed to the other hypothesis speaker
    r = d(REF, [(0.0, 8.0, "x"), (8.0, 20.0, "y")])
    assert r.confusion == pytest.approx(2 / 20)
    assert r.miss == 0 and r.false_alarm == 0


def test_missed_speech():
    r = d(REF, [(0.0, 10.0, "x"), (10.0, 15.0, "y")])
    assert r.miss == pytest.approx(5 / 20)
    assert r.false_alarm == 0 and r.confusion == 0
    assert r.der == pytest.approx(0.25)


def test_false_alarm_outside_reference_speech():
    ref = [(0.0, 10.0, "A")]
    r = d(ref, [(0.0, 10.0, "x"), (10.0, 15.0, "x")])
    assert r.false_alarm == pytest.approx(5 / 10)
    assert r.miss == 0 and r.confusion == 0


def test_extra_speaker_is_confusion_for_the_split_part():
    ref = [(0.0, 20.0, "A")]
    r = d(ref, [(0.0, 15.0, "x"), (15.0, 20.0, "y")])
    assert r.n_ref == 1 and r.n_hyp == 2
    assert r.confusion == pytest.approx(5 / 20)
    assert r.der == pytest.approx(0.25)


def test_merged_speakers_are_confusion():
    r = d(REF, [(0.0, 20.0, "x")])
    assert r.n_hyp == 1
    assert r.confusion == pytest.approx(10 / 20)


def test_overlap_is_scored_and_can_be_skipped():
    ref = [(0.0, 10.0, "A"), (5.0, 10.0, "B")]  # 5 s overlap
    hyp = [(0.0, 10.0, "x")]
    r = d(ref, hyp)
    assert r.ref_speech_s == pytest.approx(15.0)
    assert r.miss == pytest.approx(5 / 15)  # B is never found in the overlap
    skipped = d(ref, hyp, skip_overlap=True)
    assert skipped.ref_speech_s == pytest.approx(5.0)
    assert skipped.der == pytest.approx(0.0)


def test_collar_forgives_boundary_errors():
    hyp = [(0.0, 10.2, "x"), (10.2, 20.0, "y")]  # boundary 0.2 s late
    assert d(REF, hyp).confusion == pytest.approx(0.2 / 20)
    r = d(REF, hyp, collar=0.25)
    assert r.der == pytest.approx(0.0)
    # the collar also removes 0.25 s inside each reference turn at every boundary
    assert r.ref_speech_s == pytest.approx(20.0 - 4 * 0.25)
    assert d(REF, hyp, collar=0.1).confusion > 0


def test_collar_default_is_a_quarter_second():
    hyp = [(0.0, 10.2, "x"), (10.2, 20.0, "y")]
    assert der.compute_der(REF, hyp).der == pytest.approx(0.0)


def test_uem_limits_scoring_window():
    r = d(REF, [(0.0, 10.0, "x")], uem=(0.0, 10.0))
    assert r.der == pytest.approx(0.0)
    r = d(REF, [(0.0, 10.0, "x"), (12.0, 14.0, "x")], uem=(0.0, 10.0))
    assert r.false_alarm == 0


def test_empty_hypothesis_is_all_miss():
    r = d(REF, [])
    assert r.miss == pytest.approx(1.0) and r.n_hyp == 0 and r.mapping == {}


def test_empty_reference_is_an_error():
    with pytest.raises(ValueError):
        d([], [(0.0, 1.0, "x")])


def test_hungarian_matches_brute_force():
    rng = np.random.default_rng(0)
    for rows, cols in [(3, 3), (2, 5), (5, 2), (4, 4), (1, 3)]:
        w = rng.integers(0, 20, size=(rows, cols)).astype(float)
        pairs = der.hungarian_max(w)
        assert len(pairs) == min(rows, cols)
        got = sum(w[r, c] for r, c in pairs)
        k = min(rows, cols)
        best = max(
            sum(w[r, c] for r, c in zip(rs, cs, strict=True))
            for rs in itertools.combinations(range(rows), k)
            for cs in itertools.permutations(range(cols), k)
        )
        assert got == pytest.approx(best)


def test_rttm_roundtrip_and_parse(tmp_path):
    turns = [(1.0, 2.5, "spk1"), (0.0, 1.0, "spk2")]
    path = tmp_path / "t.rttm"
    der.write_rttm(path, turns, file_id="fake01")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("SPEAKER fake01 1 0.000 1.000 <NA> <NA> spk2 <NA> <NA>")
    back = der.read_rttm(path)
    assert back == {"fake01": [(0.0, 1.0, "spk2"), (1.0, 2.5, "spk1")]}


def test_read_rttm_ignores_other_lines_and_zero_durations(tmp_path):
    path = tmp_path / "t.rttm"
    path.write_text(
        "; comment\nSPEAKER f 1 0.0 0.0 <NA> <NA> a <NA> <NA>\n"
        "SPEAKER f 1 2.0 1.0 <NA> <NA> a <NA> <NA>\nLEXEME f 1 0 1 x\n",
        encoding="utf-8",
    )
    assert der.read_rttm(path) == {"f": [(2.0, 3.0, "a")]}


def test_crop_turns():
    assert der.crop_turns([(0, 5, "a"), (8, 12, "a"), (20, 30, "b")], 2, 10) == [
        (2, 5, "a"),
        (8, 10, "a"),
    ]
