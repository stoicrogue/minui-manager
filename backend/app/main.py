"""FastAPI app entry point. Phase 1: settings + SD card status."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.paths import ensure_data_dirs
from app.routers import sdcard, settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Make sure ./data/ exists so the first PATCH /api/settings succeeds.
    ensure_data_dirs()
    yield


app = FastAPI(
    title="MinUI Manager",
    description="Local tool for curating a Miyoo Mini Plus running MinUI (Five Game Handheld layout).",
    version="0.1.0",
    lifespan=lifespan,
)

# Local Angular dev server runs on :4200; allow it during development.
# Once `make run` builds the static bundle and serves it from FastAPI directly,
# this becomes a no-op.
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
