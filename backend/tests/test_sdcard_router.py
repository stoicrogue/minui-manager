"""Tests for the Phase 2 sdcard endpoints (games / orphan-art / box-art).

These test the HTTP contract; the underlying scanner has its own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_sd(tmp_path: Path) -> Path:
    import sys

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "scripts"))
    try:
        from seed_dev_sd import seed
    finally:
        sys.path.pop(0)

    sd = tmp_path / "seeded_sd"
    seed(sd)
    return sd


def _client_with_sd(tmp_project_root: Path, sd: Path) -> TestClient:
    from app.main import app

    client = TestClient(app)
    r = client.patch("/api/settings", json={"sd_card_path": str(sd)})
    assert r.status_code == 200
    return client


def test_games_returns_listing(tmp_project_root: Path, seeded_sd: Path) -> None:
    client = _client_with_sd(tmp_project_root, seeded_sd)
    r = client.get("/api/sdcard/games")
    assert r.status_code == 200
    body = r.json()
    assert body["slot_count"] == 7
    assert body["slot_cap"] == 10
    assert body["summary"]["malformed"] == 1
    folder_names = {g["game_folder_name"] for g in body["games"]}
    assert "Tetris (FC)" in folder_names
    assert "Pokemon Unbound (GBA)" in folder_names


def test_games_400_when_sd_not_ready(tmp_project_root: Path) -> None:
    from app.main import app

    client = TestClient(app)
    # No SD path set.
    r = client.get("/api/sdcard/games")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "sd_card_not_ready"


def test_orphan_art_lists_unmatched_pngs(
    tmp_project_root: Path, seeded_sd: Path
) -> None:
    client = _client_with_sd(tmp_project_root, seeded_sd)
    r = client.get("/api/sdcard/orphan-art")
    assert r.status_code == 200
    names = {item["game_folder_name"] for item in r.json()["art"]}
    assert "Lunar - Silver Star Story (PS)" in names
    assert "Chrono Trigger (SFC)" in names
    assert "Advance Wars (GBA)" in names
    # Don't list anything that's currently on the card.
    assert "Tetris (FC)" not in names


def test_box_art_streams_png(tmp_project_root: Path, seeded_sd: Path) -> None:
    client = _client_with_sd(tmp_project_root, seeded_sd)
    r = client.get("/api/sdcard/box-art", params={"name": "Tetris (FC)"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # PNG signature
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_box_art_404_for_missing_name(
    tmp_project_root: Path, seeded_sd: Path
) -> None:
    client = _client_with_sd(tmp_project_root, seeded_sd)
    r = client.get("/api/sdcard/box-art", params={"name": "Nonexistent Game (FC)"})
    assert r.status_code == 404


def test_box_art_rejects_path_traversal(
    tmp_project_root: Path, seeded_sd: Path
) -> None:
    """name parameter must not be able to escape Roms/.res/."""
    client = _client_with_sd(tmp_project_root, seeded_sd)
    for evil in ["../secret", "..\\secret", "/etc/passwd", "..", "."]:
        r = client.get("/api/sdcard/box-art", params={"name": evil})
        assert r.status_code == 404, f"path '{evil}' should be rejected"


def test_box_art_accepts_name_with_or_without_png_suffix(
    tmp_project_root: Path, seeded_sd: Path
) -> None:
    client = _client_with_sd(tmp_project_root, seeded_sd)
    r1 = client.get("/api/sdcard/box-art", params={"name": "Tetris (FC)"})
    r2 = client.get("/api/sdcard/box-art", params={"name": "Tetris (FC).png"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.content == r2.content
