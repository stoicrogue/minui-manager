"""Read the live state of an SD card.

Scans ``<sd_root>/Roms/*/`` and emits a ``SDCardGame`` per recognized
folder. Also lists "orphan art" — PNGs in the shared ``Roms/.res/``
folder that don't correspond to any current game folder.

The SD card is the source of truth for what's currently on the device;
nothing here is persisted to the DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.services.system_registry import SystemRegistry, parse_game_folder_name

# Conventional MinUI Five-Game layout paths (relative to SD root).
ROMS_DIR = "Roms"
SHARED_RES_DIR = ".res"
SAVES_DIR = "Saves"


class SDCardGame(BaseModel):
    """Live state for one game on the card. Not persisted."""

    system_code: str
    game_folder_name: str
    display_name: str
    folder_path: str
    rom_filename: str | None
    rom_path: str | None
    m3u_path: str | None
    has_rom_file: bool
    has_boxart: bool
    boxart_path: str | None
    has_save: bool
    save_path: str | None
    is_malformed: bool
    malformed_reason: str | None
    matches_library_id: int | None = None


class OrphanArt(BaseModel):
    """A PNG in Roms/.res/ that doesn't match any current game folder."""

    filename: str  # e.g. "Lunar - Silver Star Story (PS).png"
    game_folder_name: str  # PNG basename without .png — looks like a folder name
    system_code: str | None  # None if the (CODE) suffix isn't recognized
    path: str


class SDCardListing(BaseModel):
    """Result of scanning the card. Used by ``GET /api/sdcard/games``."""

    games: list[SDCardGame]
    slot_count: int
    slot_cap: int | None
    summary: dict[str, int]


@dataclass(frozen=True)
class _GameFolderPaths:
    folder: Path
    m3u: Path
    shared_art: Path
    save_via_m3u: Path
    save_via_rom: Path | None


def _paths_for(sd_root: Path, game_folder: Path, system_code: str, rom_filename: str | None) -> _GameFolderPaths:
    folder_name = game_folder.name
    m3u = game_folder / f"{folder_name}.m3u"
    shared_art = sd_root / ROMS_DIR / SHARED_RES_DIR / f"{folder_name}.png"
    save_via_m3u = sd_root / SAVES_DIR / system_code / f"{folder_name}.m3u.sav"
    save_via_rom = (
        sd_root / SAVES_DIR / system_code / f"{rom_filename}.sav"
        if rom_filename
        else None
    )
    return _GameFolderPaths(
        folder=game_folder,
        m3u=m3u,
        shared_art=shared_art,
        save_via_m3u=save_via_m3u,
        save_via_rom=save_via_rom,
    )


def _read_m3u(m3u_path: Path) -> str | None:
    """Return the first non-empty, non-comment line from an .m3u file.

    The Five-Game m3u format is a single line: the ROM's filename relative
    to the game folder. Be tolerant of CRLF, BOM, and stray whitespace.
    """
    try:
        text = m3u_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return None


def scan_games(sd_root: Path, registry: SystemRegistry) -> list[SDCardGame]:
    """Return the games currently on the card, sorted by display name."""
    roms = sd_root / ROMS_DIR
    if not roms.is_dir():
        return []

    games: list[SDCardGame] = []
    for folder in sorted(roms.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir():
            continue
        if folder.name == SHARED_RES_DIR:
            continue
        parsed = parse_game_folder_name(folder.name, registry)
        if parsed is None:
            # Folder doesn't end in a known (CODE) suffix — skip silently.
            # (We could surface these as "unrecognized" later; not needed for
            # the Phase 2 acceptance criterion.)
            continue
        display_name, system_code = parsed

        rom_filename = _read_m3u(folder / f"{folder.name}.m3u")
        paths = _paths_for(sd_root, folder, system_code, rom_filename)

        # Resolve ROM presence & malformed reason.
        malformed_reasons: list[str] = []
        if not paths.m3u.exists():
            malformed_reasons.append(f"missing {folder.name}.m3u")
            has_rom = False
            rom_path: Path | None = None
        else:
            if rom_filename is None:
                malformed_reasons.append("m3u is empty")
                has_rom = False
                rom_path = None
            else:
                rom_path = folder / rom_filename
                has_rom = rom_path.is_file()
                if not has_rom:
                    malformed_reasons.append(f"rom file '{rom_filename}' not found in folder")

        has_boxart = paths.shared_art.is_file()
        save_path: Path | None = None
        if paths.save_via_m3u.exists():
            save_path = paths.save_via_m3u
        elif paths.save_via_rom is not None and paths.save_via_rom.exists():
            save_path = paths.save_via_rom

        games.append(
            SDCardGame(
                system_code=system_code,
                game_folder_name=folder.name,
                display_name=display_name,
                folder_path=str(folder),
                rom_filename=rom_filename,
                rom_path=str(rom_path) if rom_path else None,
                m3u_path=str(paths.m3u) if paths.m3u.exists() else None,
                has_rom_file=has_rom,
                has_boxart=has_boxart,
                boxart_path=str(paths.shared_art) if has_boxart else None,
                has_save=save_path is not None,
                save_path=str(save_path) if save_path else None,
                is_malformed=len(malformed_reasons) > 0,
                malformed_reason="; ".join(malformed_reasons) if malformed_reasons else None,
            )
        )

    return games


def scan_orphan_art(sd_root: Path, registry: SystemRegistry, games: list[SDCardGame]) -> list[OrphanArt]:
    """List PNGs in ``Roms/.res/`` that don't match a current game folder."""
    res = sd_root / ROMS_DIR / SHARED_RES_DIR
    if not res.is_dir():
        return []

    on_card = {g.game_folder_name for g in games}
    orphans: list[OrphanArt] = []
    for png in sorted(res.iterdir(), key=lambda p: p.name.lower()):
        if not png.is_file() or png.suffix.lower() != ".png":
            continue
        stem = png.stem  # filename without .png
        if stem in on_card:
            continue
        parsed = parse_game_folder_name(stem, registry)
        sys_code = parsed[1] if parsed is not None else None
        orphans.append(
            OrphanArt(
                filename=png.name,
                game_folder_name=stem,
                system_code=sys_code,
                path=str(png),
            )
        )
    return orphans


def listing(sd_root: Path, registry: SystemRegistry, slot_cap: int | None) -> SDCardListing:
    """Top-level scan: games + counters used by the dashboard endpoint."""
    games = scan_games(sd_root, registry)
    summary = {
        "total": len(games),
        "with_boxart": sum(1 for g in games if g.has_boxart),
        "with_save": sum(1 for g in games if g.has_save),
        "malformed": sum(1 for g in games if g.is_malformed),
    }
    return SDCardListing(
        games=games,
        slot_count=len(games),
        slot_cap=slot_cap,
        summary=summary,
    )


def resolve_shared_art_path(sd_root: Path, name: str) -> Path | None:
    """Safely resolve ``Roms/.res/<name>.png`` under ``sd_root``.

    Returns the path if it exists and is contained under the shared art
    folder; returns None otherwise. Defends against path traversal
    (``../`` etc.) and absolute-path arguments.
    """
    # No separators allowed in the user-supplied name.
    if "/" in name or "\\" in name or name in {".", ".."}:
        return None
    if name.endswith(".png"):
        name = name[:-4]
    candidate = sd_root / ROMS_DIR / SHARED_RES_DIR / f"{name}.png"
    try:
        resolved = candidate.resolve(strict=False)
        res_root = (sd_root / ROMS_DIR / SHARED_RES_DIR).resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(res_root)
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return resolved
