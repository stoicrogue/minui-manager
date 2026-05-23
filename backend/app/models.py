"""SQLAlchemy models. Phase 3 added LibraryGame; Phase 4 adds the
libretro-thumbnails listing cache; Phase 7 adds ArchivedGame.

Multi-disk games: ``LibraryGame.disc_filenames`` is a JSON-encoded list
of disc filenames when the game has more than one disc. NULL means
single-disk (``rom_filename`` is the only file). The on-disk layout is
always per-game: ``data/library/<CODE>/<game_folder_name>/<disc>``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app import paths as _paths
from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    """Serialize a datetime as an ISO 8601 string with explicit UTC offset.

    The SQLAlchemy ``DateTime`` columns here store wall-clock UTC but strip
    the tzinfo on round-trip via SQLite. Calling ``.isoformat()`` on a naive
    datetime produces a string without a timezone suffix, which the browser
    then parses as **local** time -- making UTC timestamps render shifted by
    the user's UTC offset. Forcing the UTC tzinfo before isoformat fixes
    that: the wire format becomes ``2026-05-21T18:00:00+00:00`` which JS
    parses as UTC and the Angular ``date`` pipe converts to local time for
    display.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class LibraryGame(Base):
    """A ROM that's been uploaded to the laptop library.

    The on-disk file lives at ``./data/library/<system_code>/<rom_filename>``;
    box art (added in Phase 5) will live at
    ``./data/library/<system_code>/.res/<game_folder_name>.png``.
    """

    __tablename__ = "library_games"
    __table_args__ = (
        # No two library entries can share the same rom filename within
        # the same system — that would mean a duplicate upload.
        UniqueConstraint("system_code", "rom_filename", name="uq_library_rom"),
        # No two library entries can share the same display name within
        # the same system — that would collide on the SD card folder name
        # at sync time.
        UniqueConstraint("system_code", "display_name", name="uq_library_display"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # For multi-disk games, this is the first disc — kept so the existing
    # uniqueness constraint still gives one row per logical game. The
    # authoritative disc list is ``disc_filenames``.
    rom_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    # JSON-encoded list of disc filenames in playback order. NULL means
    # single-disk (treat as ``[rom_filename]``).
    disc_filenames: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    @property
    def game_folder_name(self) -> str:
        """The folder name that will be used on the SD card (Phase 6)
        and for the box-art PNG (Phase 5)."""
        return f"{self.display_name} ({self.system_code})"

    @property
    def disc_filenames_list(self) -> list[str]:
        """Parsed disc list. Falls back to ``[rom_filename]`` for single-disk."""
        if not self.disc_filenames:
            return [self.rom_filename]
        try:
            data = json.loads(self.disc_filenames)
        except (json.JSONDecodeError, TypeError):
            return [self.rom_filename]
        if not isinstance(data, list) or not data:
            return [self.rom_filename]
        return [str(x) for x in data]

    @property
    def is_multi_disk(self) -> bool:
        return len(self.disc_filenames_list) > 1

    @property
    def library_folder(self) -> Path:
        """The per-game folder that holds the ROM(s) on disk."""
        return _paths.LIBRARY_DIR / self.system_code / self.game_folder_name

    @property
    def disc_paths(self) -> list[Path]:
        """Absolute paths to each disc file in playback order."""
        folder = self.library_folder
        return [folder / disc for disc in self.disc_filenames_list]

    @property
    def m3u_content(self) -> str:
        """The exact bytes to write into ``<folder>.m3u`` at sync time."""
        return "\n".join(self.disc_filenames_list) + "\n"

    @property
    def boxart_path(self) -> Path:
        return _paths.LIBRARY_DIR / self.system_code / ".res" / f"{self.game_folder_name}.png"

    def to_public_dict(self) -> dict[str, object]:
        discs = self.disc_filenames_list
        return {
            "id": self.id,
            "system_code": self.system_code,
            "rom_filename": self.rom_filename,
            "display_name": self.display_name,
            "game_folder_name": self.game_folder_name,
            "size_bytes": self.size_bytes,
            "added_at": _iso_utc(self.added_at),
            "library_path": str(self.library_folder),
            "disc_filenames": discs,
            "is_multi_disk": len(discs) > 1,
            "disc_count": len(discs),
            "has_boxart": self.boxart_path.is_file(),
            "boxart_path": str(self.boxart_path) if self.boxart_path.is_file() else None,
        }


class LibretroListingCache(Base):
    """Cached libretro-thumbnails directory listing per repo.

    Refreshed at most once every 24h (see boxart_libretro.LISTING_TTL).
    The listing is the JSON payload from the GitHub Contents API: a list
    of ``{"name": "...", "download_url": "https://raw..."}`` entries.
    """

    __tablename__ = "libretro_listing_cache"

    repo: Mapped[str] = mapped_column(String(256), primary_key=True)
    listing_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class ArchivedGame(Base):
    """A snapshot of a game's save file(s) taken when it was removed from
    the SD card.

    Each row points at a timestamped directory under ``./data/archive/``
    that holds only the ``.sav`` file(s) that were on the card at remove
    time. The library is the canonical backup for the ROM and box art.
    ``archive_relpath`` is relative to :data:`app.paths.ARCHIVE_DIR` so
    the archive remains valid if the project root moves.

    ``has_boxart`` and ``disc_filenames`` are kept as columns to avoid a
    migration, but the archive no longer carries either — both are
    always falsy on new rows.
    """

    __tablename__ = "archived_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    game_folder_name: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    rom_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    archive_relpath: Mapped[str] = mapped_column(String(1024), nullable=False)
    has_save: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_boxart: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    disc_filenames: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    @property
    def archive_path(self) -> Path:
        return _paths.ARCHIVE_DIR / self.archive_relpath

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "system_code": self.system_code,
            "game_folder_name": self.game_folder_name,
            "display_name": self.display_name,
            "rom_filename": self.rom_filename,
            "archive_path": str(self.archive_path),
            "archive_relpath": self.archive_relpath,
            "has_save": self.has_save,
            "has_boxart": self.has_boxart,
            "archived_at": _iso_utc(self.archived_at),
        }
