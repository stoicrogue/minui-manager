"""SD card router — status, native folder picker.

Phase 2 will add /api/sdcard/games and friends.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter

from app.config import load_settings
from app.services.folder_picker import open_folder_dialog
from app.services.sdcard_validator import check_sd_card

router = APIRouter(prefix="/api/sdcard", tags=["sdcard"])


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

    Runs the Tk dialog in a worker thread so the event loop stays responsive.
    """
    current = load_settings().sd_card_path
    initial_dir: Path | None = None
    if current is not None:
        # If a path was previously set, open the picker at its parent so
        # the user lands close to where they were last time.
        initial_dir = current.parent if current.exists() else None

    selected = await asyncio.to_thread(open_folder_dialog, initial_dir)
    return {"path": selected}
