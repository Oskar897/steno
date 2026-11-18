from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest

from steno.config import Config, ConfigError, config_from_dict, load_config

REPO = Path(__file__).resolve().parents[1]


def test_defaults_without_file():
    assert load_config() == Config()


def test_example_matches_defaults():
    with (REPO / "config.example.toml").open("rb") as fh:
        assert config_from_dict(tomllib.load(fh)) == Config()


def test_example_lists_every_key_of_every_section_and_nothing_else():
    from dataclasses import fields

    with (REPO / "config.example.toml").open("rb") as fh:
        data = tomllib.load(fh)
    cfg = Config()
    assert set(data) == {f.name for f in fields(cfg)}
    for section, values in data.items():
        assert set(values) == {f.name for f in fields(getattr(cfg, section))}, section


def test_partial_override_from_data_dir(steno_data_dir):
    steno_data_dir.mkdir(parents=True, exist_ok=True)
    (steno_data_dir / "config.toml").write_text(
        '[asr]\nmodel = "tiny"\nlanguage = "auto"\n[vad]\nmax_segment_s = 8\n', encoding="utf-8"
    )
    cfg = load_config()
    assert cfg.asr.model == "tiny"
    assert cfg.asr.language == "auto"
    assert cfg.asr.compute_type == "int8"  # untouched default
    assert cfg.vad.max_segment_s == 8.0
    assert cfg.audio == Config().audio


def test_explicit_path(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("[audio]\ncapture_mic = false\n", encoding="utf-8")
    assert load_config(f).audio.capture_mic is False


def test_missing_explicit_path_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_wrong_type_errors():
    with pytest.raises(ConfigError):
        config_from_dict({"asr": {"cpu_threads": "many"}})


def test_unknown_keys_ignored_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = config_from_dict({"asr": {"bogus": 1}, "nothing": {}})
    assert cfg == Config()
    assert "bogus" in caplog.text


def test_invalid_toml(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text("[asr\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)


def test_speakers_section_defaults_and_override():
    assert Config().speakers.enabled is True
    assert Config().speakers.accept_threshold == 0.55 and Config().speakers.margin == 0.05
    cfg = config_from_dict({"speakers": {"enabled": False, "accept_threshold": 1}})
    assert cfg.speakers.enabled is False and cfg.speakers.accept_threshold == 1.0
    with pytest.raises(ConfigError):
        config_from_dict({"speakers": {"min_embed_s": "long"}})
