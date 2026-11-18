from __future__ import annotations

import sys

import pytest

if sys.platform != "win32":
    # No DPAPI here: biometric files fall back to plaintext, for tests only.
    import os

    os.environ.setdefault("STENO_INSECURE_TEST", "1")


@pytest.fixture(autouse=True)
def steno_data_dir(tmp_path, monkeypatch):
    """Every test gets an empty, private data dir. Nothing touches real user data."""
    data = tmp_path / "data"
    monkeypatch.setenv("STENO_DATA_DIR", str(data))
    return data


@pytest.fixture(autouse=True)
def no_claude_cli(monkeypatch):
    """The real Claude CLI must never run from a test (tests inject a fake runner)."""
    import subprocess

    from steno import llm

    def forbidden(*args, **kwargs):
        raise AssertionError("a test tried to run the real Claude CLI")

    monkeypatch.setattr(llm.subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
