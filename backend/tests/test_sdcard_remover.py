"""Safety tests for SafeSDCardRemover (Phase 7).

The remover is the only code path that takes files *off* the SD card.
It must enforce the same source-side safety as the writer (no escapes,
allow-listed subtrees) plus a destination-side check (the target must
land under ``ARCHIVE_DIR``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.sdcard_remover import SafeSDCardRemover, UnsafePathError


def _remover(fake_sd_card: Path, archive_root: Path) -> SafeSDCardRemover:
    return SafeSDCardRemover(fake_sd_card, archive_root)


# ---------------------------------------------------------------------------
# Source-side refusals (same shape as the writer's whitelist tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "../escape.txt",
        "Roms/../.system/evil.txt",
        "..\\escape.txt",
    ],
)
def test_source_dotdot_traversal_rejected(
    tmp_path: Path, fake_sd_card: Path, rel_path: str
) -> None:
    r = _remover(fake_sd_card, tmp_path / "archive")
    with pytest.raises(UnsafePathError):
        r.resolve_source(rel_path)


def test_source_absolute_path_rejected(tmp_path: Path, fake_sd_card: Path) -> None:
    r = _remover(fake_sd_card, tmp_path / "archive")
    with pytest.raises(UnsafePathError):
        r.resolve_source(str(tmp_path / "evil.txt"))


@pytest.mark.parametrize(
    "forbidden",
    [
        ".system/marker",
        ".userdata/foo",
        ".tmp_update/x",
        "Bios/x",
        "Emus/miyoomini/x.pak/y",
        "Roms_systems/x",
        "Tools_hidden/x",
        "em_ui.sh",
        "README.txt",
    ],
)
def test_source_outside_roms_and_saves_rejected(
    tmp_path: Path, fake_sd_card: Path, forbidden: str
) -> None:
    """Remover whitelist is Roms/ + Saves/ — nothing else."""
    r = _remover(fake_sd_card, tmp_path / "archive")
    with pytest.raises(UnsafePathError):
        r.resolve_source(forbidden)


def test_source_under_roms_accepted(tmp_path: Path, fake_sd_card: Path) -> None:
    r = _remover(fake_sd_card, tmp_path / "archive")
    resolved = r.resolve_source("Roms/Tetris (FC)")
    assert resolved == (fake_sd_card / "Roms" / "Tetris (FC)").resolve()


def test_source_under_saves_accepted(tmp_path: Path, fake_sd_card: Path) -> None:
    """Phase 7's new privilege: Saves/<CODE>/<file>.sav is a valid source."""
    r = _remover(fake_sd_card, tmp_path / "archive")
    resolved = r.resolve_source("Saves/FC/Tetris (FC).m3u.sav")
    assert resolved == (fake_sd_card / "Saves" / "FC" / "Tetris (FC).m3u.sav").resolve()


# ---------------------------------------------------------------------------
# Destination-side refusals
# ---------------------------------------------------------------------------


def test_dest_outside_archive_root_rejected(tmp_path: Path, fake_sd_card: Path) -> None:
    """copy_out must reject any destination that doesn't land under the
    configured archive root, even if it's a perfectly legitimate path."""
    archive = tmp_path / "archive"
    archive.mkdir()
    r = _remover(fake_sd_card, archive)

    (fake_sd_card / "Roms" / "Tetris (FC)").mkdir()
    (fake_sd_card / "Roms" / "Tetris (FC)" / "rom.nes").write_bytes(b"x")

    bad_dest = tmp_path / "elsewhere" / "rom.nes"
    with pytest.raises(UnsafePathError):
        r.copy_out("Roms/Tetris (FC)/rom.nes", bad_dest)


def test_dest_traversal_into_sibling_rejected(
    tmp_path: Path, fake_sd_card: Path
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    r = _remover(fake_sd_card, archive)

    (fake_sd_card / "Roms" / "Tetris (FC)").mkdir()
    (fake_sd_card / "Roms" / "Tetris (FC)" / "rom.nes").write_bytes(b"x")

    # archive/../sibling/... escapes the archive root.
    with pytest.raises(UnsafePathError):
        r.copy_out("Roms/Tetris (FC)/rom.nes", archive / ".." / "rom.nes")


# ---------------------------------------------------------------------------
# Happy-path copy + delete
# ---------------------------------------------------------------------------


def test_copy_out_file_preserves_contents(tmp_path: Path, fake_sd_card: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    r = _remover(fake_sd_card, archive)

    folder = fake_sd_card / "Roms" / "Tetris (FC)"
    folder.mkdir()
    src = folder / "Tetris.nes"
    payload = b"\x4e\x45\x53\x1a" + b"\x00" * 64
    src.write_bytes(payload)

    dest = archive / "FC" / "Tetris (FC)" / "20260520T193000" / "Tetris.nes"
    out = r.copy_out("Roms/Tetris (FC)/Tetris.nes", dest)
    assert out == dest
    assert dest.read_bytes() == payload
    # Source is still on the card — copy-then-delete keeps copy non-destructive.
    assert src.exists()


def test_copy_out_directory_recursive(tmp_path: Path, fake_sd_card: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    r = _remover(fake_sd_card, archive)

    folder = fake_sd_card / "Roms" / "Tetris (FC)"
    folder.mkdir()
    (folder / "Tetris.nes").write_bytes(b"rom")
    (folder / "Tetris (FC).m3u").write_text("Tetris.nes")

    dest = archive / "FC" / "Tetris (FC)" / "20260520T193000"
    out = r.copy_out("Roms/Tetris (FC)", dest)
    assert (out / "Tetris.nes").read_bytes() == b"rom"
    assert (out / "Tetris (FC).m3u").read_text(encoding="utf-8") == "Tetris.nes"
    # Source folder still exists.
    assert folder.is_dir()


def test_copy_out_handles_apostrophe_in_filename(
    tmp_path: Path, fake_sd_card: Path
) -> None:
    """The reference card has names like 'Kirby's Dream Land 2 (GB)' — make
    sure shutil.copy2 doesn't choke on the apostrophe."""
    archive = tmp_path / "archive"
    archive.mkdir()
    r = _remover(fake_sd_card, archive)

    folder = fake_sd_card / "Roms" / "Kirby's Dream Land 2 (GB)"
    folder.mkdir()
    (folder / "Kirby's Dream Land 2 (USA).gb").write_bytes(b"rom")

    dest = archive / "GB" / "Kirby's Dream Land 2 (GB)" / "ts"
    out = r.copy_out("Roms/Kirby's Dream Land 2 (GB)", dest)
    assert (out / "Kirby's Dream Land 2 (USA).gb").read_bytes() == b"rom"


def test_delete_source_under_roms_works(tmp_path: Path, fake_sd_card: Path) -> None:
    r = _remover(fake_sd_card, tmp_path / "archive")
    folder = fake_sd_card / "Roms" / "Tetris (FC)"
    folder.mkdir()
    (folder / "rom.nes").write_bytes(b"x")
    r.delete("Roms/Tetris (FC)")
    assert not folder.exists()


def test_delete_save_under_saves_works(tmp_path: Path, fake_sd_card: Path) -> None:
    """Save deletion is part of the remove flow."""
    r = _remover(fake_sd_card, tmp_path / "archive")
    saves = fake_sd_card / "Saves" / "FC"
    saves.mkdir(parents=True)
    sav = saves / "Tetris (FC).m3u.sav"
    sav.write_bytes(b"\x00" * 256)
    r.delete("Saves/FC/Tetris (FC).m3u.sav")
    assert not sav.exists()


def test_delete_path_outside_whitelist_refused(
    tmp_path: Path, fake_sd_card: Path
) -> None:
    """A bug that tried to .delete('.system') must not damage the card."""
    (fake_sd_card / ".system" / "marker").mkdir(parents=True)
    (fake_sd_card / ".system" / "marker" / "do-not-touch").write_text("ok")

    r = _remover(fake_sd_card, tmp_path / "archive")
    with pytest.raises(UnsafePathError):
        r.delete(".system/marker")
    assert (fake_sd_card / ".system" / "marker" / "do-not-touch").read_text() == "ok"


def test_delete_missing_path_is_no_op(tmp_path: Path, fake_sd_card: Path) -> None:
    r = _remover(fake_sd_card, tmp_path / "archive")
    # Should not raise.
    r.delete("Roms/Does Not Exist (FC)")


def test_remover_does_not_have_writer_privileges(
    tmp_path: Path, fake_sd_card: Path
) -> None:
    """Whitelist regression: the writer's Saves/ rejection from Phase 6
    must still hold for SafeSDCardWriter. Verifies that adding Saves/ to
    the remover didn't accidentally widen the writer."""
    from app.services.sdcard_writer import SafeSDCardWriter, UnsafePathError as WriterUnsafe

    w = SafeSDCardWriter(fake_sd_card)
    with pytest.raises(WriterUnsafe):
        w.write_text("Saves/FC/anything.sav", "x")
