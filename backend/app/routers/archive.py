"""Archive router (Phase 7).

Endpoints:
    GET  /api/archive                            -> list archived games (newest first)
    POST /api/archive/{id}/restore-to-library    -> copy ROM + art back into the library
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db import session_scope
from app.services.archive_store import (
    ArchiveError,
    delete_archived,
    get_archived,
    list_archived,
    restore_to_library,
)

router = APIRouter(prefix="/api/archive", tags=["archive"])


@router.get("")
def get_archive(limit: int | None = Query(default=None, ge=1, le=500)) -> dict[str, object]:
    """List archived games, most-recent first."""
    with session_scope() as session:
        rows = list_archived(session, limit=limit)
        return {"archived": [r.to_public_dict() for r in rows]}


@router.get("/{archive_id}")
def get_archive_entry(archive_id: int) -> dict[str, object]:
    """Single archived entry by id."""
    with session_scope() as session:
        row = get_archived(session, archive_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No archived game with that id.")
        return row.to_public_dict()


@router.delete("/{archive_id}")
def delete_archive_entry(archive_id: int) -> dict[str, object]:
    """Permanently delete an archived game (DB row + on-disk bundle).

    Lets the user trim the archive list when a game has been cycled on
    and off the card several times and the older snapshots are no
    longer useful.
    """
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


@router.post("/{archive_id}/restore-to-library")
def post_restore(archive_id: int) -> dict[str, object]:
    """Copy the archived ROM (and art, if any) back into the library.

    Idempotent: re-running on the same archive returns the existing
    library entry and re-copies the files (so a corrupted library file
    gets healed). The archive itself is left intact.
    """
    with session_scope() as session:
        try:
            row = restore_to_library(session, archive_id)
        except ArchiveError as exc:
            if exc.code == "not_found":
                raise HTTPException(status_code=404, detail=exc.message) from exc
            if exc.code == "archive_missing":
                raise HTTPException(status_code=410, detail=exc.message) from exc
            raise HTTPException(status_code=400, detail=exc.message) from exc
        return {"library_game": row.to_public_dict()}
