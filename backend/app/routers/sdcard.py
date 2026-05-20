"""SD card router — status, native folder picker, game listing.

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
from fastapi.responses import FileResponse

from app.config import load_settings
from app.services.folder_picker import open_folder_dialog
from app.services.sdcard_reader import (
    SDCardListing,
    listing,
    resolve_shared_art_path,
    scan_games,
    scan_orphan_art,
)
from app.services.sdcard_validator import check_sd_card
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
    """List games currently on the SD card, with slot count + summary."""
    sd_root = _require_ok_sd_path()
    settings = load_settings()
    registry = load_systems()
    return listing(sd_root, registry, slot_cap=settings.max_games_total)


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
