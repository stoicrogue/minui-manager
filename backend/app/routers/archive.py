"""Archive router.

Endpoints:
    GET    /api/archive                              -> list archived saves
    GET    /api/archive/{id}                         -> single archive entry
    DELETE /api/archive/{id}                         -> delete entry + on-disk bundle
    POST   /api/archive/{id}/restore-save-to-card    -> drop the archived save back on the card
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db import session_scope
from app.routers.sdcard import _require_ok_sd_path
from app.services.archive_store import (
    ArchiveError,
    delete_archived,
    get_archived,
    list_archived,
    restore_save_to_card,
)
from app.services.system_registry import load_systems

router = APIRouter(prefix="/api/archive", tags=["archive"])


@router.get("")
def get_archive(limit: int | None = Query(default=None, ge=1, le=500)) -> dict[str, object]:
    """List archived saves, most-recent first."""
    with session_scope() as session:
        rows = list_archived(session, limit=limit)
        return {"archived": [r.to_public_dict() for r in rows]}


@router.get("/{archive_id}")
def get_archive_entry(archive_id: int) -> dict[str, object]:
    """Single archive entry by id."""
    with session_scope() as session:
        row = get_archived(session, archive_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No archived game with that id.")
        return row.to_public_dict()


@router.delete("/{archive_id}")
def delete_archive_entry(archive_id: int) -> dict[str, object]:
    """Permanently delete an archive entry (DB row + on-disk save bundle)."""
    with session_scope() as session:
        try:
            row = delete_archived(session, archive_id)
            payload = row.to_public_dict()
        except ArchiveError as exc:
            status_map = {
                "not_found": 404,
                "unsafe_path": 400,
                "delete_failed": 500,
            }
            raise HTTPException(
                status_code=status_map.get(exc.code, 400), detail=exc.message
            ) from exc
        return {"deleted": payload}


@router.post("/{archive_id}/restore-save-to-card")
def post_restore_save(archive_id: int) -> dict[str, object]:
    """Copy the archived save file(s) back onto the SD card.

    The game must already be on the card (send it from the library
    first). Overwrites any existing save with the same name; the archive
    is left intact so re-restore is possible.
    """
    sd_root = _require_ok_sd_path()
    registry = load_systems()
    with session_scope() as session:
        try:
            result = restore_save_to_card(session, archive_id, sd_root, registry)
        except ArchiveError as exc:
            status_map = {
                "not_found": 404,
                "archive_missing": 410,
                "no_save": 400,
                "game_not_on_card": 409,
            }
            raise HTTPException(
                status_code=status_map.get(exc.code, 400), detail=exc.message
            ) from exc
        return {"restored": result}
