"""SafeSDCardWriter — the only code path that mutates the SD card.

Every write resolves through :meth:`SafeSDCardWriter.resolve_under_whitelist`,
which guarantees three invariants:

1. The destination resolves under the configured SD root (no symlink or
   ``../`` escapes).
2. The destination resolves under one of the writer's allowed subtrees
   (defaults to ``Roms/`` — Phase 6's whitelist; Phase 7 may add
   ``Saves/``).
3. The relative path is free of NUL bytes and other obviously-hostile
   inputs.

Every successful write appends a human-readable line to
``./data/sync.log`` so the user can audit exactly what touched the card.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from app import paths as _paths

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_SUBTREES: tuple[str, ...] = ("Roms",)


class SDCardWriteError(Exception):
    """Base error for SD-card write failures."""


class UnsafePathError(SDCardWriteError):
    """The given relative path failed the safety checks. Nothing was written."""


class SafeSDCardWriter:
    """Append-only writer rooted under a validated SD card path.

    Construct with the validated SD root (caller ensures it passes
    :func:`app.services.sdcard_validator.check_sd_card`). Each mutation
    method takes a *relative* destination (POSIX or native separators
    both fine) and refuses anything that doesn't fall under one of the
    allowed subtrees.
    """

    def __init__(
        self,
        sd_root: Path,
        allowed_subtrees: Iterable[str] = DEFAULT_ALLOWED_SUBTREES,
    ) -> None:
        # Resolve once; every later check compares against this absolute path.
        self._sd_root = Path(sd_root).resolve(strict=False)
        # Allowed subtree roots, resolved.
        self._allowed_roots: list[Path] = []
        for sub in allowed_subtrees:
            # Defensive: subtree names themselves must not escape.
            sub_path = self._sd_root / sub
            self._allowed_roots.append(sub_path.resolve(strict=False))

    @property
    def sd_root(self) -> Path:
        return self._sd_root

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def resolve_under_whitelist(self, rel_path: str | Path) -> Path:
        """Return the absolute path for ``rel_path`` under the SD root.

        Raises :class:`UnsafePathError` if the path tries to escape the
        SD root or lands outside the allowed subtrees.
        """
        if isinstance(rel_path, Path):
            rel_str = str(rel_path)
        else:
            rel_str = rel_path
        if "\x00" in rel_str:
            raise UnsafePathError("Null byte in path.")
        # Reject absolute paths up front — even if they happened to point
        # inside sd_root, accepting them blurs the API contract (the writer
        # takes paths *relative to* the SD root, not arbitrary destinations).
        if PurePosixPath(rel_str.replace("\\", "/")).is_absolute() or Path(rel_str).is_absolute():
            raise UnsafePathError(f"Absolute paths are not accepted: {rel_str!r}")
        if not rel_str or rel_str in {".", ".."}:
            raise UnsafePathError(f"Empty or trivial path: {rel_str!r}")

        candidate = (self._sd_root / rel_str).resolve(strict=False)
        try:
            candidate.relative_to(self._sd_root)
        except ValueError as exc:
            raise UnsafePathError(
                f"Path {rel_str!r} escapes the SD root ({self._sd_root})."
            ) from exc

        # Must fall under at least one of the allowed subtree roots.
        if not any(
            self._is_under(candidate, allowed) for allowed in self._allowed_roots
        ):
            raise UnsafePathError(
                f"Path {rel_str!r} is not under any allowed subtree "
                f"({', '.join(str(r.name) for r in self._allowed_roots)})."
            )
        return candidate

    @staticmethod
    def _is_under(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        return True

    # ------------------------------------------------------------------
    # Mutations — every one routes through resolve_under_whitelist + log
    # ------------------------------------------------------------------

    def mkdir(self, rel_path: str | Path) -> Path:
        """Create ``rel_path`` (and parents) under the SD root. Idempotent."""
        dest = self.resolve_under_whitelist(rel_path)
        existed = dest.exists()
        dest.mkdir(parents=True, exist_ok=True)
        if not existed:
            self._log("MKDIR", dest, size=None)
        return dest

    def write_text(self, rel_path: str | Path, content: str) -> Path:
        """Write a text file to ``rel_path`` (UTF-8, no BOM)."""
        dest = self.resolve_under_whitelist(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")
        self._log("WRITE", dest, size=dest.stat().st_size)
        return dest

    def copy_file(self, src: Path, rel_dest: str | Path) -> Path:
        """Copy ``src`` to ``rel_dest`` under the SD root.

        Raises :class:`SDCardWriteError` if the source is missing or the
        size check after copy doesn't match.
        """
        src = Path(src)
        if not src.is_file():
            raise SDCardWriteError(f"Source file not found: {src}")
        dest = self.resolve_under_whitelist(rel_dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        # Cheap integrity check (per plan section 6 step 7).
        src_size = src.stat().st_size
        dest_size = dest.stat().st_size
        if src_size != dest_size:
            raise SDCardWriteError(
                f"Size mismatch after copy: src={src_size} dest={dest_size} ({dest})"
            )
        self._log("COPY", dest, size=dest_size, src=src)
        return dest

    def remove_tree(self, rel_path: str | Path) -> None:
        """Remove a directory tree at ``rel_path``. No-op if missing."""
        dest = self.resolve_under_whitelist(rel_path)
        if not dest.exists():
            return
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
        self._log("REMOVE", dest, size=None)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(
        self,
        action: str,
        dest: Path,
        size: int | None,
        src: Path | None = None,
    ) -> None:
        # Resolve the log path lazily — the data dir can be redirected by
        # tests after this module is first imported, so capturing the path
        # at import time would point at the wrong location.
        _paths.ensure_data_dirs()
        rel = dest.relative_to(self._sd_root).as_posix()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        parts = [ts, action, rel]
        if size is not None:
            parts.append(f"({size} bytes)")
        if src is not None:
            parts.append(f"<- {src}")
        line = "  ".join(parts) + "\n"
        try:
            with _paths.SYNC_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            # Logging is best-effort; never let a log failure abort a write.
            logger.exception("Could not append to sync log.")
