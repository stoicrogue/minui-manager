"""Archive store — remove a game from the SD card and stash its save.

Layout under ``./data/archive/``::

    <CODE>/<game-folder>/<YYYY-MM-DDTHH-MM-SS>/
        <game-folder>.m3u.sav             # save (new format, if present)
        <rom-filename>.sav                # save (legacy format, if present)

The archive only holds saves now. The library is the canonical backup
for ROMs and box art — duplicating them in the archive was pure waste.

Failure model: copy-then-commit-then-delete.

1. Copy save file(s) from the card into the archive directory.
2. Insert the ``ArchivedGame`` row and commit.
3. Delete the ROM folder, box art, and saves from the card.

If step 3 fails partway, the archive is complete and the card may have
duplicates — the user can re-trigger remove or clean them up manually,
and the archived save is safe. The opposite failure (delete-without-
archive) is the nightmare scenario; this ordering eliminates it for the
only thing the archive is responsible for: the save.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import paths as _paths
from app.models import ArchivedGame
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
    """Move a game off the SD card, archiving only its save file(s).

    The ROM and box art are deleted from the card without backup — the
    library already holds canonical copies of both, so duplicating them
    here would just waste disk.
    """
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

    remover = SafeSDCardRemover(sd_root, _paths.ARCHIVE_DIR)

    has_save = False
    # Saves go into the archive. ROM folder + box art are deleted only.
    save_copies: list[str] = []
    delete_after: list[str] = [f"Roms/{game_folder_name}"]
    if target.has_boxart:
        delete_after.append(f"Roms/.res/{game_folder_name}.png")

    save_via_m3u_rel = f"{SAVES_DIR}/{code}/{game_folder_name}.m3u.sav"
    if (sd_root / save_via_m3u_rel).is_file():
        save_copies.append(save_via_m3u_rel)
    if target.rom_filename:
        save_via_rom_rel = f"{SAVES_DIR}/{code}/{target.rom_filename}.sav"
        if (sd_root / save_via_rom_rel).is_file():
            save_copies.append(save_via_rom_rel)

    # Only create the archive directory if there's something to put in it.
    # An empty timestamp folder is just noise.
    if save_copies:
        archive_dir.mkdir(parents=True, exist_ok=False)
        try:
            for src_rel in save_copies:
                dest_name = Path(src_rel).name
                remover.copy_out(src_rel, archive_dir / dest_name)
                delete_after.append(src_rel)
                has_save = True
        except (SDCardRemoveError, OSError) as exc:
            shutil.rmtree(archive_dir, ignore_errors=True)
            raise ArchiveError("copy_failed", f"Could not archive: {exc}") from exc

    # Commit BEFORE the destructive delete step. If delete fails afterward,
    # the archive is intact and the worst case is a stale folder on the card
    # that the user can re-remove.
    row = ArchivedGame(
        system_code=code,
        game_folder_name=game_folder_name,
        display_name=target.display_name,
        rom_filename=target.rom_filename or "",
        archive_relpath=archive_relpath,
        has_save=has_save,
        # has_boxart used to mean "boxart bundled in the archive". Now nothing
        # but saves goes in, so it's always False — kept on the model to avoid
        # a migration, but it's no longer meaningful.
        has_boxart=False,
        archived_at=datetime.now(timezone.utc),
        disc_filenames=None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    try:
        for src in delete_after:
            remover.delete(src)
    except (SDCardRemoveError, OSError):
        logger.exception(
            "Archive copy succeeded but card cleanup failed for %s — "
            "user may need to remove leftovers manually.",
            game_folder_name,
        )
        # Don't raise: the archive is complete and the DB row exists. The
        # user's save is safe; only cleanup is incomplete.

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


def restore_save_to_card(
    session: Session,
    archive_id: int,
    sd_root: Path,
    registry: SystemRegistry,
) -> dict[str, object]:
    """Copy the archived save file(s) back onto the SD card.

    Use case: a game was removed from the card a while ago and the user
    wants to pick up where they left off. They re-send the game from
    the library (existing flow), then call this to drop their old save
    back into ``Saves/<CODE>/``.

    Preconditions:
        - The archive row exists and recorded at least one save.
        - The save file(s) still live in the archive directory.
        - The game folder is currently on the card (so the save has
          something to bind to).

    Overwrites any existing save with the same name. The archive itself
    is left intact so the user can re-restore later.

    Returns a dict describing what was placed: ``{"restored": [<rel paths
    written to the card>], "archive_path": ...}``.
    """
    archived = get_archived(session, archive_id)
    if archived is None:
        raise ArchiveError("not_found", f"No archived game with id {archive_id}.")

    if not archived.has_save:
        raise ArchiveError(
            "no_save",
            f"Archive for {archived.display_name} has no save to restore.",
        )

    archive_dir = archived.archive_path
    if not archive_dir.is_dir():
        raise ArchiveError(
            "archive_missing",
            f"Archive directory is missing at {archive_dir}. It may have been "
            "moved or deleted outside the app.",
        )

    save_srcs = sorted(p for p in archive_dir.iterdir() if p.is_file() and p.suffix == ".sav")
    if not save_srcs:
        raise ArchiveError(
            "archive_missing",
            f"No .sav files found in archive at {archive_dir}.",
        )

    # Require the game folder to be on the card. Saves on MinUI are bound
    # to the .m3u basename (which equals the folder name), so a save
    # without a corresponding game folder is orphaned and pointless.
    games = scan_games(sd_root, registry)
    on_card = next(
        (g for g in games if g.game_folder_name == archived.game_folder_name), None
    )
    if on_card is None:
        raise ArchiveError(
            "game_not_on_card",
            f"{archived.display_name} isn't on the card. Send it from the "
            "library first, then restore the save.",
        )

    saves_dir = sd_root / SAVES_DIR / archived.system_code
    saves_dir.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    for src in save_srcs:
        dest = saves_dir / src.name
        shutil.copy2(src, dest)
        restored.append(f"{SAVES_DIR}/{archived.system_code}/{src.name}")

    return {
        "restored": restored,
        "archive_path": str(archive_dir),
        "game_folder_name": archived.game_folder_name,
        "display_name": archived.display_name,
        "system_code": archived.system_code,
    }
