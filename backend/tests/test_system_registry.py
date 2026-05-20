"""Tests for systems.yaml loading + folder-name parsing."""

from __future__ import annotations

from app.services.system_registry import (
    load_systems,
    parse_game_folder_name,
    reset_cache,
)


def setup_module(_module: object) -> None:
    reset_cache()


def test_registry_loads_all_codes_from_yaml() -> None:
    reg = load_systems()
    expected = {
        "FC", "GB", "GBC", "GBA", "MGBA",
        "SFC", "SUPA", "SGB",
        "VB", "PKM",
        "MD", "SMS", "GG",
        "PCE", "PS",
        "NGP", "NGPC",
        "P8",
    }
    assert reg.codes == expected


def test_systems_for_extension_sorted_by_preference() -> None:
    reg = load_systems()
    gba = reg.systems_for_extension(".gba")
    assert [s.code for s in gba] == ["GBA", "MGBA"]  # 10, 5


def test_systems_for_extension_handles_dotless_input() -> None:
    reg = load_systems()
    assert [s.code for s in reg.systems_for_extension("nes")] == ["FC"]


def test_parse_extracts_display_and_code() -> None:
    reg = load_systems()
    assert parse_game_folder_name("Tetris (FC)", reg) == ("Tetris", "FC")
    assert parse_game_folder_name(
        "Kirby's Dream Land 2 (GB)", reg
    ) == ("Kirby's Dream Land 2", "GB")
    assert parse_game_folder_name(
        "WarioWare, Inc. - Mega Microgame$! (GBA)", reg
    ) == ("WarioWare, Inc. - Mega Microgame$!", "GBA")


def test_parse_rejects_unknown_code() -> None:
    reg = load_systems()
    assert parse_game_folder_name("My Documents (Final)", reg) is None


def test_parse_rejects_missing_suffix() -> None:
    reg = load_systems()
    assert parse_game_folder_name("Tetris", reg) is None
    assert parse_game_folder_name("Tetris (FC) extra", reg) is None
