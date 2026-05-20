"""Tests for the folder picker endpoint.

The Tk dialog is mocked — we don't want a real OS window popping up during
test runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_pick_folder_returns_selection(
    tmp_project_root: Path, fake_sd_card: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the dialog returns a path, the endpoint surfaces it as JSON."""
    from app.routers import sdcard

    def fake_dialog(initial_dir: Path | None) -> str | None:
        return str(fake_sd_card)

    monkeypatch.setattr(sdcard, "open_folder_dialog", fake_dialog)

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/sdcard/pick-folder")
    assert r.status_code == 200
    assert r.json() == {"path": str(fake_sd_card)}


def test_pick_folder_returns_null_on_cancel(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the user cancels the dialog (returns None), the endpoint returns null."""
    from app.routers import sdcard

    monkeypatch.setattr(sdcard, "open_folder_dialog", lambda _initial: None)

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/sdcard/pick-folder")
    assert r.status_code == 200
    assert r.json() == {"path": None}


def test_pick_folder_passes_parent_of_current_path_as_initial(
    tmp_project_root: Path, fake_sd_card: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a path is already saved, the picker opens at its parent dir."""
    from app.routers import sdcard

    seen_initial: list[Path | None] = []

    def fake_dialog(initial_dir: Path | None) -> str | None:
        seen_initial.append(initial_dir)
        return None

    monkeypatch.setattr(sdcard, "open_folder_dialog", fake_dialog)

    from app.main import app

    client = TestClient(app)
    # Save a path first.
    client.patch("/api/settings", json={"sd_card_path": str(fake_sd_card)})
    # Then call pick-folder.
    client.post("/api/sdcard/pick-folder")

    assert len(seen_initial) == 1
    assert seen_initial[0] == fake_sd_card.parent


def test_pick_folder_no_initial_when_unset(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If no path is saved, initial_dir is None."""
    from app.routers import sdcard

    seen: list[Path | None] = []

    def fake_dialog(initial_dir: Path | None) -> str | None:
        seen.append(initial_dir)
        return None

    monkeypatch.setattr(sdcard, "open_folder_dialog", fake_dialog)

    from app.main import app

    client = TestClient(app)
    client.post("/api/sdcard/pick-folder")
    assert seen == [None]
