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


# --------------------------------------------------------------------------- #
# Code-switching strategies: run specs, local datasets, English-term accuracy, LLM fix
# --------------------------------------------------------------------------- #

_SENTENCES = Path(__file__).resolve().parents[1] / "scripts" / "codeswitch_sentences.txt"


def test_parse_run_spec_and_label_roundtrip():
    spec = bench.parse_run_spec("kb-whisper-small")
    assert spec == bench.RunSpec("kb-whisper-small")
    assert spec.label == "kb-whisper-small"
    for text in (
        "kb-whisper-small+prompt",
        "whisper-large-v3-turbo:auto",
        "whisper-large-v3-turbo:sv+prompt",
        "kb-whisper-medium@5:sv+prompt+hot",
    ):
        assert bench.parse_run_spec(text).label == text
    full = bench.parse_run_spec(" kb-whisper-medium@5:sv+prompt+hot ")
    assert (full.key, full.beam, full.language, full.prompt, full.hotwords) == (
        "kb-whisper-medium",
        5,
        "sv",
        True,
        True,
    )


def test_parse_run_spec_rejects_garbage():
    for bad in ("m+nope", "m:", "m:SV", "m:sv:en", "m@x", "m@0+prompt"):
        with pytest.raises(ValueError):
            bench.parse_run_spec(bad)


def test_resolve_fix_labels():
    specs = [bench.parse_run_spec(t) for t in ("a", "a+prompt", "b:auto")]
    assert bench.resolve_fix_labels(None, specs) == frozenset()
    assert bench.resolve_fix_labels([], specs) == frozenset({"a"})
    assert bench.resolve_fix_labels(["b:auto"], specs) == frozenset({"b:auto"})
    with pytest.raises(ValueError):
        bench.resolve_fix_labels(["zzz"], specs)


def test_parse_refs_tsv_skips_comments_and_blank_lines():
    text = "# comment\n01\tHej där\n\n02\t  Vi kör   \n03\t\nnotab\n04\tOk\tstray tab\n"
    assert bench.parse_refs_tsv(text) == [
        ("01", "Hej där"),
        ("02", "Vi kör"),
        ("04", "Ok\tstray tab"),
    ]


def test_load_local_dataset_skips_missing_wavs(tmp_path):
    ds = tmp_path / "cs"
    ds.mkdir()
    (ds / "refs.tsv").write_text("01\tHej\n02\tDå\n03\tTre\n", encoding="utf-8")
    (ds / "01.wav").write_bytes(b"")
    (ds / "03.wav").write_bytes(b"")
    items, missing = bench.load_local_dataset("cs", tmp_path)
    assert [i["id"] for i in items] == ["01", "03"]
    assert [i["text"] for i in items] == ["Hej", "Tre"]
    assert items[0]["audio"].endswith("01.wav")
    assert missing == ["02"]


def test_load_local_dataset_rejects_bad_names_and_missing_tsv(tmp_path):
    for bad in ("", "..", "a/b", "../x"):
        with pytest.raises(ValueError):
            bench.load_local_dataset(bad, tmp_path)
    with pytest.raises(FileNotFoundError):
        bench.load_local_dataset("nope", tmp_path)


def test_term_tokens_handles_suffix_colon_ampersand_and_apostrophes():
    assert bench.term_tokens("Kolla API:et!") == ["kolla", "apiet"]
    assert bench.term_tokens("plus Q&A, åäö.") == ["plus", "q", "and", "a", "åäö"]
    assert bench.term_tokens("Let's go") == ["lets", "go"]


def test_english_terms_accept_swedish_suffixes_and_spellings():
    ref = "Vi måste pusha branchen och kolla API:et."
    assert bench.score_english_terms(ref, "vi måste pusha branchen och kolla API:et") == {
        "cs": (3, 3),
        "en": (0, 0),
    }
    # inflection of the English stem is fine, a phonetic Swedish spelling is not
    assert bench.score_english_terms(ref, "pusha branches api")["cs"] == (3, 3)
    assert bench.score_english_terms(ref, "puscha brantch api")["cs"] == (1, 3)
    assert bench.score_english_terms(ref, "")["cs"] == (0, 3)


def test_english_terms_phrases_and_alternate_spellings():
    ref = "Jag ska merga pull requesten och köra unit testerna."
    assert bench.english_units(ref)[0] == "cs"
    assert bench.score_english_terms(ref, "jag ska merge pull requesten och unit testerna")[
        "cs"
    ] == (3, 3)
    # a phrase is one unit: "pull" alone does not count
    assert bench.score_english_terms(ref, "merga pull och unit test")["cs"] == (2, 3)


def test_english_terms_repeated_unit_must_appear_as_often():
    ref = "Both the main branch and the other main branch."  # 'the' x2 -> English sentence
    assert bench.english_units(ref)[0] == "en"
    assert bench.score_english_terms(ref, "both the main branch and other")["en"] == (6, 9)
    ref_sv = "På main och sen main igen."
    assert bench.score_english_terms(ref_sv, "på main och sen igen")["cs"] == (1, 2)


def test_english_sentence_scores_every_token_exactly():
    ref = "Could you send me the link after the meeting?"
    hyp_ok = "could you send me the link after the meeting"
    assert bench.score_english_terms(ref, hyp_ok)["en"] == (9, 9)
    # Swedish phonetic rendering: only "send" and "link" survive
    assert bench.score_english_terms(ref, "kud ju send mi de link efter de miting")["en"] == (
        2,
        9,
    )
    # no Swedish-suffix leniency for whole English sentences
    assert bench.score_english_terms(ref, "could you send me the links after the meetings")[
        "en"
    ] == (7, 9)


def test_english_term_accuracy_corpus_buckets():
    refs = ["Vi pushar branchen", "Could you send me the link now", "Inget engelskt här"]
    hyps = ["vi pushar branchen", "could you send mi the link now", "inget engelskt här"]
    acc = bench.english_term_accuracy(refs, hyps)
    assert acc["eng_n_cs"] == 2 and acc["eng_acc_cs"] == 1.0
    assert acc["eng_n_en"] == 7 and acc["eng_acc_en"] == pytest.approx(6 / 7)
    assert acc["eng_acc"] == pytest.approx(8 / 9)
    none = bench.english_term_accuracy(["Hej då"], ["hej då"])
    assert none["eng_acc"] is None and none["eng_n"] == 0
    with pytest.raises(ValueError):
        bench.english_term_accuracy(["a"], [])


@pytest.mark.skipif(not _SENTENCES.is_file(), reason="eval sentence list not present")
def test_lexicon_covers_the_codeswitch_sentence_list():
    lines = [
        ln.strip()
        for ln in _SENTENCES.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    kinds = [bench.english_units(ln) for ln in lines]
    assert sum(1 for _, u in kinds if u) >= len(lines) - 4
    assert [k for k, _ in kinds].count("en") >= 3


@pytest.mark.skipif(not _SENTENCES.is_file(), reason="eval sentence list not present")
def test_prompt_and_hotwords_do_not_leak_eval_terms():
    lines = [
        ln.strip()
        for ln in _SENTENCES.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    eval_units = {
        u
        for ln in lines
        for kind, units in [bench.english_units(ln)]
        if kind == "cs"
        for u in units
    }
    for text in (bench.INITIAL_PROMPT, bench.HOTWORDS):
        tokens = bench.term_tokens(text)
        for unit in eval_units:
            assert not any(bench._unit_at(tokens, i, unit, True) for i in range(len(tokens)))
    # and the prompt really contains English terms spelled in English
    assert bench.english_units(bench.INITIAL_PROMPT)[1]


def test_expand_sets_prompt_and_hotwords_only_for_whisper():
    jobs = [{"name": "local", "language": "sv", "audio": ["x.wav"], "refs": ["a"]}]
    whisper = bench.expand_sets(
        bench.MODELS["kb-whisper-small"], jobs, True, prompt="P", hotwords="H"
    )
    assert whisper[0]["initial_prompt"] == "P" and whisper[0]["hotwords"] == "H"
    plain = bench.expand_sets(bench.MODELS["kb-whisper-small"], jobs, True)
    assert "initial_prompt" not in plain[0] and "hotwords" not in plain[0]
    sherpa = bench.expand_sets(bench.MODELS["parakeet-tdt-0.6b-v3"], jobs, True, prompt="P")
    assert "initial_prompt" not in sherpa[0]
    single = bench.expand_sets(
        bench.MODELS["kb-whisper-small"],
        [{"name": "file", "language": "auto", "audio": ["x.wav"], "refs": None}],
        False,
        prompt="P",
    )
    assert all(s["initial_prompt"] == "P" for s in single)


def _raw(hyps):
    return {
        "download_s": 0.0,
        "load_s": 1.0,
        "runs": [
            {
                "set": "local",
                "language": "sv",
                "detected_language": None,
                "audio_s": 10.0,
                "transcribe_s": 2.0,
                "peak_rss_mb": 300.0,
                "hypotheses": list(hyps),
            }
        ],
    }


def test_build_rows_adds_english_term_accuracy_when_asked():
    refs = {"local": ["Vi pushar branchen", "Hej då"]}
    rows, _ = bench.build_rows(_raw(["vi pushar branchen", "hej då"]), "m", None, refs, terms=True)
    assert rows[0]["eng_acc"] == 1.0 and rows[0]["eng_n"] == 2
    rows, _ = bench.build_rows(_raw(["vi puscha brantchen", "hej då"]), "m", None, refs)
    assert "eng_acc" not in rows[0]


def _fake_claude(edits):
    import json
    import subprocess

    def runner(cmd, **kwargs):
        assert "[1] " in kwargs["input"]  # transcript goes on stdin, not argv
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": json.dumps(edits)}), stderr=""
        )

    return runner


def test_llm_fix_texts_applies_safe_edits_through_steno_llm():
    runner = _fake_claude([{"id": 1, "old": "puscha", "new": "push"}])
    fixed, report = bench.llm_fix_texts(["vi puscha den", "hej då"], runner=runner)
    assert fixed == ["vi push den", "hej då"]
    assert report["applied"] == 1 and report["failed_batches"] == 0


def test_fix_row_scores_the_fixed_hypotheses_and_adds_fix_time():
    base_rows, hyps = bench.build_rows(
        _raw(["vi puscha branchen", "hej då"]),
        "m",
        None,
        {"local": ["Vi pusha branchen", "Hej då"]},
    )
    base = base_rows[0]

    def fixer(texts):
        return [t.replace("puscha", "pusha") for t in texts], {"proposed": 1, "applied": 1}

    row, fixed = bench.fix_row(
        base, hyps[base["run"]], ["Vi pusha branchen", "Hej då"], fixer=fixer, terms=True
    )
    assert fixed[0] == "vi pusha branchen"
    assert row["model"] == "m+fix" and row["run"] == "m+fix[local]"
    assert row["wer"] == 0.0 and base["wer"] > 0
    assert row["fix_applied"] == 1
    assert row["rtf"] >= base["rtf"]
    assert row["eng_acc"] == 1.0


def test_format_local_table_columns_and_notes():
    rows = [
        {
            "model": "a+prompt",
            "wer": 0.1,
            "cer": 0.05,
            "eng_acc": 0.5,
            "eng_acc_cs": 0.6,
            "eng_acc_en": None,
            "rtf": 0.3,
            "peak_rss_mb": 700.0,
            "language_match": 0.9,
            "error": None,
        },
        {
            "model": "a+fix",
            "wer": 0.08,
            "cer": 0.04,
            "eng_acc": 0.7,
            "eng_acc_cs": 0.8,
            "eng_acc_en": 0.1,
            "rtf": 0.4,
            "fix_s": 8.0,
            "fix_applied": 2,
            "fix_proposed": 3,
            "error": None,
        },
        {"model": "b", "error": "boom"},
    ]
    table = bench.format_local_table(rows)
    assert "| a+prompt | 10.0% | 5.0% | 50.0% | 60.0% | - | 0.300 | 700 |" in table
    assert "language detected correctly in 90%" in table
    assert "claude fix 8.0 s, 2/3 edits applied" in table
    assert "FAILED: boom" in table
