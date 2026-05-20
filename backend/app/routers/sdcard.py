"""SD card router — for now just the validity/status endpoint.

Phase 2 will add /api/sdcard/games and friends.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import load_settings
from app.services.sdcard_validator import check_sd_card

router = APIRouter(prefix="/api/sdcard", tags=["sdcard"])


@router.get("/status")
def get_sd_card_status() -> dict[str, object]:
    """Return the current SD card status: not_set | not_found | invalid | ok."""
    settings = load_settings()
    return check_sd_card(settings.sd_card_path).to_dict()
