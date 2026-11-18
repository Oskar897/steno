"""sv/en-only language choice for long-form chunks and model folder resolution."""

from __future__ import annotations

from steno.asr import SvEnLanguage, resolve_model


class FakeCt2:
    is_multilingual = True

    def __init__(self, results):
        self.results = results

    def detect_language(self, encoder_output):
        return self.results


def test_english_wins_only_against_all_nordic_languages_together():
    inner = FakeCt2([
        [("<|is|>", 0.5), ("<|sv|>", 0.3), ("<|en|>", 0.1)],  # Swedish heard as Icelandic
        [("<|en|>", 0.9), ("<|sv|>", 0.05)],
        [("<|no|>", 0.3), ("<|en|>", 0.4), ("<|sv|>", 0.2)],  # Nordic 0.5 beats en 0.4
        [("<|de|>", 0.8), ("<|en|>", 0.1)],  # neither: Swedish
    ])
    got = SvEnLanguage(inner).detect_language(None)
    assert [r[0][0] for r in got] == ["<|sv|>", "<|en|>", "<|sv|>", "<|sv|>"]
    assert got[1][0][1] == 0.9


def test_wrapper_passes_everything_else_through():
    assert SvEnLanguage(FakeCt2([])).is_multilingual is True


def test_model_id_maps_to_an_existing_plain_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("STENO_DATA_DIR", str(tmp_path))
    assert resolve_model("org/model") == "org/model"  # not downloaded: faster-whisper fetches it
    folder = tmp_path / "models" / "org_model"
    folder.mkdir(parents=True)
    assert resolve_model("org/model") == "org/model"  # empty folder is not a model
    (folder / "model.bin").write_bytes(b"x")
    assert resolve_model("org/model") == str(folder)
    assert resolve_model("small") == "small"
