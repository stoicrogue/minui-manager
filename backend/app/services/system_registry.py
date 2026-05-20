"""Loads systems.yaml and exposes lookups by code, by extension, and by
the folder-suffix `(CODE)` parsing convention.

The Phase 2 reader uses this primarily to validate that a folder's
parenthesized suffix is a real system code (so random folders like
`My Documents (Final)` don't get treated as games).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class System(BaseModel):
    code: str
    display_name: str
    extensions: list[str] = Field(default_factory=list)
    libretro_repo: str | None = None
    extension_preference: int = 0

    def normalized_extensions(self) -> list[str]:
        return [e.lower() if e.startswith(".") else f".{e.lower()}" for e in self.extensions]


class SystemRegistry:
    """Read-only registry. Build via :func:`load_systems` (cached)."""

    def __init__(self, systems: list[System]) -> None:
        self._systems: dict[str, System] = {s.code: s for s in systems}
        # Pre-build extension → list of codes (sorted by preference desc).
        self._by_ext: dict[str, list[System]] = {}
        for s in systems:
            for ext in s.normalized_extensions():
                self._by_ext.setdefault(ext, []).append(s)
        for ext in self._by_ext:
            self._by_ext[ext].sort(key=lambda s: -s.extension_preference)

    @property
    def all(self) -> list[System]:
        return list(self._systems.values())

    @property
    def codes(self) -> set[str]:
        return set(self._systems.keys())

    def get(self, code: str) -> System | None:
        return self._systems.get(code)

    def systems_for_extension(self, ext: str) -> list[System]:
        """Return systems claiming ``ext``, sorted by preference desc."""
        normalized = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        return list(self._by_ext.get(normalized, []))


_FOLDER_SUFFIX_RE = re.compile(r"^(.*)\s+\(([A-Z][A-Z0-9]*)\)\s*$")


def parse_game_folder_name(folder_name: str, registry: SystemRegistry) -> tuple[str, str] | None:
    """Parse a game-folder name into ``(display_name, system_code)``.

    Returns None if the name doesn't end in `` (CODE)`` or the code isn't
    in the registry. Examples::

        parse_game_folder_name("Tetris (FC)", reg)             -> ("Tetris", "FC")
        parse_game_folder_name("Kirby's Dream Land 2 (GB)", reg) -> ("Kirby's Dream Land 2", "GB")
        parse_game_folder_name("My Documents (Final)", reg)    -> None  # not a code
        parse_game_folder_name("Tetris", reg)                  -> None  # no suffix
    """
    m = _FOLDER_SUFFIX_RE.match(folder_name)
    if m is None:
        return None
    display, code = m.group(1).rstrip(), m.group(2)
    if code not in registry.codes:
        return None
    return display, code


def _systems_yaml_path() -> Path:
    # systems.yaml ships alongside the `app` package.
    return Path(__file__).resolve().parent.parent / "systems.yaml"


@lru_cache(maxsize=1)
def load_systems(path: Path | None = None) -> SystemRegistry:
    """Load and cache the system registry from ``systems.yaml``.

    Pass an explicit ``path`` for tests; otherwise the package's bundled
    file is used.
    """
    yaml_path = path or _systems_yaml_path()
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    raw = data.get("systems", [])
    systems = [System.model_validate(item) for item in raw]
    return SystemRegistry(systems)


def reset_cache() -> None:
    """Clear the cached registry (test helper)."""
    load_systems.cache_clear()
