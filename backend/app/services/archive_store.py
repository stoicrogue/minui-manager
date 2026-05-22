"""Archive store — remove a game from the SD card and restore it later.

Layout under ``./data/archive/``::

    <CODE>/<game-folder>/<YYYY-MM-DDTHH-MM-SS>/
        <game-folder>/                    # whole Roms/<game-folder> tree
            <rom-file>
            <game-folder>.m3u
        <game-folder>.png                 # box art (if it was on the card)
        <game-folder>.m3u.sav             # save (new format, if present)
        <rom-filename>.sav                # save (legacy format, if present)

Each archive directory is self-contained: the user can zip it, ship it,
or restore it back into the library — no DB-only state.

Failure model: copy-then-commit-then-delete.

1. Copy every file from the card into the archive directory.
2. Insert the ``ArchivedGame`` row and commit.
3. Delete the originals from the card.

If step 3 fails partway, the archive is complete and the card may have
duplicates — the user can manually clean those up, and the archive is
still restorable. The opposite failure (delete-without-archive) is the
nightmare scenario; this ordering eliminates it.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import paths as _paths
from app.models import ArchivedGame, LibraryGame
from app.services.sdcard_reader import SAVES_DIR, scan_games
from app.services.sdcard_remover import (
    SafeSDCardRemover,
    SDCardRemoveError,
    make_archive_timestamp,
)
from app.services.system_registry import SystemRegistry

logger = logging.getLogger(__name__)


class ArchiveError(Exception):
    """Surfaced by the router as a 4xx."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Archive (remove from card)
# ---------------------------------------------------------------------------


def archive_game(
    session: Session,
    sd_root: Path,
    registry: SystemRegistry,
    game_folder_name: str,
) -> ArchivedGame:
    """Move a game off the SD card into a fresh archive directory."""
    games = scan_games(sd_root, registry)
    target = next((g for g in games if g.game_folder_name == game_folder_name), None)
    if target is None:
        raise ArchiveError(
            "not_on_card", f"No game folder named {game_folder_name!r} on the card."
        )

    code = target.system_code
    timestamp = make_archive_timestamp()
    archive_relpath = f"{code}/{game_folder_name}/{timestamp}"
    archive_dir = _paths.ARCHIVE_DIR / archive_relpath
    if archive_dir.exists():
        # Vanishingly unlikely in practice (timestamps are second-resolution),
        # but defensive — never let an existing path silently overwrite.
        raise ArchiveError(
            "archive_collision",
            f"Archive path already exists: {archive_dir}.",
        )
    archive_dir.parent.mkdir(parents=True, exist_ok=True)

    remover = SafeSDCardRemover(sd_root, _paths.ARCHIVE_DIR)

    copied_sources: list[str] = []
    has_save = False
    has_boxart = False

    try:
        # 1. The game folder itself (rom + m3u).
        folder_rel = f"Roms/{game_folder_name}"
        remover.copy_out(folder_rel, archive_dir / game_folder_name)
        copied_sources.append(folder_rel)

        # 2. Box art, if present.
        if target.has_boxart:
            art_rel = f"Roms/.res/{game_folder_name}.png"
            remover.copy_out(art_rel, archive_dir / f"{game_folder_name}.png")
            copied_sources.append(art_rel)
            has_boxart = True

        # 3. Saves — both formats. The reference card has examples of each.
        save_via_m3u_rel = f"{SAVES_DIR}/{code}/{game_folder_name}.m3u.sav"
        if (sd_root / save_via_m3u_rel).is_file():
            remover.copy_out(
                save_via_m3u_rel, archive_dir / f"{game_folder_name}.m3u.sav"
            )
            copied_sources.append(save_via_m3u_rel)
            has_save = True
        if target.rom_filename:
            save_via_rom_rel = f"{SAVES_DIR}/{code}/{target.rom_filename}.sav"
            if (sd_root / save_via_rom_rel).is_file():
                remover.copy_out(
                    save_via_rom_rel, archive_dir / f"{target.rom_filename}.sav"
                )
                copied_sources.append(save_via_rom_rel)
                has_save = True
    except (SDCardRemoveError, OSError) as exc:
        # Roll back: nuke the partially-built archive dir.
        shutil.rmtree(archive_dir, ignore_errors=True)
        raise ArchiveError("copy_failed", f"Could not archive: {exc}") from exc

    # 4. Insert + commit BEFORE the destructive delete step. If the delete
    # fails afterward, we still have a valid archive entry; the worst case
    # is a duplicate game on the card that the user can re-remove.
    disc_filenames_json = (
        json.dumps(target.disc_filenames)
        if len(target.disc_filenames) > 1
        else None
    )
    row = ArchivedGame(
        system_code=code,
        game_folder_name=game_folder_name,
        display_name=target.display_name,
        rom_filename=target.rom_filename or "",
        archive_relpath=archive_relpath,
        has_save=has_save,
        has_boxart=has_boxart,
        archived_at=datetime.now(timezone.utc),
        disc_filenames=disc_filenames_json,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    # 5. Delete originals.
    try:
        for src in copied_sources:
            remover.delete(src)
    except (SDCardRemoveError, OSError):
        logger.exception(
            "Archive copy succeeded but card cleanup failed for %s — "
            "user may need to remove leftovers manually.",
            game_folder_name,
        )
        # Don't raise: the archive is complete and the DB row exists. The
        # user's data is safe; only cleanup is incomplete.

    return row


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_archived(session: Session, limit: int | None = None) -> list[ArchivedGame]:
    """Return archived games, most-recent first."""
    stmt = select(ArchivedGame).order_by(desc(ArchivedGame.archived_at))
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def get_archived(session: Session, archive_id: int) -> ArchivedGame | None:
    return session.get(ArchivedGame, archive_id)


# ---------------------------------------------------------------------------
# Restore (back into library)
# ---------------------------------------------------------------------------


def delete_archived(session: Session, archive_id: int) -> ArchivedGame:
    """Permanently delete an archived game (DB row + on-disk bundle).

    Useful for trimming the archive list when the same game has been
    cycled through the card multiple times. The DB row is dropped even
    if the on-disk directory is already missing (treated as already
    cleaned-up). Returns the row that was deleted so the caller can
    report what went away.

    Raises :class:`ArchiveError` (``not_found``) if no row exists.
    """
    archived = get_archived(session, archive_id)
    if archived is None:
        raise ArchiveError("not_found", f"No archived game with id {archive_id}.")

    # Snapshot fields before delete so the return value is still usable
    # after the session detaches the row.
    archive_dir = archived.archive_path

    # Guard against an empty/odd archive_relpath that could resolve outside
    # ./data/archive/. Belt-and-braces: rmtree below would explode anyway
    # if the path isn't real, but a typed-up relpath could theoretically
    # escape, and we don't want that even in a single-user tool.
    try:
        archive_dir.resolve(strict=False).relative_to(_paths.ARCHIVE_DIR.resolve(strict=False))
    except ValueError:
        raise ArchiveError(
            "unsafe_path",
            f"Refusing to delete archive at {archive_dir} — path escapes the archive root.",
        )

    if archive_dir.is_dir():
        try:
            shutil.rmtree(archive_dir)
        except OSError as exc:
            raise ArchiveError(
                "delete_failed",
                f"Could not remove archive directory {archive_dir}: {exc}",
            ) from exc
        # Try to clean up the empty `<CODE>/<game-folder>/` parent if no
        # sibling timestamps remain. Best-effort only.
        parent = archive_dir.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                grandparent = parent.parent
                if grandparent.is_dir() and not any(grandparent.iterdir()):
                    grandparent.rmdir()
        except OSError:
            pass

    session.delete(archived)
    session.flush()
    return archived


def restore_to_library(session: Session, archive_id: int) -> LibraryGame:
    """Copy the archived game's discs (+ art if present) into the library.

    Multi-disk-aware: every disc listed in the archive (either via the
    persisted ``disc_filenames`` or recovered by reading the archived
    .m3u) is copied into the per-game folder. Single-disk falls out of
    the same code path with a one-element list.

    Idempotent: if a library entry with the same (system_code,
    display_name) or (system_code, rom_filename) already exists, the
    files are still re-copied (so a corrupted library file gets healed)
    and the existing row is returned without modification. The archive
    directory is left intact so re-restore is possible.

    Raises :class:`ArchiveError` if the archive itself is missing files
    (the user moved or deleted them manually).
    """
    archived = get_archived(session, archive_id)
    if archived is None:
        raise ArchiveError("not_found", f"No archived game with id {archive_id}.")

    code = archived.system_code
    display_name = archived.display_name
    archived_folder = archived.archived_game_folder

    if not archived_folder.is_dir():
        raise ArchiveError(
            "archive_missing",
            f"Archived game folder is missing at {archived_folder}. The archive "
            "may have been moved or deleted outside the app.",
        )

    # Recover the disc list. Prefer what's stored on the row; if it's
    # NULL (pre-multi-disk archive), peek at the on-disk .m3u to catch
    # multi-disk games archived before this feature shipped.
    disc_names = list(archived.disc_filenames_list)
    if len(disc_names) <= 1:
        m3u_path = archived_folder / f"{archived.game_folder_name}.m3u"
        if m3u_path.is_file():
            from app.services.library_store import _read_m3u_disc_list
            recovered = _read_m3u_disc_list(m3u_path)
            if len(recovered) > 1:
                disc_names = recovered

    if not disc_names:
        disc_names = [archived.rom_filename]

    # Every disc must exist in the archive folder.
    disc_srcs: list[Path] = []
    for disc in disc_names:
        src = archived_folder / disc
        if not src.is_file():
            raise ArchiveError(
                "archive_missing",
                f"Disc '{disc}' missing from archive at {src}.",
            )
        disc_srcs.append(src)

    # Re-copy every disc into the per-game library folder.
    library_system_dir = _paths.LIBRARY_DIR / code
    library_system_dir.mkdir(parents=True, exist_ok=True)
    game_dest_folder = library_system_dir / archived.game_folder_name
    game_dest_folder.mkdir(parents=True, exist_ok=True)
    dest_discs: list[Path] = []
    for src, name in zip(disc_srcs, disc_names):
        dest = game_dest_folder / name
        shutil.copy2(src, dest)
        dest_discs.append(dest)

    # Write the canonical .m3u so the restored folder mirrors the on-card layout.
    from app.services.library_store import _write_m3u
    _write_m3u(game_dest_folder, archived.game_folder_name, disc_names)

    # Re-copy art if it's in the archive.
    art_src = archived.archived_boxart_path
    if art_src.is_file():
        res_dir = library_system_dir / ".res"
        res_dir.mkdir(parents=True, exist_ok=True)
        art_dest = res_dir / f"{archived.game_folder_name}.png"
        shutil.copy2(art_src, art_dest)

    # Look up existing library row by either unique key.
    rom_filename = disc_names[0]
    existing = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == code,
            LibraryGame.rom_filename == rom_filename,
        )
    )
    if existing is None:
        existing = session.scalar(
            select(LibraryGame).where(
                LibraryGame.system_code == code,
                LibraryGame.display_name == display_name,
            )
        )

    if existing is not None:
        return existing

    total_size = sum(p.stat().st_size for p in dest_discs)
    disc_filenames_json = (
        json.dumps(disc_names) if len(disc_names) > 1 else None
    )
    row = LibraryGame(
        system_code=code,
        rom_filename=rom_filename,
        display_name=display_name,
        size_bytes=total_size,
        disc_filenames=disc_filenames_json,
    )
    session.add(row)
    session.flush()
    return row
