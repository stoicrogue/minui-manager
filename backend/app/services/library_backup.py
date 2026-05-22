"""Export / import the laptop-side library as a single zip (Phase 8).

Scope: ROMs + cached box art under ``./data/library/`` and just enough
DB-derived metadata (display_name, added_at, system_code) to recreate
the rows on import. Archive, settings, sync log, and the libretro
listing cache are explicitly NOT included — if the user wants a full
app backup they can zip ``./data/`` themselves.

Zip layout::

    library-manifest.json
    <CODE>/<rom-filename>
    <CODE>/.res/<game-folder>.png

The manifest is mandatory and versioned. Entries are sorted by
``(system_code, rom_filename)`` so two exports of the same library
produce byte-identical zips (modulo timestamps).

Import is conservative: on any per-entry problem (path traversal,
collision with existing library row, unknown system code, missing
file in the zip) the entry is **skipped with a reason** and the rest
of the import continues. There is no overwrite/force flag in v1.
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paths as _paths
from app.models import LibraryGame
from app.services.library_store import PENDING_DIR_NAME
from app.services.system_registry import load_systems

logger = logging.getLogger(__name__)

MANIFEST_NAME = "library-manifest.json"
# v1: single ROM per entry, flat <CODE>/<rom> layout.
# v2: per-game folder layout, ``disc_filenames`` for multi-disk games.
MANIFEST_VERSION = 2
SUPPORTED_MANIFEST_VERSIONS = (1, 2)
SHARED_RES_DIR = ".res"


# ---------------------------------------------------------------------------
# Manifest schema (typed contract through the middle of the feature)
# ---------------------------------------------------------------------------


class ManifestEntry(BaseModel):
    system_code: str = Field(..., min_length=1, max_length=16)
    rom_filename: str = Field(..., min_length=1, max_length=512)
    display_name: str = Field(..., min_length=1, max_length=512)
    size_bytes: int = Field(..., ge=0)
    added_at: datetime
    rom_path: str  # relative path of the primary disc inside the zip
    boxart_path: str | None = None
    boxart_size_bytes: int | None = None
    # v2 fields — absent in v1 backups (loader fills sensibly).
    disc_filenames: list[str] = Field(default_factory=list)
    disc_paths: list[str] = Field(default_factory=list)

    @property
    def game_folder_name(self) -> str:
        return f"{self.display_name} ({self.system_code})"

    @property
    def effective_discs(self) -> list[tuple[str, str]]:
        """Return ``[(filename, zip_path), ...]`` in playback order.

        v2 entries always carry ``disc_filenames``/``disc_paths``. v1
        entries (legacy) are treated as single-disk where the only disc
        lives at ``rom_path`` with the name ``rom_filename``.
        """
        if self.disc_filenames and self.disc_paths and len(self.disc_filenames) == len(self.disc_paths):
            return list(zip(self.disc_filenames, self.disc_paths))
        return [(self.rom_filename, self.rom_path)]


class LibraryManifest(BaseModel):
    version: Literal[1, 2] = MANIFEST_VERSION
    exported_at: datetime
    games: list[ManifestEntry]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@dataclass
class ExportResult:
    games_written: int
    files_written: int
    bytes_written: int
    skipped: list[dict[str, str]] = field(default_factory=list)


def _library_root() -> Path:
    return _paths.LIBRARY_DIR


def _disc_zip_path(code: str, game_folder_name: str, disc_filename: str) -> str:
    return f"{code}/{game_folder_name}/{disc_filename}"


def _boxart_zip_path(code: str, game_folder_name: str) -> str:
    return f"{code}/{SHARED_RES_DIR}/{game_folder_name}.png"


def export_library(session: Session, dest_path: Path) -> ExportResult:
    """Write a library backup zip to ``dest_path``.

    Walks the DB (not the filesystem) so library rows whose files have
    been deleted out from under us get flagged in ``skipped`` rather
    than silently included. Every disc of a multi-disk game is included
    under ``<CODE>/<game-folder>/<disc>``. ``_pending/`` is never bundled.
    """
    rows = session.scalars(
        select(LibraryGame).order_by(LibraryGame.system_code, LibraryGame.rom_filename)
    ).all()

    result = ExportResult(games_written=0, files_written=0, bytes_written=0)
    manifest_entries: list[ManifestEntry] = []

    with zipfile.ZipFile(dest_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            discs = row.disc_filenames_list
            disc_paths = row.disc_paths
            missing = [p for p in disc_paths if not p.is_file()]
            if missing:
                result.skipped.append(
                    {
                        "system_code": row.system_code,
                        "rom_filename": row.rom_filename,
                        "reason": f"Disc file(s) missing on disk: {', '.join(str(p) for p in missing)}",
                    }
                )
                continue

            zip_disc_paths: list[str] = []
            for disc_name, disc_path in zip(discs, disc_paths):
                arc = _disc_zip_path(row.system_code, row.game_folder_name, disc_name)
                zf.write(disc_path, arcname=arc)
                result.files_written += 1
                result.bytes_written += disc_path.stat().st_size
                zip_disc_paths.append(arc)

            boxart_zip: str | None = None
            boxart_size: int | None = None
            art = row.boxart_path
            if art.is_file():
                boxart_zip = _boxart_zip_path(row.system_code, row.game_folder_name)
                zf.write(art, arcname=boxart_zip)
                result.files_written += 1
                result.bytes_written += art.stat().st_size
                boxart_size = art.stat().st_size

            manifest_entries.append(
                ManifestEntry(
                    system_code=row.system_code,
                    rom_filename=row.rom_filename,
                    display_name=row.display_name,
                    size_bytes=row.size_bytes,
                    added_at=row.added_at,
                    rom_path=zip_disc_paths[0],
                    boxart_path=boxart_zip,
                    boxart_size_bytes=boxart_size,
                    disc_filenames=discs,
                    disc_paths=zip_disc_paths,
                )
            )

        manifest = LibraryManifest(
            version=MANIFEST_VERSION,
            exported_at=datetime.now(timezone.utc),
            games=manifest_entries,
        )
        zf.writestr(MANIFEST_NAME, manifest.model_dump_json(indent=2))

    result.games_written = len(manifest_entries)
    return result


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@dataclass
class ImportEntryResult:
    system_code: str
    rom_filename: str
    display_name: str
    status: Literal["restored", "skipped"]
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "system_code": self.system_code,
            "rom_filename": self.rom_filename,
            "display_name": self.display_name,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class ImportResult:
    restored: int
    skipped: int
    entries: list[ImportEntryResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "restored": self.restored,
            "skipped": self.skipped,
            "entries": [e.to_dict() for e in self.entries],
        }


class LibraryImportError(Exception):
    """Whole-import failure (bad zip, missing manifest, version mismatch)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def import_library(session: Session, src_path: Path) -> ImportResult:
    """Read a library backup zip and recreate entries that don't collide.

    Raises :class:`LibraryImportError` for whole-archive problems (not a
    zip, missing manifest, version mismatch). Per-entry problems are
    recorded in the result, never raised.
    """
    if not zipfile.is_zipfile(src_path):
        raise LibraryImportError("not_a_zip", "The uploaded file is not a valid zip.")

    with zipfile.ZipFile(src_path, mode="r") as zf:
        try:
            raw_manifest = zf.read(MANIFEST_NAME).decode("utf-8")
        except KeyError as exc:
            raise LibraryImportError(
                "missing_manifest",
                f"Zip is missing the required {MANIFEST_NAME!r} entry.",
            ) from exc

        try:
            manifest_dict = json.loads(raw_manifest)
        except json.JSONDecodeError as exc:
            raise LibraryImportError(
                "corrupt_manifest", f"Manifest JSON is malformed: {exc}"
            ) from exc

        version = manifest_dict.get("version")
        if version not in SUPPORTED_MANIFEST_VERSIONS:
            raise LibraryImportError(
                "version_mismatch",
                f"Manifest version is {version!r}; this build understands "
                f"versions {SUPPORTED_MANIFEST_VERSIONS}.",
            )

        try:
            manifest = LibraryManifest.model_validate(manifest_dict)
        except ValidationError as exc:
            raise LibraryImportError(
                "invalid_manifest", f"Manifest failed validation: {exc}"
            ) from exc

        registry = load_systems()
        library_root = _library_root()
        library_root.mkdir(parents=True, exist_ok=True)
        result = ImportResult(restored=0, skipped=0)

        for entry in manifest.games:
            er = _import_one(session, zf, entry, registry, library_root)
            result.entries.append(er)
            if er.status == "restored":
                result.restored += 1
            else:
                result.skipped += 1

    return result


def _import_one(
    session: Session,
    zf: zipfile.ZipFile,
    entry: ManifestEntry,
    registry,
    library_root: Path,
) -> ImportEntryResult:
    """Restore one manifest entry. Per-entry failures return a skipped result.

    v1 manifests describe a single flat file at ``rom_path``; v2 manifests
    describe one-or-more discs at ``disc_paths``. ``effective_discs`` on
    the entry hides the difference. Either way, the on-disk destination
    is always the per-game folder.
    """
    base = ImportEntryResult(
        system_code=entry.system_code,
        rom_filename=entry.rom_filename,
        display_name=entry.display_name,
        status="skipped",
    )

    if entry.system_code not in registry.codes:
        return _skip(base, f"Unknown system code {entry.system_code!r}.")

    # Conflict checks against existing rows.
    existing_filename = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == entry.system_code,
            LibraryGame.rom_filename == entry.rom_filename,
        )
    )
    if existing_filename is not None:
        return _skip(
            base,
            f"A ROM named {entry.rom_filename!r} is already in the "
            f"{entry.system_code} library — delete it first to re-import.",
        )
    existing_display = session.scalar(
        select(LibraryGame).where(
            LibraryGame.system_code == entry.system_code,
            LibraryGame.display_name == entry.display_name,
        )
    )
    if existing_display is not None:
        return _skip(
            base,
            f"A {entry.system_code} game named {entry.display_name!r} already "
            "exists — delete it first to re-import.",
        )

    discs = entry.effective_discs  # [(filename, zip_path), ...]
    game_folder = library_root / entry.system_code / entry.game_folder_name

    # Resolve destinations and verify each member exists in the zip. The
    # zip member name is also validated even though we never use it as a
    # destination — a v1 manifest that points at ``../escape.bin`` is a
    # signal of tampering, not a legitimate backup, and we reject it
    # outright.
    disc_dests: list[Path] = []
    disc_bytes: list[bytes] = []
    for disc_filename, zip_path in discs:
        try:
            _safe_extract_path(zip_path, library_root)
        except _UnsafeZipPath as exc:
            return _skip(base, str(exc))
        try:
            dest = _safe_extract_path(
                f"{entry.system_code}/{entry.game_folder_name}/{disc_filename}",
                library_root,
            )
        except _UnsafeZipPath as exc:
            return _skip(base, str(exc))
        try:
            data = zf.read(zip_path)
        except KeyError:
            return _skip(base, f"Disc file {zip_path!r} not present in the zip.")
        disc_dests.append(dest)
        disc_bytes.append(data)

    art_dest: Path | None = None
    art_bytes: bytes | None = None
    if entry.boxart_path is not None:
        try:
            art_dest = _safe_extract_path(entry.boxart_path, library_root)
        except _UnsafeZipPath as exc:
            return _skip(base, str(exc))
        try:
            art_bytes = zf.read(entry.boxart_path)
        except KeyError:
            logger.warning(
                "Zip manifest references missing boxart %s for %s — restoring ROM only.",
                entry.boxart_path,
                entry.rom_filename,
            )
            art_bytes = None

    # Write the files.
    game_folder.mkdir(parents=True, exist_ok=True)
    for dest, data in zip(disc_dests, disc_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    # Canonical .m3u next to the discs (matches the on-card layout).
    from app.services.library_store import _write_m3u
    _write_m3u(game_folder, entry.game_folder_name, [d[0] for d in discs])
    if art_bytes is not None and art_dest is not None:
        art_dest.parent.mkdir(parents=True, exist_ok=True)
        art_dest.write_bytes(art_bytes)

    # Insert the row.
    disc_names = [d[0] for d in discs]
    total_size = sum(p.stat().st_size for p in disc_dests)
    disc_filenames_json = (
        json.dumps(disc_names) if len(disc_names) > 1 else None
    )
    row = LibraryGame(
        system_code=entry.system_code,
        rom_filename=entry.rom_filename,
        display_name=entry.display_name,
        size_bytes=total_size,
        added_at=entry.added_at,
        disc_filenames=disc_filenames_json,
    )
    session.add(row)
    session.flush()

    base.status = "restored"
    return base


def _skip(base: ImportEntryResult, reason: str) -> ImportEntryResult:
    base.status = "skipped"
    base.reason = reason
    return base


# ---------------------------------------------------------------------------
# Zip-slip defense
# ---------------------------------------------------------------------------


class _UnsafeZipPath(ValueError):
    pass


def _safe_extract_path(member_name: str, root: Path) -> Path:
    """Resolve a zip member name to an absolute path under ``root``.

    Raises :class:`_UnsafeZipPath` if the member tries to escape the
    library root via ``..``, absolute paths, NUL bytes, or symlinks.
    """
    if "\x00" in member_name:
        raise _UnsafeZipPath("Zip member name contains a NUL byte.")
    if PurePosixPath(member_name.replace("\\", "/")).is_absolute() or Path(member_name).is_absolute():
        raise _UnsafeZipPath(f"Zip member {member_name!r} is an absolute path.")
    if not member_name or member_name in {".", ".."}:
        raise _UnsafeZipPath(f"Zip member name {member_name!r} is empty or trivial.")

    candidate = (root / member_name).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise _UnsafeZipPath(
            f"Zip member {member_name!r} escapes the library root."
        ) from exc

    # Pending uploads live under LIBRARY_DIR/_pending/. Never overwrite there.
    rel = candidate.relative_to(root.resolve(strict=False))
    if rel.parts and rel.parts[0] == PENDING_DIR_NAME:
        raise _UnsafeZipPath(
            f"Zip member {member_name!r} points into the pending-uploads area."
        )
    return candidate
