"""Tests for the SD card scanner.

Uses the seed_dev_sd.py script (via a fixture) so the test fixtures
exactly match what the reader is supposed to read.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def seeded_sd(tmp_path: Path) -> Path:
    """Run the seed script against tmp_path and return the SD root."""
    # Make scripts/ importable
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


def test_scan_finds_all_well_formed_games(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    reg = load_systems()
    games = scan_games(seeded_sd, reg)
    # 7 games seeded; all should be returned (including the malformed one).
    assert len(games) == 7

    by_folder = {g.game_folder_name: g for g in games}
    assert "Tetris (FC)" in by_folder
    assert "F-Zero (SFC)" in by_folder
    assert "Pokemon Unbound (GBA)" in by_folder


def test_reader_extracts_display_and_code(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    games = scan_games(seeded_sd, load_systems())
    tetris = next(g for g in games if g.game_folder_name == "Tetris (FC)")
    assert tetris.display_name == "Tetris"
    assert tetris.system_code == "FC"


def test_reader_reads_rom_filename_from_m3u(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    games = scan_games(seeded_sd, load_systems())
    pokemon = next(g for g in games if g.game_folder_name == "Pokemon Unbound (GBA)")
    assert pokemon.rom_filename == "Pokemon Unbound (v2.1.1.1).gba"
    assert pokemon.has_rom_file is True


def test_reader_detects_malformed_no_m3u(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    games = scan_games(seeded_sd, load_systems())
    broken = next(g for g in games if g.game_folder_name == "Broken Game (GB)")
    assert broken.is_malformed is True
    assert broken.malformed_reason is not None
    assert "m3u" in broken.malformed_reason.lower()
    assert broken.rom_filename is None
    assert broken.has_rom_file is False


def test_reader_detects_box_art(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    games = scan_games(seeded_sd, load_systems())
    # All seeded well-formed games have art; broken one does not.
    for g in games:
        if g.game_folder_name == "Broken Game (GB)":
            assert g.has_boxart is False
        else:
            assert g.has_boxart is True, f"{g.game_folder_name} should have art"
            assert g.boxart_path is not None


def test_reader_detects_saves_both_naming_conventions(seeded_sd: Path) -> None:
    """Saves can be either <m3u-basename>.sav or <rom-filename>.sav."""
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    games = scan_games(seeded_sd, load_systems())
    # Tetris seeded with .m3u.sav style only
    tetris = next(g for g in games if g.game_folder_name == "Tetris (FC)")
    assert tetris.has_save is True
    assert tetris.save_path is not None
    assert tetris.save_path.endswith(".m3u.sav")

    # Pokemon Unbound seeded with BOTH; we expect the .m3u.sav variant to win
    # (it's the canonical Five-Game naming).
    pokemon = next(g for g in games if g.game_folder_name == "Pokemon Unbound (GBA)")
    assert pokemon.has_save is True
    assert pokemon.save_path is not None
    assert pokemon.save_path.endswith(".m3u.sav")

    # Mike Tyson has no save.
    tyson = next(g for g in games if "Punch-Out" in g.game_folder_name)
    assert tyson.has_save is False
    assert tyson.save_path is None


def test_reader_skips_folders_without_known_code(tmp_path: Path) -> None:
    """Random folder names like 'screenshots' should be skipped silently."""
    from app.services.sdcard_reader import scan_games
    from app.services.system_registry import load_systems

    sd = tmp_path / "sd"
    (sd / "Roms" / "screenshots").mkdir(parents=True)
    (sd / "Roms" / "My Photos (Final)").mkdir()
    (sd / "Roms" / "Tetris (FC)").mkdir()
    (sd / "Roms" / "Tetris (FC)" / "Tetris.nes").write_bytes(b"\x00")
    (sd / "Roms" / "Tetris (FC)" / "Tetris (FC).m3u").write_text("Tetris.nes")

    games = scan_games(sd, load_systems())
    assert len(games) == 1
    assert games[0].game_folder_name == "Tetris (FC)"


def test_scan_orphan_art_excludes_current_games(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import scan_games, scan_orphan_art
    from app.services.system_registry import load_systems

    reg = load_systems()
    games = scan_games(seeded_sd, reg)
    orphans = scan_orphan_art(seeded_sd, reg, games)

    orphan_names = {o.game_folder_name for o in orphans}
    # Seeded orphans:
    assert "Lunar - Silver Star Story (PS)" in orphan_names
    assert "Chrono Trigger (SFC)" in orphan_names
    assert "Advance Wars (GBA)" in orphan_names
    # And nothing from the current game set:
    assert "Tetris (FC)" not in orphan_names
    assert "Pokemon Unbound (GBA)" not in orphan_names


def test_listing_returns_slot_count_and_summary(seeded_sd: Path) -> None:
    from app.services.sdcard_reader import listing
    from app.services.system_registry import load_systems

    result = listing(seeded_sd, load_systems(), slot_cap=10)
    assert result.slot_count == 7
    assert result.slot_cap == 10
    assert result.summary["total"] == 7
    assert result.summary["malformed"] == 1
    assert result.summary["with_boxart"] == 6
    # Seeded m3u_save=True for Tetris, Kirby, Pokemon Unbound, F-Zero.
    assert result.summary["with_save"] == 4
