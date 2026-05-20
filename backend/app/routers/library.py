"""Library router — upload (two-step), list, delete."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.db import session_scope
from app.services.library_store import (
    LibraryError,
    cancel_draft,
    confirm_draft,
    delete_library_game,
    get_draft,
    list_library,
    save_pending_upload,
)
from app.services.system_detector import SystemDetection, detect
from app.services.system_registry import load_systems

router = APIRouter(prefix="/api/library", tags=["library"])


# ---------------------------------------------------------------------------
# Upload (draft) + confirm + cancel
# ---------------------------------------------------------------------------


class UploadResponse(BaseModel):
    draft_id: str
    original_filename: str
    size_bytes: int
    detection: SystemDetection


@router.post("/upload", response_model=UploadResponse)
async def upload_rom(file: Annotated[UploadFile, File()]) -> UploadResponse:
    """Save the uploaded file as a draft, then run system detection on the
    name. The frontend uses the result to pre-populate the confirm form.

    Nothing's committed to the DB or moved into the permanent library
    until ``POST /api/library/drafts/{id}/confirm``.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename on the upload.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # The file IO is small (just bytes → disk); run it in a thread so
    # we don't block the event loop on big ROMs.
    draft = await asyncio.to_thread(save_pending_upload, file.filename, content)

    detection = detect(draft.original_filename, load_systems())
    return UploadResponse(
        draft_id=draft.draft_id,
        original_filename=draft.original_filename,
        size_bytes=draft.file_path.stat().st_size,
        detection=detection,
    )


class ConfirmRequest(BaseModel):
    system_code: str = Field(..., min_length=1, max_length=16)
    display_name: str = Field(..., min_length=1, max_length=512)


@router.post("/drafts/{draft_id}/confirm")
def confirm_upload(draft_id: str, body: ConfirmRequest) -> dict[str, object]:
    """Commit a draft into the library."""
    # Validate the system_code is real before we touch storage.
    registry = load_systems()
    if body.system_code not in registry.codes:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown system code '{body.system_code}'.",
        )

    try:
        with session_scope() as session:
            row = confirm_draft(
                session, draft_id, body.system_code, body.display_name.strip()
            )
            return row.to_public_dict()
    except LibraryError as exc:
        status = 404 if exc.code == "draft_not_found" else 409
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.delete("/drafts/{draft_id}")
def cancel_upload(draft_id: str) -> dict[str, bool]:
    removed = cancel_draft(draft_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return {"removed": True}


@router.get("/drafts/{draft_id}")
def get_draft_info(draft_id: str) -> dict[str, object]:
    """Recover the draft + detection if the user navigates away and back."""
    draft = get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    detection = detect(draft.original_filename, load_systems())
    return {
        **draft.to_dict(),
        "detection": detection.model_dump(),
    }


# ---------------------------------------------------------------------------
# List / delete
# ---------------------------------------------------------------------------


@router.get("")
def list_library_endpoint(
    system_code: str | None = Query(default=None, description="Filter by system code."),
) -> dict[str, object]:
    with session_scope() as session:
        rows = list_library(session, system_code=system_code)
        return {"games": [r.to_public_dict() for r in rows], "total": len(rows)}


@router.delete("/{library_id}")
def delete_library_endpoint(library_id: int) -> dict[str, bool]:
    with session_scope() as session:
        ok = delete_library_game(session, library_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Library entry not found.")
    return {"deleted": True}
