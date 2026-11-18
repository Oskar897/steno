from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def steno_data_dir(tmp_path, monkeypatch):
    """Every test gets an empty, private data dir. Nothing touches real user data."""
    data = tmp_path / "data"
    monkeypatch.setenv("STENO_DATA_DIR", str(data))
    return data
