"""Archive roundtrip tests — save-only model.

The archive stores only save file(s); the library is the canonical backup
for ROMs and box art. Covers:
    - Remove → archive contains only the save(s); card is cleaned up.
    - Archive entry has no save when the card didn't have one.
    - Restore-save-to-card copies saves back into ``Saves/<CODE>/``.
    - Restore preconditions: archive missing, no save, game not on card.
    - Delete-archive removes the bundle + DB row.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


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
        (sd / "Roms" / ".res" / f"{folder}.png").write_bytes(b"PNGBYTES")
    saves_dir = sd / "Saves" / code
    saves_dir.mkdir(parents=True, exist_ok=True)
    if with_save_m3u:
        (saves_dir / f"{folder}.m3u.sav").write_bytes(b"SAVE-M3U")
    if with_save_legacy:
        (saves_dir / f"{rom}.sav").write_bytes(b"SAVE-LEGACY")


# ---------------------------------------------------------------------------
# Remove → archive (save-only)
# ---------------------------------------------------------------------------


def test_remove_archives_save_only_and_cleans_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Happy path: a fully-decked game lands as a save-only archive,
    and the card has the ROM folder + boxart + save removed."""
    _seed_card_with_game(
        fake_sd_card,
        with_art=True,
        with_save_m3u=True,
    )
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    r = client.delete("/api/sdcard/games/Tetris (GB)")
    assert r.status_code == 200, r.text
    archived = r.json()["archived"]
    assert archived["game_folder_name"] == "Tetris (GB)"
    assert archived["has_save"] is True
    # has_boxart is always False under the save-only model.
    assert archived["has_boxart"] is False

    archive_path = Path(archived["archive_path"])
    # Only the save lives in the archive.
    assert (archive_path / "Tetris (GB).m3u.sav").read_bytes() == b"SAVE-M3U"
    assert not (archive_path / "Tetris (GB)").exists()  # no ROM folder
    assert not (archive_path / "Tetris (GB).png").exists()  # no boxart

    # Card is cleaned up.
    assert not (fake_sd_card / "Roms" / "Tetris (GB)").exists()
    assert not (fake_sd_card / "Roms" / ".res" / "Tetris (GB).png").exists()
    assert not (fake_sd_card / "Saves" / "GB" / "Tetris (GB).m3u.sav").exists()


def test_remove_archives_both_save_formats(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """The reference card has both <game>.m3u.sav (current) and
    <rom>.sav (legacy). Both land in the archive."""
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


def test_remove_without_save_skips_archive_directory(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """A game with no save still archives the row but no on-disk bundle.

    The card is still cleaned up; the archive entry just has has_save=False
    and an empty (non-existent) archive folder. Lets the user see the
    history without leaving empty timestamp folders behind.
    """
    _seed_card_with_game(fake_sd_card, with_art=True)  # boxart but no save
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    r = client.delete("/api/sdcard/games/Tetris (GB)")
    assert r.status_code == 200, r.text
    archived = r.json()["archived"]
    assert archived["has_save"] is False
    assert archived["has_boxart"] is False

    # No archive directory created — nothing to put in it.
    assert not Path(archived["archive_path"]).exists()

    # Card still cleaned up.
    assert not (fake_sd_card / "Roms" / "Tetris (GB)").exists()
    assert not (fake_sd_card / "Roms" / ".res" / "Tetris (GB).png").exists()


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


def test_archived_at_iso_string_has_utc_offset(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """The wire format must include a timezone suffix so the browser parses
    it as UTC instead of (incorrectly) as local time. Without this, the
    Angular date pipe shifts the displayed timestamp by the user's UTC
    offset."""
    _seed_card_with_game(fake_sd_card)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    ts = archived["archived_at"]
    # Acceptable UTC suffixes: "Z" or "+00:00".
    assert ts.endswith("+00:00") or ts.endswith("Z"), (
        f"archived_at is missing a timezone suffix: {ts!r}"
    )


# ---------------------------------------------------------------------------
# Restore save to card
# ---------------------------------------------------------------------------


def _put_game_back_on_card(
    sd: Path,
    *,
    folder: str = "Tetris (GB)",
    rom: str = "Tetris.gb",
) -> None:
    """Simulate the user re-sending the game from the library to the card."""
    game_dir = sd / "Roms" / folder
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / rom).write_bytes(b"ROMBYTES")
    (game_dir / f"{folder}.m3u").write_text(rom)


def test_restore_save_copies_back_to_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Archive a game with a save, then re-seed the ROM on the card and
    confirm the save lands in Saves/<CODE>/ on the card."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]

    # User sends the game back from the library — simulate by re-creating
    # the folder on the card.
    _put_game_back_on_card(fake_sd_card)

    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 200, r.text
    payload = r.json()["restored"]
    assert payload["game_folder_name"] == "Tetris (GB)"
    assert payload["system_code"] == "GB"
    assert "Saves/GB/Tetris (GB).m3u.sav" in payload["restored"]

    # Save is back on the card.
    save = fake_sd_card / "Saves" / "GB" / "Tetris (GB).m3u.sav"
    assert save.read_bytes() == b"SAVE-M3U"


def test_restore_save_copies_both_formats(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Both .m3u.sav and the legacy <rom>.sav are restored together."""
    _seed_card_with_game(
        fake_sd_card,
        folder="Pokemon (GBA)",
        rom="pokemon.gba",
        code="GBA",
        with_save_m3u=True,
        with_save_legacy=True,
    )
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Pokemon (GBA)").json()["archived"]
    _put_game_back_on_card(fake_sd_card, folder="Pokemon (GBA)", rom="pokemon.gba")

    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 200, r.text
    restored_paths = r.json()["restored"]["restored"]
    assert "Saves/GBA/Pokemon (GBA).m3u.sav" in restored_paths
    assert "Saves/GBA/pokemon.gba.sav" in restored_paths

    saves = fake_sd_card / "Saves" / "GBA"
    assert (saves / "Pokemon (GBA).m3u.sav").read_bytes() == b"SAVE-M3U"
    assert (saves / "pokemon.gba.sav").read_bytes() == b"SAVE-LEGACY"


def test_restore_save_overwrites_existing_save_on_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """The user explicitly asked to restore — clobber whatever's there."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    _put_game_back_on_card(fake_sd_card)

    # Stash a different save on the card before restoring.
    (fake_sd_card / "Saves" / "GB").mkdir(parents=True, exist_ok=True)
    (fake_sd_card / "Saves" / "GB" / "Tetris (GB).m3u.sav").write_bytes(b"NEWER")

    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 200, r.text
    assert (
        fake_sd_card / "Saves" / "GB" / "Tetris (GB).m3u.sav"
    ).read_bytes() == b"SAVE-M3U"


def test_restore_save_is_repeatable_and_leaves_archive_intact(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Re-restore: the archive must still hold the save, so it works twice."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    archive_path = Path(archived["archive_path"])
    _put_game_back_on_card(fake_sd_card)

    r1 = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r1.status_code == 200
    r2 = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r2.status_code == 200

    assert (archive_path / "Tetris (GB).m3u.sav").is_file()


def test_restore_save_404_when_archive_id_unknown(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    r = client.post("/api/archive/9999/restore-save-to-card")
    assert r.status_code == 404


def test_restore_save_400_when_no_save_archived(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """An archive entry with has_save=False has nothing to restore."""
    _seed_card_with_game(fake_sd_card)  # no save
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    _put_game_back_on_card(fake_sd_card)
    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 400


def test_restore_save_409_when_game_not_on_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Without the game folder on the card the save has nothing to bind
    to. The user should send the game from the library first."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    # Skip _put_game_back_on_card — game is NOT on the card.
    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 409


def test_restore_save_410_when_archive_files_missing(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """If the user moved/deleted the archive directory behind the app's
    back, restore must fail cleanly rather than half-restore."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    _put_game_back_on_card(fake_sd_card)
    import shutil

    shutil.rmtree(Path(archived["archive_path"]))

    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 410


def test_restore_save_400_when_sd_card_not_ready(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """No SD card configured → the precondition check rejects the call."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]

    # Now clear the SD card setting.
    r = client.patch("/api/settings", json={"sd_card_path": None})
    assert r.status_code == 200

    r = client.post(f"/api/archive/{archived['id']}/restore-save-to-card")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Delete an archive
# ---------------------------------------------------------------------------


def test_delete_archive_removes_row_and_on_disk_bundle(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Happy path: archive a game, then delete the archive. Both the DB row
    and the on-disk bundle should be gone, but the SD card stays untouched."""
    _seed_card_with_game(fake_sd_card, with_art=True, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    archive_dir = Path(archived["archive_path"])
    assert archive_dir.is_dir()

    r = client.delete(f"/api/archive/{archived['id']}")
    assert r.status_code == 200, r.text
    body = r.json()["deleted"]
    assert body["id"] == archived["id"]
    assert body["game_folder_name"] == "Tetris (GB)"

    # On-disk bundle gone.
    assert not archive_dir.exists()
    # DB row gone.
    listing = client.get("/api/archive").json()["archived"]
    assert all(item["id"] != archived["id"] for item in listing)


def test_delete_archive_404_when_id_unknown(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)
    r = client.delete("/api/archive/9999")
    assert r.status_code == 404


def test_delete_archive_still_removes_row_when_dir_already_gone(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """If the user wiped ./data/archive/... manually, deleting the entry
    should still clean up the orphan DB row (no resurrection)."""
    _seed_card_with_game(fake_sd_card, with_save_m3u=True)
    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    archived = client.delete("/api/sdcard/games/Tetris (GB)").json()["archived"]
    # Sabotage: nuke the archive dir behind the app's back.
    import shutil

    shutil.rmtree(Path(archived["archive_path"]))

    r = client.delete(f"/api/archive/{archived['id']}")
    assert r.status_code == 200
    # Row should be gone from the listing.
    listing = client.get("/api/archive").json()["archived"]
    assert all(item["id"] != archived["id"] for item in listing)


def test_delete_one_archive_leaves_sibling_archives_alone(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """When the same game has been cycled through the card twice, deleting
    one timestamp should not touch the other."""
    import time

    client = _client(tmp_project_root)
    _set_sd(client, fake_sd_card)

    # First cycle: seed → remove (archive #1).
    _seed_card_with_game(
        fake_sd_card, folder="Chrono (SFC)", rom="Chrono.sfc", code="SFC", with_save_m3u=True
    )
    first = client.delete("/api/sdcard/games/Chrono (SFC)").json()["archived"]

    # Bump the wall clock a smidge so the timestamp suffix differs. Archive
    # paths are second-resolution, so this is the minimum gap.
    time.sleep(1.1)

    # Second cycle: re-seed → remove (archive #2).
    _seed_card_with_game(
        fake_sd_card, folder="Chrono (SFC)", rom="Chrono.sfc", code="SFC", with_save_m3u=True
    )
    second = client.delete("/api/sdcard/games/Chrono (SFC)").json()["archived"]

    assert first["id"] != second["id"]
    first_dir = Path(first["archive_path"])
    second_dir = Path(second["archive_path"])
    assert first_dir.is_dir() and second_dir.is_dir()

    # Delete the first archive; the second should be untouched.
    r = client.delete(f"/api/archive/{first['id']}")
    assert r.status_code == 200
    assert not first_dir.exists()
    assert second_dir.is_dir()

    # Listing still has the second.
    listing = client.get("/api/archive").json()["archived"]
    ids = {item["id"] for item in listing}
    assert second["id"] in ids
    assert first["id"] not in ids
