"""Library router — upload (two-step), list, delete, backup export/import."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.db import session_scope
from app.services.library_backup import (
    LibraryImportError,
    export_library,
    import_library,
)
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


# ---------------------------------------------------------------------------
# Backup export / import (Phase 8)
# ---------------------------------------------------------------------------


def _cleanup_temp(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@router.get("/export")
async def export_library_endpoint() -> FileResponse:
    """Stream a zip of the library + cached box art for backup.

    Writes to a temp file (multi-GB libraries would OOM the laptop if
    held in BytesIO) and registers a BackgroundTask to delete it after
    the response is sent.
    """
    tmp = Path(tempfile.NamedTemporaryFile(prefix="minui-library-", suffix=".zip", delete=False).name)

    def _do() -> None:
        with session_scope() as session:
            export_library(session, tmp)

    try:
        await asyncio.to_thread(_do)
    except Exception:
        _cleanup_temp(tmp)
        raise

    filename = f"minui-library-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}.zip"
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_cleanup_temp, tmp),
    )


@router.post("/import")
async def import_library_endpoint(file: Annotated[UploadFile, File()]) -> JSONResponse:
    """Restore from a previously exported library zip.

    Per-entry problems (collisions, unknown system codes, zip-slip)
    are recorded as ``skipped`` rather than aborting the import. The
    response shape lets the frontend show a per-entry summary.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename on the upload.")

    # Stream the upload to a temp file rather than read() into memory.
    tmp = Path(tempfile.NamedTemporaryFile(prefix="minui-import-", suffix=".zip", delete=False).name)
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)

        def _do():
            with session_scope() as session:
                return import_library(session, tmp)

        try:
            result = await asyncio.to_thread(_do)
        except LibraryImportError as exc:
            return JSONResponse(
                status_code=400,
                content={"code": exc.code, "detail": exc.message},
            )
        return JSONResponse(status_code=200, content=result.to_dict())
    finally:
        _cleanup_temp(tmp)
