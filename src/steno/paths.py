"""Where Steno keeps user data. Always outside the repository.

Default: ``~/.steno`` (``%USERPROFILE%\\.steno`` on Windows). AppData is avoided on
purpose: when Steno is started from an MSIX-packaged app (e.g. a terminal inside a
desktop app), Windows silently redirects AppData writes into that app's private
package folder, so the same user would end up with two separate data folders.
Documents is avoided because it is often synced to the cloud (biometric data).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Steno"
log = logging.getLogger(__name__)


def _legacy_dir() -> Path:
    """Data dir used by Steno <= 0.1 (platformdirs, may be MSIX-virtualised)."""
    return Path(user_data_dir(APP_NAME, appauthor=False))


def _migrate_legacy(target: Path) -> None:
    legacy = _legacy_dir()
    if target.exists() or not legacy.is_dir() or not any(legacy.iterdir()):
        return
    log.warning("Moving Steno data from %s to %s", legacy, target)
    shutil.copytree(legacy, target)
    shutil.rmtree(legacy, ignore_errors=True)


def data_dir() -> Path:
    """Root data directory. Override with the STENO_DATA_DIR env var (used by tests)."""
    override = os.environ.get("STENO_DATA_DIR")
    if override:
        path = Path(override)
    else:
        path = Path.home() / ".steno"
        _migrate_legacy(path)
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
