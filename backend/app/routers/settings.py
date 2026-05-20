"""Settings router — GET current settings, PATCH partial updates."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import Settings, SettingsUpdate, load_settings, save_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=Settings)
def get_settings() -> Settings:
    """Return current settings (defaults if no config.json exists yet)."""
    return load_settings()


@router.patch("", response_model=Settings)
def update_settings(patch: SettingsUpdate) -> Settings:
    """Merge ``patch`` into current settings and persist."""
    current = load_settings()
    updated = patch.apply_to(current)
    save_settings(updated)
    return updated
