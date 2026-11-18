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


def test_models_registry_has_new_candidates():
    for key in ("kb-whisper-medium", "kb-whisper-large", "qwen3-asr-0.6b"):
        assert key in bench.MODELS
    assert bench.MODELS["qwen3-asr-0.6b"].kind == "sherpa-qwen3"


def test_corpus_error_rates_sums_errors_over_utterances():
    refs = ["a b c d", "e f"]
    hyps = ["a b c d", "x"]  # 2 errors (1 sub + 1 del) over 6 reference words
    wer, cer = bench.corpus_error_rates(refs, hyps)
    assert wer == pytest.approx(2 / 6)
    assert 0 < cer < 1


def test_corpus_error_rates_empty_hypothesis_and_empty_reference():
    wer, _ = bench.corpus_error_rates(["a b", "?!"], ["", "x"])
    assert wer == 1.0  # empty reference dropped, empty hypothesis = all deleted
    with pytest.raises(ValueError):
        bench.corpus_error_rates(["?!"], ["x"])
    with pytest.raises(ValueError):
        bench.corpus_error_rates(["a"], [])


def test_parse_model_arg():
    assert bench.parse_model_arg("kb-whisper-small") == ("kb-whisper-small", None)
    assert bench.parse_model_arg(" kb-whisper-small@5 ") == ("kb-whisper-small", 5)
    assert bench.run_label("m", 5) == "m@5"
    assert bench.run_label("m", None) == "m"
    for bad in ("m@", "m@x", "m@0"):
        with pytest.raises(ValueError):
            bench.parse_model_arg(bad)


_TSV = (
    "1\ta.wav\tHej där.\thej där\tx\t80000\tMALE\n"  # 5.0 s
    "2\tb.wav\tHej  där!\thej där\tx\t96000\tFEMALE\n"  # duplicate sentence
    "3\tc.wav\tEtt annat.\tett annat\tx\t16000\tMALE\n"  # 1 s: too short
    "4\td.wav\tTredje.\ttredje\tx\t160000\tMALE\n"  # 10 s
    "bad line\n"
)


def test_parse_fleurs_tsv_skips_malformed_lines():
    rows = bench.parse_fleurs_tsv(_TSV)
    assert [r["id"] for r in rows] == ["1", "2", "3", "4"]
    assert rows[0]["seconds"] == 5.0
    assert rows[0]["text"] == "Hej där."


def test_select_fleurs_rows_dedupes_filters_and_is_reproducible():
    rows = bench.parse_fleurs_tsv(_TSV)
    picked = bench.select_fleurs_rows(rows, 10)
    assert sorted(r["id"] for r in picked) == ["1", "4"]  # dup + too short removed
    assert picked == bench.select_fleurs_rows(rows, 10)
    many = [
        {"id": str(i), "file": f"{i}.wav", "text": f"sentence {i}", "seconds": 8.0}
        for i in range(50)
    ]
    big, small = bench.select_fleurs_rows(many, 20), bench.select_fleurs_rows(many, 10)
    assert big[:10] == small  # n=10 is a prefix of n=20


def test_dominant_language():
    assert bench.dominant(["sv", "en", "sv", None]) == "sv"
    assert bench.dominant([None]) is None


def test_expand_sets_dataset_mode_forces_language_only_for_whisper():
    jobs = [{"name": "en", "language": "en", "audio": ["x.wav"], "refs": ["a"]}]
    whisper = bench.expand_sets(bench.MODELS["kb-whisper-small"], jobs, True)
    sherpa = bench.expand_sets(bench.MODELS["parakeet-tdt-0.6b-v3"], jobs, True)
    assert whisper[0]["language"] == "en"
    assert sherpa[0]["language"] == "auto"


def test_expand_sets_single_file_tries_each_language():
    jobs = [{"name": "file", "language": "auto", "audio": ["x.wav"], "refs": None}]
    sets = bench.expand_sets(bench.MODELS["kb-whisper-small"], jobs, False)
    assert [s["name"] for s in sets] == ["sv", "auto"]


def test_build_rows_scores_each_set_against_its_references():
    raw = {
        "download_s": 0.0,
        "load_s": 1.0,
        "runs": [
            {
                "set": "sv",
                "language": "sv",
                "detected_language": None,
                "audio_s": 10.0,
                "transcribe_s": 2.0,
                "peak_rss_mb": 300.0,
                "hypotheses": ["hej där", "ett två"],
            }
        ],
    }
    rows, hyps = bench.build_rows(raw, "m@5", 5, {"sv": ["hej där", "ett tre"]})
    assert hyps == {"m@5[sv]": ["hej där", "ett två"]}
    assert rows[0]["rtf"] == pytest.approx(0.2)
    assert rows[0]["wer"] == pytest.approx(1 / 4)
    assert rows[0]["beam"] == 5


def test_format_dataset_table_combines_sets_per_model():
    def row(model, set_name, wer, rtf):
        return {
            "run": f"{model}[{set_name}]",
            "model": model,
            "set": set_name,
            "wer": wer,
            "cer": wer / 2,
            "rtf": rtf,
            "peak_rss_mb": 800.0,
            "load_s": 2.0,
            "error": None,
        }

    rows = [
        row("a", "sv", 0.1, 0.3),
        row("a", "en", 0.5, 0.4),
        {"run": "b", "model": "b", "error": "boom"},
    ]
    table = bench.format_dataset_table(rows)
    assert "| a | 10.0% | 5.0% | 50.0% | 25.0% | 0.300 | 0.400 | 800 | 2.0 |  |" in table
    assert "FAILED: boom" in table


def test_expand_sets_language_override_only_for_whisper():
    jobs = [{"name": "en", "language": "en", "audio": ["x.wav"], "refs": ["a"]}]
    whisper = bench.expand_sets(bench.MODELS["kb-whisper-small"], jobs, True, "auto")
    sherpa = bench.expand_sets(bench.MODELS["parakeet-tdt-0.6b-v3"], jobs, True, "auto")
    assert whisper[0]["language"] == "auto"
    assert whisper[0]["expected_language"] == "en"
    assert sherpa[0]["language"] == "auto"


def test_language_match_share():
    assert bench.language_match(["en", "sv", "en", "en"], "en") == 0.75
    assert bench.language_match(None, "en") is None
    assert bench.language_match(["en"], None) is None


def test_format_dataset_table_notes_language_detection():
    rows = [
        {
            "run": "a:auto[en]",
            "model": "a:auto",
            "set": "en",
            "wer": 0.2,
            "cer": 0.1,
            "rtf": 0.3,
            "peak_rss_mb": 800.0,
            "load_s": 2.0,
            "language_match": 0.9,
            "error": None,
        }
    ]
    assert "en language detected correctly in 90%" in bench.format_dataset_table(rows)
