"""FastAPI app entry point.

Phase 1: settings + SD card status.
Phase 2: SD card games + orphan art + box art streaming.
Phase 3: library upload + system detection + library CRUD.
Phase 4: libretro-thumbnails search + select + library box-art serving.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import init_db
from app.paths import PROJECT_ROOT, ensure_data_dirs
from app.routers import archive, boxart, library, sdcard, settings
from app.services.library_store import cleanup_stale_drafts

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist" / "minui-manager-ui" / "browser"


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


class _SpaStaticFiles(StaticFiles):
    """Serve static files, falling back to index.html for unknown non-API paths.

    Lets the Angular client-side router handle /games, /library, /settings on a
    full page load. /api/* paths bypass the fallback so a missing endpoint
    surfaces as a real 404 instead of HTML.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            # On Windows the separator is `\`, so normalize before the guard.
            normalized = path.replace("\\", "/")
            if ex.status_code == 404 and not normalized.startswith("api/"):
                return FileResponse(Path(self.directory) / "index.html")
            raise


if FRONTEND_DIST.is_dir():
    app.mount("/", _SpaStaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
