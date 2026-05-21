"""Phase 7 archive roundtrip tests.

The plan flags this as critical: tests first. Covers:
    - Remove → archive contains rom + m3u + art + save (both formats).
    - Card-side teardown (folder + art + save removed).
    - Restore-to-library copies ROM + art into the library.
    - Restore is idempotent; archive files survive a restore.
    - Structured failure cases (missing game, missing archive files).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (200, 300), "red").save(buf, format="PNG")
    return buf.getvalue()


def _set_sd(client: TestClient, sd: Path) -> None:
    r = client.patch("/api/settings", json={"sd_card_path": str(sd)})
    assert r.status_code == 200, r.text


def _seed_card_with_game(
    sd: Path,
    *,
    folder: str = "Tetris (GB)",
    rom: str = "Tetris.gb",
    code: str = "GB",
    with_save_m3u: bool = False,
    with_save_legacy: bool = False,
    with_art: bool = False,
) -> None:
    game_dir = sd / "Roms" / folder
    game_dir.mkdir(parents=True)
    (game_dir / rom).write_bytes(b"ROMBYTES")
    (game_dir / f"{folder}.m3u").write_text(rom)
    if with_art:
        (sd / "Roms" / ".res").mkdir(parents=True, exist_ok=True)
        (sd / "Roms" / ".res" / f"{folder}.png").write_bytes(_png_bytes())
    saves_dir = sd / "Saves" / code
    saves_dir.mkdir(parents=True, exist_ok=True)
    if with_save_m3u:
        (saves_dir / f"{folder}.m3u.sav").write_bytes(b"SAVE-M3U")
    if with_save_legacy:
        (saves_dir / f"{rom}.sav").write_bytes(b"SAVE-LEGACY")


# ---------------------------------------------------------------------------
# Remove → archive
# ---------------------------------------------------------------------------


def test_remove_archives_rom_m3u_art_and_save(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Happy path: remove a fully-decked game, verify everything lands
    in the archive and the card is cleaned up."""
    _seed_card_with_game(
        fake_sd_card,
        with_art=True,
        with_save_m3u=True,
    )
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    r = client.delete("/api/sdcard/games/Tetris (GB)")
    assert r.status_code == 200, r.text
    body = r.json()
    archived = body["archived"]
    assert archived["game_folder_name"] == "Tetris (GB)"
    assert archived["has_save"] is True
    assert archived["has_boxart"] is True

    archive_path = Path(archived["archive_path"])
    # Folder structure inside the archive.
    assert (archive_path / "Tetris (GB)" / "Tetris.gb").read_bytes() == b"ROMBYTES"
    assert (archive_path / "Tetris (GB)" / "Tetris (GB).m3u").read_text() == "Tetris.gb"
    assert (archive_path / "Tetris (GB).png").is_file()
    assert (archive_path / "Tetris (GB).m3u.sav").read_bytes() == b"SAVE-M3U"

    # Card is cleaned up.
    assert not (fake_sd_card / "Roms" / "Tetris (GB)").exists()
    assert not (fake_sd_card / "Roms" / ".res" / "Tetris (GB).png").exists()
    assert not (fake_sd_card / "Saves" / "GB" / "Tetris (GB).m3u.sav").exists()


def test_remove_archives_both_save_formats(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """The reference card has both <game>.m3u.sav (current) and
    <rom>.sav (legacy). Both must land in the archive."""
    _seed_card_with_game(
        fake_sd_card,
        folder="Pokemon Unbound (GBA)",
        rom="Pokemon Unbound (v2.1.1.1).gba",
        code="GBA",
        with_save_m3u=True,
        with_save_legacy=True,
    )
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    r = client.delete("/api/sdcard/games/Pokemon Unbound (GBA)")
    assert r.status_code == 200, r.text
    archive_path = Path(r.json()["archived"]["archive_path"])

    assert (archive_path / "Pokemon Unbound (GBA).m3u.sav").read_bytes() == b"SAVE-M3U"
    assert (
        archive_path / "Pokemon Unbound (v2.1.1.1).gba.sav"
    ).read_bytes() == b"SAVE-LEGACY"

    # Both saves removed from the card.
    saves = fake_sd_card / "Saves" / "GBA"
    assert not (saves / "Pokemon Unbound (GBA).m3u.sav").exists()
    assert not (saves / "Pokemon Unbound (v2.1.1.1).gba.sav").exists()


def test_remove_without_art_or_save_is_fine(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """A bare-bones game (no art, no save) still archives cleanly."""
    _seed_card_with_game(fake_sd_card)  # no art, no saves
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    r = client.delete("/api/sdcard/games/Tetris (GB)")
    assert r.status_code == 200, r.text
    archived = r.json()["archived"]
    assert archived["has_boxart"] is False
    assert archived["has_save"] is False


def test_remove_404_when_game_not_on_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    r = client.delete("/api/sdcard/games/Nope (GB)")
    assert r.status_code == 404


def test_remove_400_when_sd_card_not_ready(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    # No SD card configured.
    r = client.delete("/api/sdcard/games/Tetris (GB)")
    assert r.status_code == 400


def test_remove_logs_to_sync_log(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.paths import SYNC_LOG_PATH

    _seed_card_with_game(fake_sd_card, with_save_m3u=True, with_art=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    client.delete("/api/sdcard/games/Tetris (GB)")

    log = SYNC_LOG_PATH.read_text(encoding="utf-8")
    assert "COPY_OUT" in log
    assert "DELETE" in log
    assert "Tetris (GB)" in log


# ---------------------------------------------------------------------------
# List archived
# ---------------------------------------------------------------------------


def test_list_archived_returns_most_recent_first(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    _seed_card_with_game(fake_sd_card, folder="A (GB)", rom="a.gb")
    client.delete("/api/sdcard/games/A (GB)")
    _seed_card_with_game(fake_sd_card, folder="B (GB)", rom="b.gb")
    client.delete("/api/sdcard/games/B (GB)")

    r = client.get("/api/archive")
    assert r.status_code == 200
    items = r.json()["archived"]
    names = [it["game_folder_name"] for it in items]
    assert names == ["B (GB)", "A (GB)"]


# ---------------------------------------------------------------------------
# Restore to library
# ---------------------------------------------------------------------------


def test_restore_copies_rom_and_art_back_into_library(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    _seed_card_with_game(fake_sd_card, with_art=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    removed = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    archive_id = removed["id"]

    r = client.post(f"/api/archive/{archive_id}/restore-to-library")
    assert r.status_code == 200, r.text
    restored = r.json()["library_game"]
    assert restored["system_code"] == "GB"
    assert restored["rom_filename"] == "Tetris.gb"
    assert restored["display_name"] == "Tetris"

    # Files are back in the library.
    from app.paths import LIBRARY_DIR

    assert (LIBRARY_DIR / "GB" / "Tetris.gb").read_bytes() == b"ROMBYTES"
    assert (LIBRARY_DIR / "GB" / ".res" / "Tetris (GB).png").is_file()


def test_restore_is_idempotent_when_library_entry_already_exists(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Re-restoring (or restoring a game whose library entry already
    exists) returns the existing entry without erroring. Files are
    re-copied so a corrupted library file gets healed."""
    _seed_card_with_game(fake_sd_card, with_art=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    removed = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]

    first = client.post(f"/api/archive/{removed['id']}/restore-to-library").json()
    second = client.post(f"/api/archive/{removed['id']}/restore-to-library").json()
    assert first["library_game"]["id"] == second["library_game"]["id"]


def test_restore_does_not_destroy_archive_files(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """The archive must still be intact after a restore so re-restore
    or zip-and-backup workflows keep working."""
    _seed_card_with_game(fake_sd_card, with_art=True, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    removed = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    archive_path = Path(removed["archive_path"])
    client.post(f"/api/archive/{removed['id']}/restore-to-library")

    assert (archive_path / "Tetris (GB)" / "Tetris.gb").is_file()
    assert (archive_path / "Tetris (GB).png").is_file()
    assert (archive_path / "Tetris (GB).m3u.sav").is_file()


def test_restore_410_when_archive_files_missing(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """If the user moved/deleted the archive folder, restore must fail
    cleanly rather than half-restore an empty file."""
    _seed_card_with_game(fake_sd_card)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    removed = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    # Sabotage: wipe the archive contents.
    import shutil

    shutil.rmtree(Path(removed["archive_path"]))

    r = client.post(f"/api/archive/{removed['id']}/restore-to-library")
    assert r.status_code == 410


def test_restore_404_when_archive_id_unknown(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    r = client.post("/api/archive/9999/restore-to-library")
    assert r.status_code == 404
