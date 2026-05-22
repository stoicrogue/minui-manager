"""Tests for importing a game from the SD card into the laptop library.

Covers ``library_store.import_from_sd_card`` and the
``POST /api/sdcard/games/{name}/import-to-library`` endpoint, plus the
``matches_library_id`` field that the games listing populates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — build a minimal but realistic card layout for one game
# ---------------------------------------------------------------------------


def _make_card_game(
    sd_root: Path,
    *,
    folder: str,
    rom_filename: str,
    rom_bytes: bytes = b"\x00ROM\x00",
    with_art: bool = True,
    system_code: str | None = None,
) -> None:
    """Create Roms/<folder>/<rom> + .m3u + (optional) shared art."""
    game_dir = sd_root / "Roms" / folder
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / rom_filename).write_bytes(rom_bytes)
    (game_dir / f"{folder}.m3u").write_text(rom_filename, encoding="utf-8")
    if with_art:
        art_dir = sd_root / "Roms" / ".res"
        art_dir.mkdir(parents=True, exist_ok=True)
        # Tiny valid-ish PNG bytes — content doesn't matter for the copy.
        (art_dir / f"{folder}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


def test_import_copies_rom_and_art_and_creates_row(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.db import session_scope
    from app.paths import LIBRARY_DIR
    from app.services.library_store import import_from_sd_card
    from app.services.system_registry import load_systems

    _make_card_game(fake_sd_card, folder="Tetris (FC)", rom_filename="Tetris.nes")

    with session_scope() as session:
        row = import_from_sd_card(session, fake_sd_card, load_systems(), "Tetris (FC)")
        # Capture values while still attached.
        folder = row.library_folder
        disc_paths = row.disc_paths
        art_path = row.boxart_path
        display = row.display_name
        sys_code = row.system_code

    assert folder == LIBRARY_DIR / "FC" / "Tetris (FC)"
    assert disc_paths == [folder / "Tetris.nes"]
    assert disc_paths[0].is_file()
    assert art_path == LIBRARY_DIR / "FC" / ".res" / "Tetris (FC).png"
    assert art_path.is_file()
    assert display == "Tetris"
    assert sys_code == "FC"


def test_import_without_box_art_succeeds(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.db import session_scope
    from app.paths import LIBRARY_DIR
    from app.services.library_store import import_from_sd_card
    from app.services.system_registry import load_systems

    _make_card_game(
        fake_sd_card, folder="Kirby (GB)", rom_filename="Kirby.gb", with_art=False
    )

    with session_scope() as session:
        row = import_from_sd_card(session, fake_sd_card, load_systems(), "Kirby (GB)")
        folder = row.library_folder
        disc_paths = row.disc_paths
        art_path = row.boxart_path

    assert folder == LIBRARY_DIR / "GB" / "Kirby (GB)"
    assert disc_paths == [folder / "Kirby.gb"]
    assert disc_paths[0].is_file()
    # boxart_path is always computed; the file should not exist.
    assert art_path == LIBRARY_DIR / "GB" / ".res" / "Kirby (GB).png"
    assert not art_path.is_file()


def test_import_rejects_missing_card_game(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.db import session_scope
    from app.services.library_store import LibraryError, import_from_sd_card
    from app.services.system_registry import load_systems

    with session_scope() as session:
        with pytest.raises(LibraryError) as info:
            import_from_sd_card(session, fake_sd_card, load_systems(), "Nope (FC)")
    assert info.value.code == "not_on_card"


def test_import_rejects_malformed_card_game(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """A folder with no .m3u (or no ROM file) shouldn't be importable."""
    from app.db import session_scope
    from app.services.library_store import LibraryError, import_from_sd_card
    from app.services.system_registry import load_systems

    # Folder exists, but no .m3u and no ROM file.
    (fake_sd_card / "Roms" / "Broken (GB)").mkdir(parents=True)

    with session_scope() as session:
        with pytest.raises(LibraryError) as info:
            import_from_sd_card(session, fake_sd_card, load_systems(), "Broken (GB)")
    assert info.value.code == "malformed"


def test_import_rejects_duplicate_rom_filename(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.db import session_scope
    from app.services.library_store import LibraryError, import_from_sd_card
    from app.services.system_registry import load_systems

    _make_card_game(fake_sd_card, folder="Tetris (FC)", rom_filename="Tetris.nes")
    with session_scope() as session:
        import_from_sd_card(session, fake_sd_card, load_systems(), "Tetris (FC)")

    # Second import of the same card game should be refused.
    with session_scope() as session:
        with pytest.raises(LibraryError) as info:
            import_from_sd_card(session, fake_sd_card, load_systems(), "Tetris (FC)")
    assert info.value.code == "duplicate_rom"


def test_import_rejects_path_traversal_name(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.db import session_scope
    from app.services.library_store import LibraryError, import_from_sd_card
    from app.services.system_registry import load_systems

    with session_scope() as session:
        with pytest.raises(LibraryError) as info:
            import_from_sd_card(
                session, fake_sd_card, load_systems(), "../etc/passwd"
            )
    # _find_card_game returns None for traversal-y names → not_on_card.
    assert info.value.code == "not_on_card"


def test_populate_library_matches_fills_in_id(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.db import session_scope
    from app.services.library_store import (
        import_from_sd_card,
        populate_library_matches,
    )
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    _make_card_game(fake_sd_card, folder="Tetris (FC)", rom_filename="Tetris.nes")
    _make_card_game(fake_sd_card, folder="Kirby (GB)", rom_filename="Kirby.gb")

    # Only import Tetris.
    with session_scope() as session:
        imported = import_from_sd_card(
            session, fake_sd_card, load_systems(), "Tetris (FC)"
        )
        imported_id = imported.id

    # Now scan the card and populate matches.
    games = scan_games(fake_sd_card, load_systems())
    with session_scope() as session:
        populate_library_matches(session, games)

    by_folder = {g.game_folder_name: g for g in games}
    assert by_folder["Tetris (FC)"].matches_library_id == imported_id
    assert by_folder["Kirby (GB)"].matches_library_id is None


# ---------------------------------------------------------------------------
# Router-level tests
# ---------------------------------------------------------------------------


def _client_with_sd(tmp_project_root: Path, fake_sd_card: Path) -> TestClient:
    """Boot the FastAPI app and point its settings at the fake SD card."""
    import app.main

    client = TestClient(app.main.app)
    # Push the SD card path through the settings endpoint so the saved
    # config.json matches what the router will read on each request.
    resp = client.patch("/api/settings", json={"sd_card_path": str(fake_sd_card)})
    assert resp.status_code == 200, resp.text
    return client


def test_router_import_endpoint_returns_200(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    _make_card_game(fake_sd_card, folder="Tetris (FC)", rom_filename="Tetris.nes")
    client = _client_with_sd(tmp_project_root, fake_sd_card)

    resp = client.post("/api/sdcard/games/Tetris (FC)/import-to-library")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"]["system_code"] == "FC"
    assert body["imported"]["rom_filename"] == "Tetris.nes"
    assert body["imported"]["display_name"] == "Tetris"
    assert body["imported"]["has_boxart"] is True


def test_router_games_listing_reports_matches(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    _make_card_game(fake_sd_card, folder="Tetris (FC)", rom_filename="Tetris.nes")
    _make_card_game(fake_sd_card, folder="Kirby (GB)", rom_filename="Kirby.gb")
    client = _client_with_sd(tmp_project_root, fake_sd_card)

    # Pre-import: nothing matches.
    pre = client.get("/api/sdcard/games").json()
    assert all(g["matches_library_id"] is None for g in pre["games"])

    # Import one.
    r = client.post("/api/sdcard/games/Tetris (FC)/import-to-library")
    assert r.status_code == 200, r.text
    imported_id = r.json()["imported"]["id"]

    # Post-import: Tetris reports the match; Kirby does not.
    post = client.get("/api/sdcard/games").json()
    by_folder = {g["game_folder_name"]: g for g in post["games"]}
    assert by_folder["Tetris (FC)"]["matches_library_id"] == imported_id
    assert by_folder["Kirby (GB)"]["matches_library_id"] is None


def test_router_import_returns_409_on_duplicate(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    _make_card_game(fake_sd_card, folder="Tetris (FC)", rom_filename="Tetris.nes")
    client = _client_with_sd(tmp_project_root, fake_sd_card)

    client.post("/api/sdcard/games/Tetris (FC)/import-to-library")
    again = client.post("/api/sdcard/games/Tetris (FC)/import-to-library")
    assert again.status_code == 409
    assert again.json()["code"] == "duplicate_rom"


def test_router_import_returns_404_when_missing(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client_with_sd(tmp_project_root, fake_sd_card)

    resp = client.post("/api/sdcard/games/Nope (FC)/import-to-library")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_on_card"
