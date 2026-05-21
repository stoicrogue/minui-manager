"""FastAPI app entry point.

Phase 1: settings + SD card status.
Phase 2: SD card games + orphan art + box art streaming.
Phase 3: library upload + system detection + library CRUD.
Phase 4: libretro-thumbnails search + select + library box-art serving.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.paths import ensure_data_dirs
from app.routers import archive, boxart, library, sdcard, settings
from app.services.library_store import cleanup_stale_drafts


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_data_dirs()
    init_db()
    cleanup_stale_drafts()
    yield


app = FastAPI(
    title="MinUI Manager",
    description="Local tool for curating a Miyoo Mini Plus running MinUI (Five Game Handheld layout).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(settings.router)
app.include_router(sdcard.router)
app.include_router(library.router)
app.include_router(boxart.router)
app.include_router(boxart.library_extra)
app.include_router(archive.router)
