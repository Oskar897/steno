"""Configuration loading.

The dataclass defaults below are the source of truth and mirror
``config.example.toml`` (a test keeps them in sync). A user ``config.toml``
overrides them key by key; unknown keys are ignored with a warning.

Search order for ``config.toml`` when no explicit path is given:
1. ``<data_dir>/config.toml``
2. ``<repo root>/config.toml`` (development checkouts only, git-ignored)
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from steno import paths

log = logging.getLogger(__name__)

CONFIG_NAME = "config.toml"
EXAMPLE_NAME = "config.example.toml"


class ConfigError(ValueError):
    """Raised for an unreadable or invalid configuration file."""


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16000
    capture_mic: bool = True
    capture_loopback: bool = True
    mic_speaker_label: str = "Me"
    loopback_speaker_label: str = "Remote"


@dataclass(frozen=True)
class AsrConfig:
    backend: str = "faster-whisper"
    model: str = "KBLab/kb-whisper-small"
    compute_type: str = "int8"
    language: str = "sv"  # "sv", "en" or "auto"
    cpu_threads: int = 6
    beam_size: int = 1


@dataclass(frozen=True)
class VadConfig:
    threshold: float = 0.5
    min_silence_ms: int = 500
    min_speech_ms: int = 250
    max_segment_s: float = 15.0


@dataclass(frozen=True)
class EchoConfig:
    """Drop microphone speech that is just the loopback audio leaking into the mic."""

    enabled: bool = True
    # Mic is treated as echo when its RMS is below ``ratio`` x the overlapping loopback RMS.
    ratio: float = 0.35


@dataclass(frozen=True)
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    echo: EchoConfig = field(default_factory=EchoConfig)


def repo_root() -> Path | None:
    """Repo root when running from a source checkout, else None."""
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def find_config_file() -> Path | None:
    candidates = [paths.data_dir() / CONFIG_NAME]
    root = repo_root()
    if root is not None:
        candidates.append(root / CONFIG_NAME)
    return next((c for c in candidates if c.is_file()), None)


def _coerce(section: str, name: str, default: Any, value: Any) -> Any:
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        value = float(value) if ok else value
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise ConfigError(
            f"[{section}] {name}: expected {type(default).__name__}, got {value!r}"
        )
    return value


def _apply_section(section: str, obj: Any, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] must be a table")
    known = {f.name: f for f in fields(obj)}
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            log.warning("Ignoring unknown config key [%s] %s", section, key)
            continue
        updates[key] = _coerce(section, key, getattr(obj, key), value)
    return replace(obj, **updates)


def config_from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key not in {f.name for f in fields(cfg)}:
            log.warning("Ignoring unknown config section [%s]", key)
            continue
        updates[key] = _apply_section(key, getattr(cfg, key), value)
    cfg = replace(cfg, **updates)
    if cfg.asr.language not in {"sv", "en", "auto"} and len(cfg.asr.language) != 2:
        raise ConfigError(f"[asr] language: unsupported value {cfg.asr.language!r}")
    return cfg


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration. An explicit ``path`` must exist; otherwise defaults apply."""
    if path is not None:
        file = Path(path)
        if not file.is_file():
            raise ConfigError(f"Config file not found: {file.name}")
    else:
        file = find_config_file()
        if file is None:
            return Config()
    try:
        with file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {file.name}: {exc}") from exc
    return config_from_dict(data)
