"""Box-art lookup + selection (Phase 4 search/select, Phase 5 image processing).

Endpoints:
    GET  /api/boxart/search?library_id=<id>     -> list of candidate thumbnails
    POST /api/boxart/select                      -> download + normalize a chosen candidate
    GET  /api/library/{id}/box-art               -> serve the saved PNG

Selection downloads the picked image, runs it through ``image_processor``
to resize it to the MinUI 200x300 PNG contract (strategy from user
settings), strips metadata, and writes the result to
``./data/library/<CODE>/.res/<game_folder>.png``.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import load_settings
from app.db import session_scope
from app.services import boxart_libretro
from app.services.image_processor import ImageProcessingError, process_image
from app.services.library_store import get_library_game
from app.services.system_registry import load_systems

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/boxart", tags=["boxart"])
library_extra = APIRouter(prefix="/api/library", tags=["library"])


class CandidateOut(BaseModel):
    name: str
    score: int
    source_url: str
    source: str = "libretro"


class SearchResponse(BaseModel):
    library_id: int
    query: str
    system_code: str
    repo: str | None
    candidates: list[CandidateOut]
    cache_hit: bool
    note: str | None = None  # human-readable explanation when there's nothing


@router.get("/search", response_model=SearchResponse)
def search(
    library_id: int = Query(..., description="LibraryGame id to find art for."),
    query_override: str | None = Query(
        default=None, alias="query", description="Override the query string."
    ),
) -> SearchResponse:
    """Return up to 5 libretro thumbnail candidates for the given library entry."""
    with session_scope() as session:
        game = get_library_game(session, library_id)
        if game is None:
            raise HTTPException(status_code=404, detail="Library entry not found.")
        registry = load_systems()
        system = registry.get(game.system_code)
        if system is None:
            raise HTTPException(
                status_code=500,
                detail=f"Library entry uses unknown system code '{game.system_code}'.",
            )
        if not system.libretro_repo:
            return SearchResponse(
                library_id=library_id,
                query=game.display_name,
                system_code=game.system_code,
                repo=None,
                candidates=[],
                cache_hit=False,
                note=f"No libretro-thumbnails repo configured for {game.system_code}.",
            )

        query = (query_override or game.display_name).strip()
        repo = system.libretro_repo

        # Cache check: if we have a fresh listing, this avoids a network call.
        cache_hit = boxart_libretro.load_cached(session, repo) is not None
        try:
            entries = boxart_libretro.get_or_fetch_listing(session, repo)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                return SearchResponse(
                    library_id=library_id,
                    query=query,
                    system_code=game.system_code,
                    repo=repo,
                    candidates=[],
                    cache_hit=False,
                    note=f"GitHub repo libretro-thumbnails/{repo} not found.",
                )
            raise HTTPException(
                status_code=502,
                detail=f"GitHub returned {status} fetching the thumbnail listing.",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Couldn't reach GitHub: {exc}",
            ) from exc

        candidates = boxart_libretro.match_thumbnails(query, entries)
        note = None
        if not candidates:
            note = (
                f"No thumbnails scored above {boxart_libretro.MATCH_THRESHOLD} "
                f"for '{query}'. Try refining the display name or pick "
                "another query."
            )
        return SearchResponse(
            library_id=library_id,
            query=query,
            system_code=game.system_code,
            repo=repo,
            candidates=[
                CandidateOut(
                    name=c.name, score=c.score, source_url=c.source_url, source=c.source
                )
                for c in candidates
            ],
            cache_hit=cache_hit,
            note=note,
        )


class SelectRequest(BaseModel):
    library_id: int = Field(..., description="LibraryGame id.")
    source_url: str = Field(..., description="The candidate's download URL.")
    source_name: str | None = Field(
        default=None, description="Original filename of the picked thumbnail (for logging)."
    )


@router.post("/select")
async def select(body: SelectRequest) -> dict[str, object]:
    """Download the chosen candidate, normalize it to 200x300 PNG, save it."""
    with session_scope() as session:
        game = get_library_game(session, body.library_id)
        if game is None:
            raise HTTPException(status_code=404, detail="Library entry not found.")
        dest = game.boxart_path

    # httpx is sync; punt to a thread so we don't block the event loop.
    try:
        content = await asyncio.to_thread(boxart_libretro.download_image, body.source_url)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to download image: {exc}"
        ) from exc

    if not content:
        raise HTTPException(status_code=502, detail="Downloaded image was empty.")

    settings = load_settings()
    target = (settings.boxart_target_width, settings.boxart_target_height)
    try:
        processed = await asyncio.to_thread(
            process_image, content, target, settings.boxart_resize_strategy
        )
    except ImageProcessingError as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not process image: {exc}"
        ) from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(processed)
    logger.info(
        "Saved boxart for library_id=%s (%s) from %s (strategy=%s, size=%sx%s)",
        body.library_id,
        body.source_name or "unknown",
        body.source_url,
        settings.boxart_resize_strategy,
        target[0],
        target[1],
    )

    # Return the fresh public dict so the frontend can refresh its UI.
    with session_scope() as session:
        game = get_library_game(session, body.library_id)
        assert game is not None
        return game.to_public_dict()


@library_extra.get("/{library_id}/box-art")
def serve_box_art(library_id: int) -> FileResponse:
    """Serve the saved PNG for a library entry, if any."""
    with session_scope() as session:
        game = get_library_game(session, library_id)
        if game is None:
            raise HTTPException(status_code=404, detail="Library entry not found.")
        art = game.boxart_path
    if not art.is_file():
        raise HTTPException(status_code=404, detail="No box art selected yet.")
    return FileResponse(art, media_type="image/png")
