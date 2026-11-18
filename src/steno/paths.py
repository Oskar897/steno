"""Where Steno keeps user data. Always outside the repository."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Steno"


def data_dir() -> Path:
    """Root data directory. Override with the STENO_DATA_DIR env var (used by tests)."""
    override = os.environ.get("STENO_DATA_DIR")
    path = Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def sessions_dir() -> Path:
    path = data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_dir() -> Path:
    """Encrypted voice profiles (biometric data, see ``steno.secure``)."""
    path = data_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path
