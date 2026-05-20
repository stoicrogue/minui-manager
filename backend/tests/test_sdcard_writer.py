"""Critical safety tests for SafeSDCardWriter (Phase 6).

Per the plan: tests come first. The writer is the only code path that
mutates the user's SD card, so every refusal case is verified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.sdcard_writer import SafeSDCardWriter, UnsafePathError


def _writer(fake_sd_card: Path) -> SafeSDCardWriter:
    return SafeSDCardWriter(fake_sd_card)


# ---------------------------------------------------------------------------
# Path-escape refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "../escape.txt",
        "Roms/../../escape.txt",
        "Roms/../.system/evil.txt",
        "..\\escape.txt",
        "Roms/../..",
    ],
)
def test_dotdot_traversal_rejected(fake_sd_card: Path, rel_path: str) -> None:
    w = _writer(fake_sd_card)
    with pytest.raises(UnsafePathError):
        w.resolve_under_whitelist(rel_path)


def test_absolute_path_rejected(fake_sd_card: Path, tmp_path: Path) -> None:
    w = _writer(fake_sd_card)
    with pytest.raises(UnsafePathError):
        w.resolve_under_whitelist(str(tmp_path / "evil.txt"))


def test_path_with_null_byte_rejected(fake_sd_card: Path) -> None:
    w = _writer(fake_sd_card)
    with pytest.raises(UnsafePathError):
        w.resolve_under_whitelist("Roms/evil\x00.txt")


# ---------------------------------------------------------------------------
# Forbidden subtree refusals — only Roms/ is writable in Phase 6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        ".system/evil.txt",
        ".userdata/evil.txt",
        ".tmp_update/evil.txt",
        "Bios/evil.txt",
        "Bios/FC/evil.txt",
        "Emus/miyoomini/FC.pak/evil.txt",
        "Roms_systems/evil.txt",
        "Tools_hidden/evil.txt",
        "Saves/FC/anything.sav",  # Phase 7's job, not Phase 6
        "em_ui.sh",
        "README.txt",
    ],
)
def test_writes_outside_roms_are_rejected(fake_sd_card: Path, forbidden: str) -> None:
    w = _writer(fake_sd_card)
    with pytest.raises(UnsafePathError):
        w.resolve_under_whitelist(forbidden)
    # And the higher-level methods refuse too.
    with pytest.raises(UnsafePathError):
        w.write_text(forbidden, "x")
    with pytest.raises(UnsafePathError):
        w.mkdir(forbidden)


def test_writer_refuses_to_remove_outside_roms(fake_sd_card: Path) -> None:
    (fake_sd_card / ".system" / "marker").write_text("don't touch me")
    w = _writer(fake_sd_card)
    with pytest.raises(UnsafePathError):
        w.remove_tree(".system")
    # File still there.
    assert (fake_sd_card / ".system" / "marker").read_text() == "don't touch me"


# ---------------------------------------------------------------------------
# Happy-path writes
# ---------------------------------------------------------------------------


def test_mkdir_and_write_text_under_roms(fake_sd_card: Path) -> None:
    w = _writer(fake_sd_card)
    folder = w.mkdir("Roms/Tetris (FC)")
    assert folder == fake_sd_card / "Roms" / "Tetris (FC)"
    assert folder.is_dir()

    m3u = w.write_text("Roms/Tetris (FC)/Tetris (FC).m3u", "Tetris.nes")
    assert m3u.read_text(encoding="utf-8") == "Tetris.nes"


def test_copy_file_under_roms(fake_sd_card: Path, tmp_path: Path) -> None:
    src = tmp_path / "Tetris.nes"
    src.write_bytes(b"\x4e\x45\x53\x1a" + b"\x00" * 32)
    w = _writer(fake_sd_card)
    w.mkdir("Roms/Tetris (FC)")
    dest = w.copy_file(src, "Roms/Tetris (FC)/Tetris.nes")

    assert dest.is_file()
    assert dest.read_bytes() == src.read_bytes()
    assert dest.stat().st_size == src.stat().st_size


def test_box_art_filename_uses_game_folder_name_not_rom_ext(
    fake_sd_card: Path, tmp_path: Path
) -> None:
    """The plan's gotcha: shared .res/ filename is <game-folder>.png, NOT
    <rom-file>.png. Verify the writer is happy to place it that way and
    nothing in the design accidentally tacks on the ROM extension."""
    src = tmp_path / "tetris-art.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    w = _writer(fake_sd_card)
    w.mkdir("Roms/.res")
    dest = w.copy_file(src, "Roms/.res/Tetris (FC).png")

    assert dest.name == "Tetris (FC).png"
    # No double-extension like Tetris.nes.png.
    assert not (fake_sd_card / "Roms" / ".res" / "Tetris.nes.png").exists()
    assert dest.read_bytes() == src.read_bytes()


def test_remove_tree_under_roms_works(fake_sd_card: Path) -> None:
    """Used by the re-sync overwrite path: existing same-named game folder
    is removed before being rewritten."""
    w = _writer(fake_sd_card)
    folder = w.mkdir("Roms/Tetris (FC)")
    (folder / "Tetris.nes").write_bytes(b"old")
    (folder / "Tetris (FC).m3u").write_text("Tetris.nes")

    w.remove_tree("Roms/Tetris (FC)")
    assert not folder.exists()
    # Roms/ itself is left intact.
    assert (fake_sd_card / "Roms").is_dir()


def test_remove_tree_missing_path_is_no_op(fake_sd_card: Path) -> None:
    w = _writer(fake_sd_card)
    # Should not raise.
    w.remove_tree("Roms/Does Not Exist (FC)")


# ---------------------------------------------------------------------------
# Sync log
# ---------------------------------------------------------------------------


def test_writes_are_logged_to_sync_log(
    tmp_project_root: Path, fake_sd_card: Path, tmp_path: Path
) -> None:
    """Every successful write appends a human-readable line to
    ./data/sync.log."""
    from app.paths import SYNC_LOG_PATH

    src = tmp_path / "Tetris.nes"
    src.write_bytes(b"\x00" * 64)

    w = _writer(fake_sd_card)
    w.mkdir("Roms/Tetris (FC)")
    w.copy_file(src, "Roms/Tetris (FC)/Tetris.nes")
    w.write_text("Roms/Tetris (FC)/Tetris (FC).m3u", "Tetris.nes")

    log = SYNC_LOG_PATH.read_text(encoding="utf-8")
    assert "MKDIR" in log
    assert "COPY" in log
    assert "WRITE" in log
    assert "Roms/Tetris (FC)" in log.replace("\\", "/")


def test_failed_unsafe_call_does_not_log_a_write(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """An UnsafePathError refusal must not append a misleading 'wrote' line."""
    from app.paths import SYNC_LOG_PATH

    w = _writer(fake_sd_card)
    with pytest.raises(UnsafePathError):
        w.write_text(".system/evil.txt", "x")

    if SYNC_LOG_PATH.exists():
        assert ".system" not in SYNC_LOG_PATH.read_text(encoding="utf-8")
