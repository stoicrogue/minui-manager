"""SD card router — status, native folder picker, game listing, sync.

Phase 6 endpoints:
    POST /api/sdcard/sync         -> plan + (optionally) execute a sync

Phase 2 endpoints:
    GET  /api/sdcard/games        -> list of games + slot count
    GET  /api/sdcard/orphan-art   -> PNGs in shared .res/ with no game folder
    GET  /api/sdcard/box-art      -> stream a PNG from the shared .res/ folder

Phase 1 endpoints:
    GET  /api/sdcard/status       -> not_set | not_found | invalid | ok
    POST /api/sdcard/pick-folder  -> native folder dialog
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import load_settings
from app.db import session_scope
from app.services.archive_store import ArchiveError, archive_game
from app.services.folder_picker import open_folder_dialog
from app.services.library_store import (
    LibraryError,
    import_from_sd_card,
    populate_library_matches,
)
from app.services.sdcard_reader import (
    SDCardListing,
    listing,
    resolve_shared_art_path,
    scan_games,
    scan_orphan_art,
)
from app.services.sdcard_sync import (
    SlotCapConflict,
    SyncPlan,
    execute_plan,
    plan_sync,
)
from app.services.sdcard_validator import check_sd_card
from app.services.sdcard_writer import SafeSDCardWriter
from app.services.system_registry import load_systems

router = APIRouter(prefix="/api/sdcard", tags=["sdcard"])


def _require_ok_sd_path() -> Path:
    """Return the SD root if status is ok; else raise 400 with the reason.

    The games/orphan-art/box-art endpoints all need a known-good card path.
    Centralizing the precondition keeps error responses consistent.
    """
    settings = load_settings()
    status = check_sd_card(settings.sd_card_path)
    if status.status != "ok" or settings.sd_card_path is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "sd_card_not_ready",
                "status": status.status,
                "message": status.detail,
            },
        )
    return settings.sd_card_path


@router.get("/status")
def get_sd_card_status() -> dict[str, object]:
    """Return the current SD card status: not_set | not_found | invalid | ok."""
    settings = load_settings()
    return check_sd_card(settings.sd_card_path).to_dict()


@router.post("/pick-folder")
async def pick_folder() -> dict[str, str | None]:
    """Open a native folder picker on the user's machine.

    Returns ``{"path": "<absolute path>"}`` on selection, or ``{"path": null}``
    if the user cancelled or the dialog couldn't be shown.
    """
    current = load_settings().sd_card_path
    initial_dir: Path | None = None
    if current is not None:
        initial_dir = current.parent if current.exists() else None

    selected = await asyncio.to_thread(open_folder_dialog, initial_dir)
    return {"path": selected}


@router.get("/games", response_model=SDCardListing)
def get_games() -> SDCardListing:
    """List games currently on the SD card, with slot count + summary.

    Each game's ``matches_library_id`` is filled in when there's a library
    entry with the same (system_code, rom_filename), so the UI can disable
    the "Import to library" action for games that are already imported.
    """
    sd_root = _require_ok_sd_path()
    settings = load_settings()
    registry = load_systems()
    result = listing(sd_root, registry, slot_cap=settings.max_games_total)
    with session_scope() as session:
        populate_library_matches(session, result.games)
    return result


@router.get("/orphan-art")
def get_orphan_art() -> dict[str, object]:
    """List PNGs in Roms/.res/ with no matching game folder.

    Informational (Phase 8 polish will let the user clean these up).
    """
    sd_root = _require_ok_sd_path()
    registry = load_systems()
    games = scan_games(sd_root, registry)
    orphans = scan_orphan_art(sd_root, registry, games)
    return {"art": [o.model_dump() for o in orphans]}


class SyncRequest(BaseModel):
    library_ids: list[int] = Field(..., description="Library entries to send to the card.")


@router.post("/sync")
async def post_sync(
    body: SyncRequest,
    dry_run: bool = Query(default=False, description="Plan only; no FS changes."),
) -> JSONResponse:
    """Plan a sync, optionally execute it. Returns the plan either way.

    Response shape:
        - 200 with ``{"dry_run": true, "plan": {...}}`` for a plan-only call.
        - 200 with ``{"dry_run": false, "plan": {...}, "result": {...}}``
          after a successful (possibly partially-failed) execution.
        - 409 with the SlotCapConflict body when the request would exceed
          the user's slot cap.
        - 400 when the SD card isn't ready or a library id is unknown.
    """
    if not body.library_ids:
        raise HTTPException(status_code=400, detail="library_ids must be non-empty.")

    sd_root = _require_ok_sd_path()
    settings = load_settings()
    registry = load_systems()

    with session_scope() as session:
        try:
            outcome = plan_sync(
                session=session,
                sd_root=sd_root,
                registry=registry,
                library_ids=body.library_ids,
                slot_cap=settings.max_games_total,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if isinstance(outcome, SlotCapConflict):
        return JSONResponse(status_code=409, content=outcome.to_dict())

    assert isinstance(outcome, SyncPlan)
    if dry_run:
        return JSONResponse(
            status_code=200, content={"dry_run": True, "plan": outcome.to_dict()}
        )

    writer = SafeSDCardWriter(sd_root)
    # The writer does sync FS IO. Run it off the event loop.
    result = await asyncio.to_thread(execute_plan, outcome, writer)
    status = 200 if result.error_count == 0 else 207
    return JSONResponse(
        status_code=status,
        content={
            "dry_run": False,
            "plan": outcome.to_dict(),
            "result": result.to_dict(),
        },
    )


@router.delete("/games/{game_folder_name}")
async def remove_game(game_folder_name: str) -> JSONResponse:
    """Archive the named game off the SD card.

    Copies the game folder, box art, and save file(s) into a fresh
    timestamped directory under ``./data/archive/`` and deletes the
    originals from the card. Returns the new ``ArchivedGame`` row.
    """
    sd_root = _require_ok_sd_path()
    registry = load_systems()

    def _do() -> dict[str, object]:
        with session_scope() as session:
            row = archive_game(session, sd_root, registry, game_folder_name)
            return row.to_public_dict()

    try:
        archived = await asyncio.to_thread(_do)
    except ArchiveError as exc:
        status_map = {
            "not_on_card": 404,
            "archive_collision": 409,
            "copy_failed": 500,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"code": exc.code, "detail": exc.message},
        )
    return JSONResponse(status_code=200, content={"archived": archived})


@router.post("/games/{game_folder_name}/import-to-library")
async def import_game_to_library(game_folder_name: str) -> JSONResponse:
    """Copy the named card game into the laptop library.

    Pulls the ROM + box art (if present) into ``./data/library/<CODE>/`` and
    creates a ``LibraryGame`` row. Leaves the card untouched. Saves are not
    pulled. Returns the new library row.
    """
    sd_root = _require_ok_sd_path()
    registry = load_systems()

    def _do() -> dict[str, object]:
        with session_scope() as session:
            row = import_from_sd_card(session, sd_root, registry, game_folder_name)
            return row.to_public_dict()

    try:
        imported = await asyncio.to_thread(_do)
    except LibraryError as exc:
        status_map = {
            "not_on_card": 404,
            "malformed": 422,
            "duplicate_rom": 409,
            "duplicate_display_name": 409,
            "integrity_error": 409,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"code": exc.code, "detail": exc.message},
        )
    return JSONResponse(status_code=200, content={"imported": imported})


@router.get("/box-art")
def get_box_art(name: str = Query(..., description="Game folder name (no .png)")) -> FileResponse:
    """Stream the PNG for ``Roms/.res/<name>.png``.

    ``name`` should be the game folder name (or PNG stem) — the same
    string used as ``game_folder_name`` in the games listing. Path
    traversal attempts are rejected.
    """
    sd_root = _require_ok_sd_path()
    art = resolve_shared_art_path(sd_root, name)
    if art is None:
        raise HTTPException(status_code=404, detail=f"No box art for '{name}'")
    return FileResponse(art, media_type="image/png")
