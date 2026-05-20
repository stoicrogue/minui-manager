"""Shared fixtures. Most importantly: redirect MINUI_MANAGER_ROOT to a tmp path
so tests never touch the real ./data/ directory.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Order matters: reload paths/db FIRST, then services that depend on them,
# then routers that import those services, then main which mounts the routers.
_RELOAD_ORDER = (
    "app.paths",
    "app.config",
    "app.db",
    "app.models",
    "app.services.system_registry",
    "app.services.system_detector",
    "app.services.sdcard_validator",
    "app.services.sdcard_reader",
    "app.services.folder_picker",
    "app.services.library_store",
    "app.services.boxart_libretro",
    "app.routers.settings",
    "app.routers.sdcard",
    "app.routers.library",
    "app.routers.boxart",
    "app.main",
)


@pytest.fixture
def tmp_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app's data dir at a tmp directory for the test.

    Reloads every app module that holds path-dependent state, in
    dependency order, so the cached ``app.main`` from earlier tests
    doesn't keep router functions wired to a stale data directory.
    """
    monkeypatch.setenv("MINUI_MANAGER_ROOT", str(tmp_path))

    for mod_name in _RELOAD_ORDER:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

    # Fresh DB after the paths reload.
    import app.db

    app.db.init_db()

    yield tmp_path


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
