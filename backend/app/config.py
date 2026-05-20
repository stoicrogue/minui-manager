"""User settings — persisted to ./data/config.json. Loaded on app startup, written on update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.paths import CONFIG_PATH, ensure_data_dirs


class Settings(BaseModel):
    """User-configurable settings. Matches Section 5 of the plan."""

    sd_card_path: Path | None = None
    boxart_target_width: int = 200
    boxart_target_height: int = 300
    boxart_resize_strategy: Literal["cover", "contain", "stretch"] = "cover"
    max_games_total: int | None = 10
    archive_on_remove: bool = True
    steamgriddb_api_key: str | None = None  # Phase 8

    # Phase-1 round-tripping note: Path is serialized as a string in JSON.
    model_config = {"json_encoders": {Path: str}}


def _empty_settings() -> Settings:
    return Settings()


def load_settings() -> Settings:
    """Read ./data/config.json. Returns defaults if absent or invalid."""
    ensure_data_dirs()
    if not CONFIG_PATH.exists():
        return _empty_settings()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt config — fall back to defaults rather than crash on boot.
        return _empty_settings()
    # pydantic handles type coercion (str → Path, etc.) on construct.
    return Settings.model_validate(raw)


def save_settings(settings: Settings) -> None:
    """Write ./data/config.json atomically (write to tmp, replace)."""
    ensure_data_dirs()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    payload = settings.model_dump(mode="json")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


class SettingsUpdate(BaseModel):
    """Partial-update payload for PATCH /api/settings.

    Fields are all optional; only provided keys are merged into current settings.
    """

    sd_card_path: Path | None = Field(default=None)
    boxart_target_width: int | None = None
    boxart_target_height: int | None = None
    boxart_resize_strategy: Literal["cover", "contain", "stretch"] | None = None
    max_games_total: int | None = None
    archive_on_remove: bool | None = None
    steamgriddb_api_key: str | None = None

    # Distinguish "unset key" from "set to null" via a separate sentinel set.
    # For Phase 1 we accept the simpler semantics: any provided key overwrites,
    # `null` clears. Good enough for the SD path + slot cap use cases.

    def apply_to(self, current: Settings) -> Settings:
        data = current.model_dump()
        for key, value in self.model_dump(exclude_unset=True).items():
            data[key] = value
        return Settings.model_validate(data)
