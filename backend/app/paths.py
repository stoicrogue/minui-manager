"""Centralized project-local paths. Everything lives under ./data/ (gitignored)."""

from __future__ import annotations

import os
from pathlib import Path


def _project_root() -> Path:
    """Walk up from this file to find the project root (contains pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    # Fallback: two levels up from backend/app/
    return here.parent.parent.parent


PROJECT_ROOT: Path = Path(os.environ.get("MINUI_MANAGER_ROOT", _project_root()))
DATA_DIR: Path = PROJECT_ROOT / "data"
CONFIG_PATH: Path = DATA_DIR / "config.json"
LIBRARY_DIR: Path = DATA_DIR / "library"
ARCHIVE_DIR: Path = DATA_DIR / "archive"
DB_PATH: Path = DATA_DIR / "app.db"
SYNC_LOG_PATH: Path = DATA_DIR / "sync.log"


def ensure_data_dirs() -> None:
    """Create the data directory tree if it doesn't exist. Idempotent."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
