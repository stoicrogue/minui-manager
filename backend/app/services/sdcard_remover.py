"""SafeSDCardRemover — the only code path that takes files OFF the SD card.

Separate from :class:`SafeSDCardWriter` on purpose. The blast radius is
different (data loss instead of corrupting an in-place sync) and the
allow-list is different (Phase 7's remove flow needs ``Saves/<CODE>/``,
which the writer must not have). Source-side checks mirror the writer;
the new check is that the destination must land under the configured
archive root so a buggy caller can't dump SD-card files into arbitrary
folders on the laptop.

Operations are copy-then-delete: callers copy everything into the
archive, verify, then delete from the card. The remover doesn't enforce
that ordering — that's the archive-store's job — but it logs each copy
and delete separately so the audit trail is honest.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from app import paths as _paths

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_SUBTREES: tuple[str, ...] = ("Roms", "Saves")


class SDCardRemoveError(Exception):
    """Base error for SD-card remove failures."""


class UnsafePathError(SDCardRemoveError):
    """A source or destination path failed safety checks. Nothing was moved."""


class SafeSDCardRemover:
    """Read-and-move-out gateway. Validates every source against the SD
    root + an allow-list of subtrees, and every destination against the
    configured archive root.
    """

    def __init__(
        self,
        sd_root: Path,
        archive_root: Path,
        allowed_subtrees: Iterable[str] = DEFAULT_ALLOWED_SUBTREES,
    ) -> None:
        self._sd_root = Path(sd_root).resolve(strict=False)
        self._archive_root = Path(archive_root).resolve(strict=False)
        self._allowed_roots: list[Path] = [
            (self._sd_root / sub).resolve(strict=False) for sub in allowed_subtrees
        ]

    @property
    def sd_root(self) -> Path:
        return self._sd_root

    @property
    def archive_root(self) -> Path:
        return self._archive_root

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def resolve_source(self, rel_path: str | Path) -> Path:
        """Validate a source path on the card; return the absolute path."""
        rel_str = str(rel_path)
        if "\x00" in rel_str:
            raise UnsafePathError("Null byte in path.")
        if PurePosixPath(rel_str.replace("\\", "/")).is_absolute() or Path(rel_str).is_absolute():
            raise UnsafePathError(f"Absolute source paths are not accepted: {rel_str!r}")
        if not rel_str or rel_str in {".", ".."}:
            raise UnsafePathError(f"Empty or trivial source path: {rel_str!r}")

        candidate = (self._sd_root / rel_str).resolve(strict=False)
        try:
            candidate.relative_to(self._sd_root)
        except ValueError as exc:
            raise UnsafePathError(
                f"Source {rel_str!r} escapes the SD root ({self._sd_root})."
            ) from exc

        if not any(self._is_under(candidate, allowed) for allowed in self._allowed_roots):
            raise UnsafePathError(
                f"Source {rel_str!r} is not under any allowed subtree "
                f"({', '.join(r.name for r in self._allowed_roots)})."
            )
        return candidate

    def _validate_dest(self, dest: Path) -> Path:
        """The destination must resolve under the configured archive root."""
        resolved = Path(dest).resolve(strict=False)
        try:
            resolved.relative_to(self._archive_root)
        except ValueError as exc:
            raise UnsafePathError(
                f"Destination {dest} is not under the archive root "
                f"({self._archive_root})."
            ) from exc
        return resolved

    @staticmethod
    def _is_under(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        return True

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def copy_out(self, rel_src: str | Path, abs_dest: Path) -> Path:
        """Copy ``rel_src`` (file or directory) on the card to ``abs_dest``.

        Source must be valid (under SD root + allow-list). Destination
        must be under the archive root. Source is left in place; the
        archive_store is responsible for calling :meth:`delete` after the
        copy succeeds.
        """
        src = self.resolve_source(rel_src)
        if not src.exists():
            raise SDCardRemoveError(f"Source does not exist: {src}")
        dest = self._validate_dest(abs_dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            # copytree refuses if dest exists. Make the destination unique
            # is the caller's job; here we just defer to the OS.
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        self._log("COPY_OUT", src, dest)
        return dest

    def delete(self, rel_path: str | Path) -> None:
        """Delete a file or directory from the card. No-op if missing."""
        target = self.resolve_source(rel_path)
        if not target.exists():
            return
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self._log("DELETE", target, None)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, action: str, src: Path, dest: Path | None) -> None:
        # Resolve sync.log lazily for the same reason as the writer.
        _paths.ensure_data_dirs()
        rel_src = src.relative_to(self._sd_root).as_posix()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        parts = [ts, action, rel_src]
        if dest is not None:
            parts.append(f"-> {dest}")
        line = "  ".join(parts) + "\n"
        try:
            with _paths.SYNC_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            logger.exception("Could not append to sync log.")


def make_archive_timestamp() -> str:
    """Format the timestamp used in archive directory names.

    Uses ``YYYY-MM-DDTHH-MM-SS`` (hyphens for the time part) since
    colons are illegal on Windows / FAT32, where the archive may end up
    being viewed.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
