"""Library = the laptop-side ROM repository.

Layout::

    ./data/library/
        _pending/<draft_id>/<original_filename>      # post-upload, pre-confirm
        <CODE>/<rom_filename>                         # confirmed ROMs
        <CODE>/.res/<game_folder_name>.png            # cached art (Phase 5)

Functions here are sync because they touch the FS and SQLite — both
fast enough that running them in the request thread is fine for a
local single-user tool. The upload endpoint funnels file-upload IO
through ``asyncio.to_thread`` so it doesn't block the event loop.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import LibraryGame
from app.paths import LIBRARY_DIR
from app.services.sdcard_reader import SDCardGame, scan_games
from app.services.system_registry import SystemRegistry

PENDING_DIR_NAME = "_pending"


def _pending_root() -> Path:
    p = LIBRARY_DIR / PENDING_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _system_root(code: str) -> Path:
    p = LIBRARY_DIR / code
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class PendingUpload:
    """A draft upload sitting in _pending/ waiting for confirm."""

    draft_id: str
    original_filename: str
    file_path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "original_filename": self.original_filename,
            "size_bytes": self.file_path.stat().st_size if self.file_path.exists() else 0,
        }


# ---------------------------------------------------------------------------
# Drafts: upload + cancel + cleanup
# ---------------------------------------------------------------------------

def save_pending_upload(filename: str, content: bytes) -> PendingUpload:
    """Persist an uploaded file under a fresh draft id. Caller is responsible
    for calling ``confirm_draft`` or ``cancel_draft`` afterward.
    """
    draft_id = uuid.uuid4().hex
    draft_dir = _pending_root() / draft_id
    draft_dir.mkdir(parents=True, exist_ok=False)

    safe_name = Path(filename).name  # strip any path components
    file_path = draft_dir / safe_name
    file_path.write_bytes(content)
    return PendingUpload(
        draft_id=draft_id, original_filename=safe_name, file_path=file_path
    )


def get_draft(draft_id: str) -> PendingUpload | None:
    draft_dir = _pending_root() / draft_id
    if not _is_safe_draft_dir(draft_dir):
        return None
    if not draft_dir.is_dir():
        return None
    files = [f for f in draft_dir.iterdir() if f.is_file()]
    if not files:
        return None
    # A draft folder contains exactly one file in normal operation.
    return PendingUpload(
        draft_id=draft_id,
        original_filename=files[0].name,
        file_path=files[0],
    )


def cancel_draft(draft_id: str) -> bool:
    """Remove a pending draft. Returns True if anything was deleted."""
    draft_dir = _pending_root() / draft_id
    if not _is_safe_draft_dir(draft_dir) or not draft_dir.is_dir():
        return False
    shutil.rmtree(draft_dir)
    return True


def cleanup_stale_drafts() -> int:
    """Wipe every existing draft. Called on app startup so we don't leak
    half-finished uploads after a server restart.
    """
    pending = _pending_root()
    if not pending.is_dir():
        return 0
    count = 0
    for child in pending.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            count += 1
    return count


def _is_safe_draft_dir(path: Path) -> bool:
    """Guard against ``draft_id`` values that try to escape ``_pending/``."""
    try:
        path.relative_to(_pending_root())
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Confirm: move from _pending → <CODE>/, insert DB row
# ---------------------------------------------------------------------------


class LibraryError(Exception):
    """Recoverable error the API can surface as a 4xx."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def confirm_draft(
    session: Session,
    draft_id: str,
    system_code: str,
    display_name: str,
) -> LibraryGame:
    """Move the draft into the library and write a DB row.

    Raises :class:`LibraryError` on:
      - draft missing
      - filename or display-name collision within the system
    """
    draft = get_draft(draft_id)
    if draft is None:
        raise LibraryError("draft_not_found", f"No pending upload with id {draft_id}")

    # Pre-check collisions so we can give a meaningful error before doing
    # any filesystem mutation.
    existing_filename = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == system_code,
            LibraryGame.rom_filename == draft.original_filename,
        )
    )
    if existing_filename is not None:
        raise LibraryError(
            "duplicate_rom",
            (
                f"A ROM named '{draft.original_filename}' is already in the "
                f"{system_code} library."
            ),
        )
    existing_display = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == system_code,
            LibraryGame.display_name == display_name,
        )
    )
    if existing_display is not None:
        raise LibraryError(
            "duplicate_display_name",
            (
                f"A {system_code} game is already named '{display_name}'. Pick a "
                "different display name (e.g. add a version suffix) or delete "
                "the existing entry first."
            ),
        )

    # Move the file: _pending/<id>/<file>  →  <CODE>/<file>
    dest_dir = _system_root(system_code)
    dest = dest_dir / draft.original_filename
    if dest.exists():
        # Shouldn't happen given the DB check above, but defend against it.
        raise LibraryError(
            "duplicate_rom",
            f"A file named '{draft.original_filename}' already exists in {system_code}/.",
        )
    shutil.move(str(draft.file_path), str(dest))
    # Remove the now-empty draft folder.
    try:
        draft.file_path.parent.rmdir()
    except OSError:
        # Non-empty (somebody put another file in the draft dir?); leave it.
        pass

    row = LibraryGame(
        system_code=system_code,
        rom_filename=draft.original_filename,
        display_name=display_name,
        size_bytes=dest.stat().st_size,
    )
    session.add(row)
    try:
        session.flush()  # surfaces unique-constraint violations as IntegrityError
    except IntegrityError as exc:
        # Roll back filesystem move: shutil.move's already done. Try to undo.
        try:
            shutil.move(str(dest), str(draft.file_path))
        except OSError:
            pass
        raise LibraryError(
            "integrity_error", f"Database rejected the insert: {exc.orig}"
        ) from exc
    return row


# ---------------------------------------------------------------------------
# List / read / delete
# ---------------------------------------------------------------------------

def list_library(session: Session, system_code: str | None = None) -> list[LibraryGame]:
    stmt = select(LibraryGame)
    if system_code is not None:
        stmt = stmt.where(LibraryGame.system_code == system_code)
    stmt = stmt.order_by(LibraryGame.system_code, LibraryGame.display_name)
    return list(session.scalars(stmt))


def get_library_game(session: Session, id_: int) -> LibraryGame | None:
    return session.get(LibraryGame, id_)


# ---------------------------------------------------------------------------
# Import: copy a game from the SD card into the library
# ---------------------------------------------------------------------------


def _find_card_game(sd_root: Path, registry: SystemRegistry, game_folder_name: str) -> SDCardGame | None:
    """Return the named card game, or None if it isn't on the card.

    The scanner has already done the (CODE) parsing and malformed checks,
    so we just look up by folder name.
    """
    if "/" in game_folder_name or "\\" in game_folder_name or game_folder_name in {".", ".."}:
        return None
    for game in scan_games(sd_root, registry):
        if game.game_folder_name == game_folder_name:
            return game
    return None


def import_from_sd_card(
    session: Session,
    sd_root: Path,
    registry: SystemRegistry,
    game_folder_name: str,
) -> LibraryGame:
    """Copy a game from the SD card into the library.

    Pulls the ROM + box art (if present) into ``./data/library/<CODE>/`` and
    creates a ``LibraryGame`` row. Does not touch the card. Saves are
    intentionally not pulled — those stay bound to the device lifecycle.

    Raises :class:`LibraryError` on:
      - card game missing
      - card game malformed (no .m3u, missing ROM file)
      - rom_filename or display_name collides with an existing library entry
    """
    card_game = _find_card_game(sd_root, registry, game_folder_name)
    if card_game is None:
        raise LibraryError(
            "not_on_card",
            f"No game folder named '{game_folder_name}' on the SD card.",
        )
    if card_game.is_malformed or not card_game.has_rom_file or not card_game.rom_filename:
        raise LibraryError(
            "malformed",
            (
                f"'{game_folder_name}' is malformed and can't be imported: "
                f"{card_game.malformed_reason or 'ROM file missing'}."
            ),
        )

    # Collision checks mirror confirm_draft so the two paths behave the same.
    existing_filename = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == card_game.system_code,
            LibraryGame.rom_filename == card_game.rom_filename,
        )
    )
    if existing_filename is not None:
        raise LibraryError(
            "duplicate_rom",
            (
                f"A ROM named '{card_game.rom_filename}' is already in the "
                f"{card_game.system_code} library."
            ),
        )
    existing_display = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == card_game.system_code,
            LibraryGame.display_name == card_game.display_name,
        )
    )
    if existing_display is not None:
        raise LibraryError(
            "duplicate_display_name",
            (
                f"A {card_game.system_code} game is already named "
                f"'{card_game.display_name}'. Rename the existing library "
                "entry or delete it before importing again."
            ),
        )

    # Copy ROM: Roms/<folder>/<rom>  →  data/library/<CODE>/<rom>
    src_rom = Path(card_game.rom_path) if card_game.rom_path else None
    if src_rom is None or not src_rom.is_file():
        # Defensive — the malformed check above should have caught this.
        raise LibraryError("malformed", f"Source ROM for '{game_folder_name}' is unreadable.")
    dest_dir = _system_root(card_game.system_code)
    dest_rom = dest_dir / card_game.rom_filename
    if dest_rom.exists():
        raise LibraryError(
            "duplicate_rom",
            f"A file named '{card_game.rom_filename}' already exists in {card_game.system_code}/.",
        )
    shutil.copy2(str(src_rom), str(dest_rom))

    # Copy box art if it's present on the card. Failure is non-fatal —
    # the user can pick art later via the boxart router.
    dest_art: Path | None = None
    if card_game.has_boxart and card_game.boxart_path:
        src_art = Path(card_game.boxart_path)
        if src_art.is_file():
            art_dir = dest_dir / ".res"
            art_dir.mkdir(parents=True, exist_ok=True)
            dest_art = art_dir / f"{card_game.game_folder_name}.png"
            try:
                shutil.copy2(str(src_art), str(dest_art))
            except OSError:
                # Don't fail the whole import on a missing/locked art file.
                dest_art = None

    row = LibraryGame(
        system_code=card_game.system_code,
        rom_filename=card_game.rom_filename,
        display_name=card_game.display_name,
        size_bytes=dest_rom.stat().st_size,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        # Roll back the filesystem copies so a failed insert doesn't leave
        # orphan files in the library.
        try:
            dest_rom.unlink(missing_ok=True)
        except OSError:
            pass
        if dest_art is not None:
            try:
                dest_art.unlink(missing_ok=True)
            except OSError:
                pass
        raise LibraryError(
            "integrity_error", f"Database rejected the insert: {exc.orig}"
        ) from exc
    return row


def populate_library_matches(session: Session, games: list[SDCardGame]) -> None:
    """Fill in ``matches_library_id`` for each card game in place.

    A card game matches a library entry when both ``system_code`` and
    ``rom_filename`` are equal — that's the same key the unique constraint
    enforces, so at most one library entry can match.
    """
    if not games:
        return
    rows = session.execute(
        select(LibraryGame.id, LibraryGame.system_code, LibraryGame.rom_filename)
    ).all()
    index = {(r.system_code, r.rom_filename): r.id for r in rows}
    for g in games:
        if g.rom_filename is None:
            continue
        g.matches_library_id = index.get((g.system_code, g.rom_filename))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_library_game(session: Session, id_: int) -> bool:
    """Delete the DB row + the on-disk ROM (and cached box art if present)."""
    row = session.get(LibraryGame, id_)
    if row is None:
        return False
    rom = row.library_path
    art = row.boxart_path
    session.delete(row)
    session.flush()
    if rom.is_file():
        rom.unlink()
    if art.is_file():
        art.unlink()
    return True
