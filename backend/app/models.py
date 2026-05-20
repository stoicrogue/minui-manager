"""SQLAlchemy models. Phase 3 added LibraryGame; Phase 4 adds the
libretro-thumbnails listing cache."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.paths import LIBRARY_DIR


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    rom_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    @property
    def game_folder_name(self) -> str:
        """The folder name that will be used on the SD card (Phase 6)
        and for the box-art PNG (Phase 5)."""
        return f"{self.display_name} ({self.system_code})"

    @property
    def library_path(self) -> Path:
        return LIBRARY_DIR / self.system_code / self.rom_filename

    @property
    def boxart_path(self) -> Path:
        # Phase 5 will populate this file; the path is determined now.
        return LIBRARY_DIR / self.system_code / ".res" / f"{self.game_folder_name}.png"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "system_code": self.system_code,
            "rom_filename": self.rom_filename,
            "display_name": self.display_name,
            "game_folder_name": self.game_folder_name,
            "size_bytes": self.size_bytes,
            "added_at": self.added_at.isoformat(),
            "library_path": str(self.library_path),
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
