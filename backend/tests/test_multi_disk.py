"""End-to-end tests for multi-disk ROM support.

Covers the new flows added with multi-disk:

- Upload + confirm: multiple disc files + an .m3u land in one library row
- SD reader: parses every disc from a multi-line .m3u
- Sync: writes every disc + an m3u listing them all
- Import-from-card: pulls every disc back into the library
- Backup roundtrip: export + import preserves the multi-disk row
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


def _set_sd(client: TestClient, sd: Path) -> None:
    r = client.patch("/api/settings", json={"sd_card_path": str(sd)})
    assert r.status_code == 200, r.text


def _make_multi_disk_card_game(sd_root: Path) -> None:
    """Seed the card with a 2-disc PS game (Lunar)."""
    folder = sd_root / "Roms" / "Lunar (PS)"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Lunar Disc 1.chd").write_bytes(b"DISC1")
    (folder / "Lunar Disc 2.chd").write_bytes(b"DISC2")
    (folder / "Lunar (PS).m3u").write_text(
        "Lunar Disc 1.chd\nLunar Disc 2.chd\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Upload + confirm
# ---------------------------------------------------------------------------


def test_multi_file_upload_creates_multi_disk_library_entry(
    tmp_project_root: Path,
) -> None:
    client = _client(tmp_project_root)
    r = client.post(
        "/api/library/upload",
        files=[
            ("files", ("Lunar (PS).m3u", b"Lunar Disc 1.chd\nLunar Disc 2.chd\n", "application/octet-stream")),
            ("files", ("Lunar Disc 1.chd", b"DISC1", "application/octet-stream")),
            ("files", ("Lunar Disc 2.chd", b"DISC2", "application/octet-stream")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_multi_disk"] is True
    assert body["disc_count"] == 2
    # Detection runs on the .m3u stem, not on a disc filename.
    assert body["original_filename"] == "Lunar (PS).m3u"
    assert body["detection"]["detected_code"] == "PS"

    draft_id = body["draft_id"]
    confirmed = client.post(
        f"/api/library/drafts/{draft_id}/confirm",
        json={"system_code": "PS", "display_name": "Lunar"},
    )
    assert confirmed.status_code == 200, confirmed.text
    game = confirmed.json()
    assert game["is_multi_disk"] is True
    assert game["disc_filenames"] == ["Lunar Disc 1.chd", "Lunar Disc 2.chd"]
    assert game["rom_filename"] == "Lunar Disc 1.chd"  # primary = first disc

    # On disk: both discs + a canonical .m3u under the per-game folder.
    # The folder mirrors what gets synced to the device so the user can
    # eyeball it (or rsync it by hand) without surprises.
    from app.paths import LIBRARY_DIR

    folder = LIBRARY_DIR / "PS" / "Lunar (PS)"
    assert (folder / "Lunar Disc 1.chd").read_bytes() == b"DISC1"
    assert (folder / "Lunar Disc 2.chd").read_bytes() == b"DISC2"
    assert (folder / "Lunar (PS).m3u").read_text(encoding="utf-8") == (
        "Lunar Disc 1.chd\nLunar Disc 2.chd\n"
    )


def test_multi_file_upload_without_m3u_orders_alphabetically(
    tmp_project_root: Path,
) -> None:
    """No .m3u uploaded — fall back to lexicographic order so 'Disc 1' precedes 'Disc 2'."""
    client = _client(tmp_project_root)
    r = client.post(
        "/api/library/upload",
        files=[
            # Send Disc 2 first to prove ordering doesn't depend on upload order.
            ("files", ("Lunar Disc 2.chd", b"DISC2", "application/octet-stream")),
            ("files", ("Lunar Disc 1.chd", b"DISC1", "application/octet-stream")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_multi_disk"] is True

    confirmed = client.post(
        f"/api/library/drafts/{body['draft_id']}/confirm",
        json={"system_code": "PS", "display_name": "Lunar"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["disc_filenames"] == ["Lunar Disc 1.chd", "Lunar Disc 2.chd"]


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_sync_writes_every_disc_and_full_m3u(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    up = client.post(
        "/api/library/upload",
        files=[
            ("files", ("Lunar (PS).m3u", b"Lunar Disc 1.chd\nLunar Disc 2.chd\n", "application/octet-stream")),
            ("files", ("Lunar Disc 1.chd", b"DISC1BYTES", "application/octet-stream")),
            ("files", ("Lunar Disc 2.chd", b"DISC2BYTES", "application/octet-stream")),
        ],
    ).json()
    game = client.post(
        f"/api/library/drafts/{up['draft_id']}/confirm",
        json={"system_code": "PS", "display_name": "Lunar"},
    ).json()

    r = client.post("/api/sdcard/sync", json={"library_ids": [game["id"]]})
    assert r.status_code == 200, r.text

    folder = fake_sd_card / "Roms" / "Lunar (PS)"
    assert (folder / "Lunar Disc 1.chd").read_bytes() == b"DISC1BYTES"
    assert (folder / "Lunar Disc 2.chd").read_bytes() == b"DISC2BYTES"
    assert (folder / "Lunar (PS).m3u").read_text(encoding="utf-8") == (
        "Lunar Disc 1.chd\nLunar Disc 2.chd\n"
    )


# ---------------------------------------------------------------------------
# SD card reader + import-from-card
# ---------------------------------------------------------------------------


def test_sd_reader_exposes_disc_filenames(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    _make_multi_disk_card_game(fake_sd_card)

    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    (game,) = scan_games(fake_sd_card, load_systems())
    assert game.is_multi_disk is True
    assert game.disc_filenames == ["Lunar Disc 1.chd", "Lunar Disc 2.chd"]
    assert game.rom_filename == "Lunar Disc 1.chd"
    assert game.is_malformed is False


def test_sd_reader_flags_multi_disk_with_missing_disc(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """If the m3u lists 2 discs but only 1 is on disk, that's malformed."""
    folder = fake_sd_card / "Roms" / "Lunar (PS)"
    folder.mkdir(parents=True)
    (folder / "Lunar Disc 1.chd").write_bytes(b"DISC1")
    (folder / "Lunar (PS).m3u").write_text(
        "Lunar Disc 1.chd\nLunar Disc 2.chd\n", encoding="utf-8"
    )

    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    (game,) = scan_games(fake_sd_card, load_systems())
    assert game.is_malformed is True
    assert "Disc 2" in (game.malformed_reason or "") or "missing" in (
        game.malformed_reason or ""
    )


def test_import_from_card_copies_every_disc(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    _make_multi_disk_card_game(fake_sd_card)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    r = client.post("/api/sdcard/games/Lunar (PS)/import-to-library")
    assert r.status_code == 200, r.text
    imported = r.json()["imported"]
    assert imported["is_multi_disk"] is True
    assert imported["disc_filenames"] == ["Lunar Disc 1.chd", "Lunar Disc 2.chd"]

    from app.paths import LIBRARY_DIR

    folder = LIBRARY_DIR / "PS" / "Lunar (PS)"
    assert (folder / "Lunar Disc 1.chd").read_bytes() == b"DISC1"
    assert (folder / "Lunar Disc 2.chd").read_bytes() == b"DISC2"


# ---------------------------------------------------------------------------
# Backup export + import
# ---------------------------------------------------------------------------


def test_backup_roundtrip_preserves_multi_disk(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    up = client.post(
        "/api/library/upload",
        files=[
            ("files", ("Lunar (PS).m3u", b"Lunar Disc 1.chd\nLunar Disc 2.chd\n", "application/octet-stream")),
            ("files", ("Lunar Disc 1.chd", b"DISC1BYTES", "application/octet-stream")),
            ("files", ("Lunar Disc 2.chd", b"DISC2BYTES", "application/octet-stream")),
        ],
    ).json()
    game = client.post(
        f"/api/library/drafts/{up['draft_id']}/confirm",
        json={"system_code": "PS", "display_name": "Lunar"},
    ).json()

    export_bytes = client.get("/api/library/export").content

    # Wipe the library.
    client.delete(f"/api/library/{game['id']}")

    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", export_bytes, "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 1

    listing = client.get("/api/library").json()["games"]
    assert len(listing) == 1
    restored = listing[0]
    assert restored["is_multi_disk"] is True
    assert restored["disc_filenames"] == ["Lunar Disc 1.chd", "Lunar Disc 2.chd"]

    from app.paths import LIBRARY_DIR

    folder = LIBRARY_DIR / "PS" / "Lunar (PS)"
    assert (folder / "Lunar Disc 1.chd").read_bytes() == b"DISC1BYTES"
    assert (folder / "Lunar Disc 2.chd").read_bytes() == b"DISC2BYTES"
