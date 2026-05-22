"""Library = the laptop-side ROM repository.

Layout::

    ./data/library/
        _pending/<draft_id>/<file1>[, <file2>, ...]       # post-upload, pre-confirm
        <CODE>/<game_folder_name>/<rom>[, <disc2>, ...]   # confirmed ROMs
        <CODE>/.res/<game_folder_name>.png                # cached art (Phase 5)

Multi-disk games store every disc file in the per-game folder and have
``LibraryGame.disc_filenames`` populated. Single-disk games still get a
folder; the folder simply contains one file. The .m3u playlist is
generated at sync time from ``disc_filenames`` — we don't store it on the
laptop side.

Functions here are sync because they touch the FS and SQLite — both
fast enough that running them in the request thread is fine for a
local single-user tool. The upload endpoint funnels file-upload IO
through ``asyncio.to_thread`` so it doesn't block the event loop.
"""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

PENDING_DIR_NAME = "_pending"
M3U_SUFFIX = ".m3u"


def _pending_root() -> Path:
    p = LIBRARY_DIR / PENDING_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _system_root(code: str) -> Path:
    p = LIBRARY_DIR / code
    p.mkdir(parents=True, exist_ok=True)
    return p


def _game_folder(code: str, game_folder_name: str) -> Path:
    return _system_root(code) / game_folder_name


def _write_m3u(folder: Path, game_folder_name: str, disc_filenames: list[str]) -> None:
    """Write the playlist next to the disc(s).

    The on-card layout MinUI reads is ``<folder>/<folder>.m3u`` + the
    disc files. We mirror that exactly in the library folder so the
    folder is a complete MinUI-ready bundle — handy for diffing,
    debugging, or copying out by hand. Sync rewrites this file on the
    card, so it's safe if the user edits the local copy.
    """
    if not disc_filenames:
        return
    m3u_path = folder / f"{game_folder_name}.m3u"
    m3u_path.write_text("\n".join(disc_filenames) + "\n", encoding="utf-8")


def _read_m3u_disc_list(m3u_path: Path) -> list[str]:
    """Return every non-empty, non-comment line from an m3u file."""
    try:
        text = m3u_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


@dataclass(frozen=True)
class UploadedDiscFile:
    """One file the user sent up in a multi-file upload."""

    original_filename: str
    file_path: Path


@dataclass(frozen=True)
class PendingUpload:
    """A draft upload sitting in _pending/ waiting for confirm.

    For single-file uploads ``files`` has one entry; for a multi-disk
    upload it has one entry per disc plus (optionally) an .m3u file the
    user dropped alongside. The .m3u is parsed at confirm time to decide
    disc order; if it's absent we sort the disc files lexicographically.
    """

    draft_id: str
    files: list[UploadedDiscFile]

    @property
    def primary_filename(self) -> str:
        """The filename used to seed system detection.

        Prefers the .m3u stem when one was uploaded (e.g. "Lunar (PS).m3u"
        → "Lunar (PS)"), otherwise falls back to the first disc.
        """
        m3u = next(
            (f for f in self.files if f.original_filename.lower().endswith(M3U_SUFFIX)),
            None,
        )
        if m3u is not None:
            return m3u.original_filename
        return self._disc_files()[0].original_filename if self._disc_files() else (
            self.files[0].original_filename if self.files else ""
        )

    # Single-file convenience accessors — preserved for tests/scripts that
    # predate multi-file upload support.
    @property
    def original_filename(self) -> str:
        return self.primary_filename

    @property
    def file_path(self) -> Path:
        return self.files[0].file_path if self.files else Path()

    @property
    def size_bytes(self) -> int:
        return sum(
            f.file_path.stat().st_size for f in self.files if f.file_path.exists()
        )

    def _disc_files(self) -> list[UploadedDiscFile]:
        return [
            f for f in self.files
            if not f.original_filename.lower().endswith(M3U_SUFFIX)
        ]

    def _m3u_file(self) -> UploadedDiscFile | None:
        return next(
            (f for f in self.files if f.original_filename.lower().endswith(M3U_SUFFIX)),
            None,
        )

    def disc_order(self) -> list[UploadedDiscFile]:
        """Return discs in playback order.

        Order rule: if the user uploaded an .m3u that mentions every disc
        file, honor that order. Otherwise sort the disc files by filename
        (which usually gives Disc 1 before Disc 2 for sensibly named ROMs).
        """
        discs = self._disc_files()
        m3u = self._m3u_file()
        if m3u is not None:
            wanted = _read_m3u_disc_list(m3u.file_path)
            by_name = {f.original_filename: f for f in discs}
            if wanted and all(name in by_name for name in wanted):
                return [by_name[name] for name in wanted]
        return sorted(discs, key=lambda f: f.original_filename.lower())

    def to_dict(self) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "original_filename": self.primary_filename,
            "size_bytes": self.size_bytes,
            "filenames": [f.original_filename for f in self.files],
            "disc_count": len(self._disc_files()),
            "is_multi_disk": len(self._disc_files()) > 1,
        }


# ---------------------------------------------------------------------------
# Drafts: upload + cancel + cleanup
# ---------------------------------------------------------------------------


def new_draft_dir() -> tuple[str, Path]:
    """Allocate a fresh draft id and create its directory.

    Returns ``(draft_id, draft_dir)``. The caller streams uploaded files
    into ``draft_dir`` (one chunk at a time, to keep memory bounded for
    multi-GB PS1 discs) and finishes by calling :func:`get_draft`.
    """
    draft_id = uuid.uuid4().hex
    draft_dir = _pending_root() / draft_id
    draft_dir.mkdir(parents=True, exist_ok=False)
    return draft_id, draft_dir


def safe_draft_filename(filename: str) -> str:
    """Strip any path components from an upload's reported name."""
    return Path(filename).name


def save_pending_upload(filename: str, content: bytes) -> PendingUpload:
    """Persist a single uploaded file under a fresh draft id.

    Kept for callers (tests, scripts) that want the old one-shot API. The
    HTTP upload endpoint uses :func:`new_draft_dir` directly so it can
    stream multi-file uploads chunk-by-chunk.
    """
    draft_id, draft_dir = new_draft_dir()
    safe_name = safe_draft_filename(filename)
    file_path = draft_dir / safe_name
    file_path.write_bytes(content)
    draft = get_draft(draft_id)
    assert draft is not None  # we just created it
    return draft


def get_draft(draft_id: str) -> PendingUpload | None:
    draft_dir = _pending_root() / draft_id
    if not _is_safe_draft_dir(draft_dir):
        return None
    if not draft_dir.is_dir():
        return None
    files = sorted(
        (f for f in draft_dir.iterdir() if f.is_file()),
        key=lambda f: f.name.lower(),
    )
    if not files:
        return None
    return PendingUpload(
        draft_id=draft_id,
        files=[
            UploadedDiscFile(original_filename=f.name, file_path=f)
            for f in files
        ],
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

    For multi-disk uploads every disc lands in ``<CODE>/<game_folder>/`` and
    ``disc_filenames`` is populated. For single-disk it's the same layout
    but ``disc_filenames`` stays NULL. The .m3u (if the user uploaded one)
    is discarded — sync regenerates a canonical one from ``disc_filenames``.

    Raises :class:`LibraryError` on:
      - draft missing or empty (no disc files)
      - filename or display-name collision within the system
    """
    draft = get_draft(draft_id)
    if draft is None:
        raise LibraryError("draft_not_found", f"No pending upload with id {draft_id}")

    ordered_discs = draft.disc_order()
    if not ordered_discs:
        raise LibraryError(
            "no_discs",
            f"Draft {draft_id} has no ROM files (only an .m3u was uploaded?).",
        )

    primary_rom = ordered_discs[0].original_filename
    game_folder_name = f"{display_name} ({system_code})"
    is_multi = len(ordered_discs) > 1

    # Pre-check collisions so we can give a meaningful error before doing
    # any filesystem mutation.
    existing_filename = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == system_code,
            LibraryGame.rom_filename == primary_rom,
        )
    )
    if existing_filename is not None:
        raise LibraryError(
            "duplicate_rom",
            (
                f"A ROM named '{primary_rom}' is already in the "
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

    # Move every disc into _pending/<id>/ → <CODE>/<game_folder>/
    dest_folder = _game_folder(system_code, game_folder_name)
    if dest_folder.exists():
        raise LibraryError(
            "duplicate_rom",
            f"A folder named '{game_folder_name}' already exists in {system_code}/.",
        )
    dest_folder.mkdir(parents=True, exist_ok=False)

    moved: list[tuple[Path, Path]] = []  # (src, dest) for rollback
    try:
        for disc in ordered_discs:
            dest = dest_folder / disc.original_filename
            shutil.move(str(disc.file_path), str(dest))
            moved.append((disc.file_path, dest))
    except OSError as exc:
        for _src, dest in moved:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            dest_folder.rmdir()
        except OSError:
            pass
        raise LibraryError(
            "move_failed", f"Could not move disc files into the library: {exc}"
        ) from exc

    # Drain the rest of the draft folder (any leftover .m3u, etc.).
    try:
        shutil.rmtree(draft.files[0].file_path.parent)
    except OSError:
        pass

    # Write the canonical .m3u inside the game folder so the layout
    # matches what the device sees after sync.
    disc_names = [d.original_filename for d in ordered_discs]
    _write_m3u(dest_folder, game_folder_name, disc_names)

    total_size = sum(p.stat().st_size for _src, p in moved)
    disc_filenames = (
        json.dumps(disc_names)
        if is_multi
        else None
    )
    row = LibraryGame(
        system_code=system_code,
        rom_filename=primary_rom,
        display_name=display_name,
        size_bytes=total_size,
        disc_filenames=disc_filenames,
    )
    session.add(row)
    try:
        session.flush()  # surfaces unique-constraint violations as IntegrityError
    except IntegrityError as exc:
        # Roll back the filesystem move so a failed insert doesn't leave
        # orphan files behind.
        shutil.rmtree(dest_folder, ignore_errors=True)
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

    # Resolve every disc file on the card. The scanner already verified
    # the .m3u and the primary ROM; we extend that here for the multi-disk
    # case by walking ``disc_filenames`` (each is relative to the game
    # folder, per the MinUI Five-Game convention).
    card_folder = Path(card_game.folder_path)
    disc_names = list(card_game.disc_filenames) or [card_game.rom_filename]
    disc_srcs: list[Path] = []
    for name in disc_names:
        src = card_folder / name
        if not src.is_file():
            raise LibraryError(
                "malformed",
                f"Disc '{name}' is listed in the .m3u but missing in '{game_folder_name}'.",
            )
        disc_srcs.append(src)

    dest_dir = _system_root(card_game.system_code)
    dest_folder = dest_dir / card_game.game_folder_name
    if dest_folder.exists():
        raise LibraryError(
            "duplicate_rom",
            f"A folder named '{card_game.game_folder_name}' already exists in {card_game.system_code}/.",
        )
    dest_folder.mkdir(parents=True, exist_ok=False)

    dest_discs: list[Path] = []
    dest_art: Path | None = None
    try:
        for src, name in zip(disc_srcs, disc_names):
            dest = dest_folder / name
            shutil.copy2(str(src), str(dest))
            dest_discs.append(dest)
        _write_m3u(dest_folder, card_game.game_folder_name, disc_names)

        # Copy box art if it's present on the card. Failure is non-fatal —
        # the user can pick art later via the boxart router.
        if card_game.has_boxart and card_game.boxart_path:
            src_art = Path(card_game.boxart_path)
            if src_art.is_file():
                art_dir = dest_dir / ".res"
                art_dir.mkdir(parents=True, exist_ok=True)
                dest_art = art_dir / f"{card_game.game_folder_name}.png"
                try:
                    shutil.copy2(str(src_art), str(dest_art))
                except OSError:
                    dest_art = None
    except OSError as exc:
        shutil.rmtree(dest_folder, ignore_errors=True)
        raise LibraryError(
            "copy_failed", f"Could not copy disc files into the library: {exc}"
        ) from exc

    total_size = sum(p.stat().st_size for p in dest_discs)
    disc_filenames_json = (
        json.dumps(disc_names) if len(disc_names) > 1 else None
    )
    row = LibraryGame(
        system_code=card_game.system_code,
        rom_filename=card_game.rom_filename,
        display_name=card_game.display_name,
        size_bytes=total_size,
        disc_filenames=disc_filenames_json,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        shutil.rmtree(dest_folder, ignore_errors=True)
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
    """Delete the DB row + the on-disk game folder + cached box art."""
    row = session.get(LibraryGame, id_)
    if row is None:
        return False
    folder = row.library_folder
    art = row.boxart_path
    session.delete(row)
    session.flush()
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    if art.is_file():
        art.unlink()
    return True


# ---------------------------------------------------------------------------
# Layout migration: move legacy flat ROMs into per-game folders
# ---------------------------------------------------------------------------


def migrate_legacy_flat_layout(session: Session) -> int:
    """Move every legacy ``<CODE>/<rom>`` into ``<CODE>/<game_folder>/<rom>``.

    Older library entries (before multi-disk landed) stored each ROM
    directly under the system folder. We now always use a per-game
    sub-folder so single- and multi-disk share the same layout. This
    runs on startup; idempotent — if a row is already in folder layout
    it's left alone, and the missing .m3u (if any) is backfilled.

    Returns the number of rows actually migrated (moved or backfilled).
    Logs each change.
    """
    moved = 0
    rows = session.scalars(select(LibraryGame)).all()
    for row in rows:
        new_folder = row.library_folder
        new_path = new_folder / row.rom_filename
        already_migrated = new_path.is_file()

        if not already_migrated:
            legacy_path = LIBRARY_DIR / row.system_code / row.rom_filename
            if not legacy_path.is_file():
                # File missing under both layouts — nothing to migrate. The
                # backup-export code will surface this as "ROM file missing".
                continue
            new_folder.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(legacy_path), str(new_path))
            except OSError as exc:
                logger.warning(
                    "Could not migrate %s to per-game folder layout: %s",
                    legacy_path,
                    exc,
                )
                continue
            moved += 1
            logger.info(
                "Migrated %s/%s into per-game folder layout.",
                row.system_code,
                row.game_folder_name,
            )

        # Backfill the .m3u inside the folder so every game has a
        # MinUI-ready playlist locally, not just after sync.
        m3u_path = new_folder / f"{row.game_folder_name}.m3u"
        if not m3u_path.is_file():
            try:
                _write_m3u(new_folder, row.game_folder_name, row.disc_filenames_list)
            except OSError as exc:
                logger.warning(
                    "Could not write .m3u for %s/%s: %s",
                    row.system_code,
                    row.game_folder_name,
                    exc,
                )
    return moved
