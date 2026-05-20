"""Tests for the SD card validity check.

Plan Section 4 rule 6: an SD card is OK iff the path exists and contains both
.system/ and Emus/ subdirectories.
"""

from __future__ import annotations

from pathlib import Path

from app.services.sdcard_validator import check_sd_card


def test_not_set_when_path_is_none() -> None:
    result = check_sd_card(None)
    assert result.status == "not_set"
    assert result.path is None


def test_not_found_when_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_dir"
    result = check_sd_card(missing)
    assert result.status == "not_found"
    assert result.path == str(missing)


def test_invalid_when_path_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "im_a_file.txt"
    f.write_text("hi")
    result = check_sd_card(f)
    assert result.status == "invalid"


def test_invalid_when_markers_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    result = check_sd_card(empty)
    assert result.status == "invalid"
    assert ".system" in result.missing_markers
    assert "Emus" in result.missing_markers


def test_invalid_when_only_one_marker_present(tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / ".system").mkdir()
    # No Emus/
    result = check_sd_card(partial)
    assert result.status == "invalid"
    assert result.missing_markers == ("Emus",)


def test_ok_when_both_markers_present(fake_sd_card: Path) -> None:
    result = check_sd_card(fake_sd_card)
    assert result.status == "ok"
    assert result.path == str(fake_sd_card)
    assert result.missing_markers == ()


def test_ok_with_just_minimum_markers(tmp_path: Path) -> None:
    """Soft markers (Roms/, Saves/, etc.) are not required for OK."""
    sd = tmp_path / "bare_sd"
    sd.mkdir()
    (sd / ".system").mkdir()
    (sd / "Emus").mkdir()
    # Deliberately no Roms/, Saves/, Bios/, em_ui.sh
    result = check_sd_card(sd)
    assert result.status == "ok"
