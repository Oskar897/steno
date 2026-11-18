"""Pure-helper tests for scripts/benchmark_asr.py (no model downloads)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_asr.py"
_spec = importlib.util.spec_from_file_location("benchmark_asr", _PATH)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


def test_normalize_keeps_swedish_letters_and_strips_punctuation():
    assert bench.normalize_text("  Är det   ÅTTA, eller Ö?! ") == "är det åtta eller ö"


def test_normalize_apostrophes_and_hyphens():
    assert bench.normalize_text("Don't e-post.") == "dont e post"


def test_error_rates_identical_after_normalization():
    wer, cer = bench.error_rates("Hej, världen!", "hej världen")
    assert wer == 0.0
    assert cer == 0.0


def test_error_rates_one_wrong_word():
    wer, cer = bench.error_rates("a b c d", "a b x d")
    assert wer == pytest.approx(0.25)
    assert 0 < cer < 0.25


def test_error_rates_empty_hypothesis_is_total_error():
    assert bench.error_rates("hej där", "...") == (1.0, 1.0)


def test_error_rates_empty_reference_raises():
    with pytest.raises(ValueError):
        bench.error_rates("?!", "hej")


def test_rtf():
    assert bench.real_time_factor(5, 10) == 0.5
    assert bench.real_time_factor(1, 0) == float("inf")


def _row(run, wer, rtf, error=None):
    return {"run": run, "wer": wer, "rtf": rtf, "error": error}


def test_recommend_lowest_wer_under_rtf_limit():
    rows = [
        _row("big", 0.05, 0.9),  # most accurate but too slow
        _row("mid", 0.10, 0.4),
        _row("small", 0.20, 0.1),
        _row("broken", None, None, error="crash"),
    ]
    best, why = bench.recommend(rows)
    assert best["run"] == "mid"
    assert "RTF" in why


def test_recommend_tie_breaks_on_rtf():
    best, _ = bench.recommend([_row("a", 0.1, 0.4), _row("b", 0.1, 0.2)])
    assert best["run"] == "b"


def test_recommend_falls_back_to_fastest_when_none_fast_enough():
    best, why = bench.recommend([_row("a", 0.1, 1.2), _row("b", 0.3, 0.8)])
    assert best["run"] == "b"
    assert "fastest" in why


def test_recommend_without_reference():
    best, why = bench.recommend([_row("a", None, 0.2)])
    assert best is None
    assert "--reference" in why


def test_sort_rows_by_wer_then_failures_last():
    rows = [
        _row("nower", None, 0.1),
        _row("bad", None, None, error="x"),
        _row("b", 0.3, 0.5),
        _row("a", 0.1, 0.9),
    ]
    assert [r["run"] for r in bench.sort_rows(rows)] == ["a", "b", "nower", "bad"]


def test_sort_rows_without_reference_uses_rtf():
    rows = [_row("slow", None, 0.9), _row("fast", None, 0.1)]
    assert [r["run"] for r in bench.sort_rows(rows)] == ["fast", "slow"]


def test_merge_spans_respects_max_len():
    spans = [(0, 10), (12, 20), (22, 40), (100, 105)]
    assert bench.merge_spans(spans, 25) == [(0, 20), (22, 40), (100, 105)]


def test_fixed_spans_cover_everything():
    spans = bench.fixed_spans(25, 10)
    assert spans == [(0, 10), (10, 20), (20, 25)]


def test_format_table_contains_failure_and_rows():
    rows = [
        {
            "run": "m[sv]",
            "wer": 0.1234,
            "cer": 0.05,
            "rtf": 0.25,
            "load_s": 1.0,
            "transcribe_s": 2.0,
            "peak_rss_mb": 500.0,
            "detected_language": None,
            "error": None,
        },
        {"run": "x", "error": "boom"},
    ]
    table = bench.format_table(rows)
    assert "12.3%" in table
    assert "FAILED: boom" in table


def test_models_registry_sane():
    assert bench.SELF_TEST_MODEL in bench.MODELS
    assert bench.MODELS["kb-whisper-small"].languages == ("sv", "auto")
    assert bench.MODELS["whisper-large-v3-turbo"].languages == ("sv",)
