from __future__ import annotations

import numpy as np

from steno.windows import (
    Turn,
    Word,
    absorb_short_turns,
    assign_words,
    cut_turns,
    join_words,
    median_filter_labels,
    snap_boundaries,
    split_by_turns,
    window_spans,
)

from .synth import silence, tone

# --------------------------------------------------------------------------- windows


def test_window_spans_regular_grid_and_aligned_tail():
    spans = window_spans(4.0, win=1.5, hop=0.75)
    assert spans[0] == (0.0, 1.5) and spans[1] == (0.75, 2.25)
    assert spans[-1][1] == 4.0  # the tail is never dropped
    assert all(abs((b - a) - 1.5) < 1e-9 for a, b in spans)


def test_window_spans_exact_fit_has_no_extra_tail_window():
    assert window_spans(3.0, 1.5, 0.75) == [(0.0, 1.5), (0.75, 2.25), (1.5, 3.0)]


def test_window_spans_short_segments():
    assert window_spans(0.9, 1.5, 0.75, min_win=1.0) == []  # too short to embed
    assert window_spans(1.2, 1.5, 0.75, min_win=1.0) == [(0.0, 1.2)]  # one shortened window
    assert window_spans(1.5, 1.5, 0.75) == [(0.0, 1.5)]


# --------------------------------------------------------------------------- smoothing


def test_median_filter_removes_isolated_flips_only():
    assert median_filter_labels(list("AAABAAA"), 3) == list("AAAAAAA")
    assert median_filter_labels(list("AABBBAA"), 3) == list("AABBBAA")  # a real turn stays
    assert median_filter_labels(list("ABAB"), 1) == list("ABAB")  # size 1 = off
    assert median_filter_labels(list("AB"), 3) == list("AB")  # too short to filter


def test_median_filter_edge_windows_follow_the_majority():
    out = median_filter_labels(list("ABBBB"), 3)
    assert out[1:] == list("BBBB")


# --------------------------------------------------------------------------- turns


def test_cut_turns_cuts_midway_between_window_centres():
    spans = [(0.0, 1.5), (0.75, 2.25), (1.5, 3.0), (2.25, 3.75)]
    turns = cut_turns(spans, ["A", "A", "B", "B"], 3.75)
    # centres 0.75, 1.5, 2.25, 3.0 -> boundary between window 2 and 3 at 1.875
    assert [(t.label, round(t.t_start, 3), round(t.t_end, 3)) for t in turns] == [
        ("A", 0.0, 1.875), ("B", 1.875, 3.75)
    ]


def test_cut_turns_covers_the_segment_and_merges_equal_neighbours():
    spans = window_spans(6.0)
    turns = cut_turns(spans, ["A"] * len(spans), 6.0)
    assert turns == [Turn(0.0, 6.0, "A")]
    assert cut_turns([], [], 3.0) == []


def test_short_turns_are_absorbed_by_the_longer_neighbour():
    turns = [Turn(0, 4, "A"), Turn(4, 4.3, "B"), Turn(4.3, 6, "C")]
    out = absorb_short_turns(turns, 0.5)
    assert [t.label for t in out] == ["A", "C"]
    assert out[0].t_start == 0 and out[-1].t_end == 6 and out[0].t_end == out[1].t_start
    only = [Turn(0, 0.2, "A")]
    assert absorb_short_turns(only, 0.5) == only  # the only turn is never removed
    same = [Turn(0, 3, "A"), Turn(3, 3.2, "B"), Turn(3.2, 6, "A")]
    assert absorb_short_turns(same, 0.5) == [Turn(0, 6, "A")]  # neighbours merge afterwards


def test_snap_boundaries_moves_the_cut_into_the_pause():
    audio = np.concatenate([tone(2.0, 0.3), silence(0.2), tone(2.0, 0.3, 330.0)])
    turns = [Turn(0.0, 2.35, "A"), Turn(2.35, 4.2, "B")]  # cut 0.15 s late, pause is 2.0-2.2
    out = snap_boundaries(turns, audio)
    assert out[0].t_end != 2.35 and 2.0 <= out[0].t_end <= 2.2
    assert out[0].t_end == out[1].t_start and out[1].t_end == 4.2 and out[0].t_start == 0.0
    assert snap_boundaries([Turn(0, 4, "A")], audio) == [Turn(0, 4, "A")]


# --------------------------------------------------------------------------- words


def words(*spec):
    return [Word(a, b, t) for a, b, t in spec]


def test_assign_words_by_maximum_overlap():
    turns = [Turn(0, 2.0, "A"), Turn(2.0, 3.0, "B"), Turn(3.0, 6.0, "A")]
    ws = words((0.1, 0.5, "hello"), (1.8, 2.3, "straddles"), (2.4, 2.9, "yes"),
               (3.2, 3.8, "ok"))
    parts = assign_words(ws, turns)
    # "straddles" overlaps A for 0.2 s and B for 0.3 s -> goes to B
    assert [[w.text for w in p] for p in parts] == [["hello"], ["straddles", "yes"], ["ok"]]


def test_assign_words_outside_every_turn_goes_to_nearest():
    turns = [Turn(0, 1, "A"), Turn(1, 2, "B")]
    ws = words((2.4, 2.6, "late"), (-0.5, -0.3, "early"), (0.5, 0.5, "instant"))
    parts = assign_words(ws, turns)
    assert [w.text for w in parts[1]] == ["late"]
    assert [w.text for w in parts[0]] == ["early", "instant"]
    assert assign_words(ws, []) == []


def test_split_by_turns_variants():
    turns = [Turn(0, 3, "A"), Turn(3, 4, "B"), Turn(4, 8, "A")]
    ws = words((0.2, 0.8, "one"), (3.1, 3.6, "mm"), (5.0, 5.4, "two"))
    assert split_by_turns("one mm two", ws, turns) == [(0, "one"), (1, "mm"), (2, "two")]
    # a turn without any word produces no line
    assert split_by_turns("x", words((0.2, 0.8, "one")), turns) == [(0, "one")]
    # no word timestamps: everything goes to the longest turn
    assert split_by_turns("one mm two", None, turns) == [(2, "one mm two")]
    assert split_by_turns("hi", ws, [Turn(0, 5, "A")]) == [(0, "hi")]
    assert split_by_turns("hi", ws, []) == []
    assert join_words(words((0, 1, "a"), (1, 2, ""), (2, 3, "b"))) == "a b"
