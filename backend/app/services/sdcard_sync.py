"""Plan + execute a sync of library games to the SD card (Phase 6).

A *plan* is a per-game list of operations the writer would perform: mkdir
the game folder, copy the ROM, write the .m3u, ensure Roms/.res/ exists,
copy the box art (if any). The plan is computable without touching the
card, so the API serves it back to the frontend as a dry-run preview.

If the requested set of library entries would push the on-card slot count
past the user's cap, the planner returns a structured conflict instead of
a plan so the frontend can prompt for which existing game to remove first.

Re-syncing the same game (matching ``game_folder_name``) is an overwrite:
the existing Roms/<folder> tree is removed and rewritten. Same-name
games don't add to the slot count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from app.models import LibraryGame
from app.services.sdcard_reader import SDCardGame, scan_games
from app.services.sdcard_writer import SDCardWriteError, SafeSDCardWriter, UnsafePathError
from app.services.system_registry import SystemRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan + result data classes
# ---------------------------------------------------------------------------


OpAction = Literal["mkdir", "copy", "write_text", "remove_tree"]


@dataclass(frozen=True)
class SyncOp:
    """One filesystem operation in a plan."""

    action: OpAction
    dest_rel: str  # path relative to the SD root (POSIX-style)
    src: str | None = None  # source path, set for copies
    size_bytes: int | None = None  # source size when known
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "dest_rel": self.dest_rel,
            "src": self.src,
            "size_bytes": self.size_bytes,
            "note": self.note,
        }


@dataclass
class SyncPlanGame:
    """All ops for one library game in a sync plan."""

    library_id: int
    game_folder_name: str
    system_code: str
    display_name: str
    rom_filename: str
    is_replacement: bool
    has_boxart: bool
    boxart_missing_reason: str | None
    ops: list[SyncOp]

    def to_dict(self) -> dict[str, object]:
        return {
            "library_id": self.library_id,
            "game_folder_name": self.game_folder_name,
            "system_code": self.system_code,
            "display_name": self.display_name,
            "rom_filename": self.rom_filename,
            "is_replacement": self.is_replacement,
            "has_boxart": self.has_boxart,
            "boxart_missing_reason": self.boxart_missing_reason,
            "ops": [o.to_dict() for o in self.ops],
        }


@dataclass
class SyncPlan:
    games: list[SyncPlanGame]
    new_slot_count: int  # total distinct folders that will be on the card
    current_slot_count: int
    slot_cap: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "games": [g.to_dict() for g in self.games],
            "new_slot_count": self.new_slot_count,
            "current_slot_count": self.current_slot_count,
            "slot_cap": self.slot_cap,
            "total_ops": sum(len(g.ops) for g in self.games),
        }


@dataclass
class SlotCapConflict:
    """Returned instead of a plan when the requested set wouldn't fit."""

    cap: int
    current_slot_count: int
    projected_slot_count: int  # what it would become if we ran the plan
    current_games: list[SDCardGame]
    new_folder_names: list[str]  # truly new (not overwrites)
    replacing_folder_names: list[str]  # overwrites — these don't cost a slot

    def to_dict(self) -> dict[str, object]:
        return {
            "code": "slot_cap_exceeded",
            "cap": self.cap,
            "current_slot_count": self.current_slot_count,
            "projected_slot_count": self.projected_slot_count,
            "current_games": [g.model_dump() for g in self.current_games],
            "new_folder_names": self.new_folder_names,
            "replacing_folder_names": self.replacing_folder_names,
        }


@dataclass
class SyncGameResult:
    """Per-game outcome from executing a plan."""

    library_id: int
    game_folder_name: str
    status: Literal["ok", "error"]
    files_written: int = 0
    bytes_written: int = 0
    skipped_boxart: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "library_id": self.library_id,
            "game_folder_name": self.game_folder_name,
            "status": self.status,
            "files_written": self.files_written,
            "bytes_written": self.bytes_written,
            "skipped_boxart": self.skipped_boxart,
            "error": self.error,
        }


@dataclass
class SyncResult:
    started_at: datetime
    completed_at: datetime
    games: list[SyncGameResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for g in self.games if g.status == "ok")

    @property
    def error_count(self) -> int:
        return sum(1 for g in self.games if g.status == "error")

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "games": [g.to_dict() for g in self.games],
            "ok_count": self.ok_count,
            "error_count": self.error_count,
        }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _ops_for_game(game: LibraryGame, is_replacement: bool) -> tuple[list[SyncOp], bool, str | None]:
    """Compute the op list for one library game.

    Returns ``(ops, has_boxart, missing_reason)``.
    """
    folder_rel = f"Roms/{game.game_folder_name}"
    ops: list[SyncOp] = []

    if is_replacement:
        ops.append(
            SyncOp(
                action="remove_tree",
                dest_rel=folder_rel,
                note="overwrite: removing existing game folder",
            )
        )

    ops.append(SyncOp(action="mkdir", dest_rel=folder_rel))

    rom_size = game.size_bytes
    ops.append(
        SyncOp(
            action="copy",
            dest_rel=f"{folder_rel}/{game.rom_filename}",
            src=str(game.library_path),
            size_bytes=rom_size,
        )
    )

    ops.append(
        SyncOp(
            action="write_text",
            dest_rel=f"{folder_rel}/{game.game_folder_name}.m3u",
            note=game.rom_filename,  # actual m3u content
            size_bytes=len(game.rom_filename.encode("utf-8")),
        )
    )

    art = game.boxart_path
    if art.is_file():
        ops.append(SyncOp(action="mkdir", dest_rel="Roms/.res"))
        ops.append(
            SyncOp(
                action="copy",
                dest_rel=f"Roms/.res/{game.game_folder_name}.png",
                src=str(art),
                size_bytes=art.stat().st_size,
            )
        )
        return ops, True, None

    return ops, False, (
        f"No box art on disk at {art} — game will sync without art "
        "and show a placeholder on the device."
    )


def plan_sync(
    session: Session,
    sd_root: Path,
    registry: SystemRegistry,
    library_ids: list[int],
    slot_cap: int | None,
) -> SyncPlan | SlotCapConflict:
    """Build a sync plan for the given library ids.

    Returns a :class:`SlotCapConflict` instead of a plan when the requested
    set would exceed the user's slot cap. The slot count counts *distinct
    game folder names*, so re-syncing an existing game doesn't add a slot.
    """
    # Load every requested library entry — preserve request order so the
    # frontend's preview matches what the user selected.
    games_by_id: dict[int, LibraryGame] = {}
    for lid in library_ids:
        row = session.get(LibraryGame, lid)
        if row is None:
            raise ValueError(f"Library entry {lid} not found.")
        games_by_id[lid] = row
    library_games = [games_by_id[lid] for lid in library_ids]

    # Current state of the card.
    current = scan_games(sd_root, registry)
    current_folder_names = {g.game_folder_name for g in current}
    current_slot_count = len(current_folder_names)

    requested_folder_names = {g.game_folder_name for g in library_games}
    replacements = requested_folder_names & current_folder_names
    new_folders = requested_folder_names - current_folder_names

    projected_total = current_slot_count + len(new_folders)

    if slot_cap is not None and projected_total > slot_cap:
        return SlotCapConflict(
            cap=slot_cap,
            current_slot_count=current_slot_count,
            projected_slot_count=projected_total,
            current_games=current,
            new_folder_names=sorted(new_folders),
            replacing_folder_names=sorted(replacements),
        )

    plan_games: list[SyncPlanGame] = []
    for lg in library_games:
        is_replacement = lg.game_folder_name in current_folder_names
        ops, has_art, missing_reason = _ops_for_game(lg, is_replacement)
        plan_games.append(
            SyncPlanGame(
                library_id=lg.id,
                game_folder_name=lg.game_folder_name,
                system_code=lg.system_code,
                display_name=lg.display_name,
                rom_filename=lg.rom_filename,
                is_replacement=is_replacement,
                has_boxart=has_art,
                boxart_missing_reason=missing_reason,
                ops=ops,
            )
        )

    return SyncPlan(
        games=plan_games,
        new_slot_count=projected_total,
        current_slot_count=current_slot_count,
        slot_cap=slot_cap,
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_plan(plan: SyncPlan, writer: SafeSDCardWriter) -> SyncResult:
    """Run each game's ops via the writer. Per-game failures isolated."""
    started = datetime.now(timezone.utc)
    results: list[SyncGameResult] = []

    for game in plan.games:
        files = 0
        size_total = 0
        try:
            for op in game.ops:
                if op.action == "remove_tree":
                    writer.remove_tree(op.dest_rel)
                elif op.action == "mkdir":
                    writer.mkdir(op.dest_rel)
                elif op.action == "copy":
                    assert op.src is not None
                    dest = writer.copy_file(Path(op.src), op.dest_rel)
                    files += 1
                    size_total += dest.stat().st_size
                elif op.action == "write_text":
                    # note holds the m3u content for write_text ops.
                    content = op.note or ""
                    dest = writer.write_text(op.dest_rel, content)
                    files += 1
                    size_total += dest.stat().st_size
                else:
                    raise SDCardWriteError(f"Unknown op action: {op.action}")
            results.append(
                SyncGameResult(
                    library_id=game.library_id,
                    game_folder_name=game.game_folder_name,
                    status="ok",
                    files_written=files,
                    bytes_written=size_total,
                    skipped_boxart=not game.has_boxart,
                )
            )
        except (SDCardWriteError, UnsafePathError, OSError) as exc:
            logger.exception(
                "Sync failed for library_id=%s (%s)",
                game.library_id,
                game.game_folder_name,
            )
            results.append(
                SyncGameResult(
                    library_id=game.library_id,
                    game_folder_name=game.game_folder_name,
                    status="error",
                    files_written=files,
                    bytes_written=size_total,
                    skipped_boxart=not game.has_boxart,
                    error=str(exc),
                )
            )

    completed = datetime.now(timezone.utc)
    return SyncResult(started_at=started, completed_at=completed, games=results)
