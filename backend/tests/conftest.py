"""Shared fixtures. Most importantly: redirect MINUI_MANAGER_ROOT to a tmp path
so tests never touch the real ./data/ directory.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app's data dir at a tmp directory for the duration of the test."""
    monkeypatch.setenv("MINUI_MANAGER_ROOT", str(tmp_path))

    # The paths module reads the env var at import time, so reload it (and
    # downstream modules that hold path references).
    import app.paths

    importlib.reload(app.paths)
    import app.config

    importlib.reload(app.config)
    yield tmp_path
    # Cleanup: env var is restored by monkeypatch; modules will be reloaded again
    # by the next test that uses this fixture.


@pytest.fixture
def fake_sd_card(tmp_path: Path) -> Path:
    """Build a minimal directory that passes the SD card validity check."""
    sd = tmp_path / "fake_sd"
    sd.mkdir()
    (sd / ".system").mkdir()
    (sd / "Emus").mkdir()
    (sd / "Roms").mkdir()
    (sd / "Saves").mkdir()
    return sd
