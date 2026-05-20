"""SD card validity check.

Per plan Section 4, rule 6:
> SD card validity check: path exists AND contains `.system/` AND contains `Emus/`.

Returns a structured status the API can hand to the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Markers we look for to recognize a MinUI / Miyoo Mini Plus SD card configured
# for the Five Game Handheld layout. The reference card at D:\ has both.
REQUIRED_MARKERS = (".system", "Emus")

# Soft markers — nice-to-have, signal a known-good card but not required.
SOFT_MARKERS = ("Roms", "Saves", "Bios", "em_ui.sh")

Status = Literal["not_set", "not_found", "invalid", "ok"]


@dataclass(frozen=True)
class SDCardStatus:
    status: Status
    path: str | None
    missing_markers: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "path": self.path,
            "missing_markers": list(self.missing_markers),
            "detail": self.detail,
        }


def check_sd_card(sd_path: Path | None) -> SDCardStatus:
    """Inspect ``sd_path`` and return its status.

    Status semantics:
      - ``not_set``  — no path configured yet
      - ``not_found`` — path is configured but doesn't exist on disk
      - ``invalid`` — path exists but lacks the required MinUI markers
      - ``ok`` — path exists and contains every required marker
    """
    if sd_path is None:
        return SDCardStatus(status="not_set", path=None, detail="No SD card path set.")

    if not sd_path.exists():
        return SDCardStatus(
            status="not_found",
            path=str(sd_path),
            detail=f"Path does not exist: {sd_path}",
        )

    if not sd_path.is_dir():
        return SDCardStatus(
            status="invalid",
            path=str(sd_path),
            detail=f"Path is not a directory: {sd_path}",
        )

    missing = tuple(m for m in REQUIRED_MARKERS if not (sd_path / m).exists())
    if missing:
        return SDCardStatus(
            status="invalid",
            path=str(sd_path),
            missing_markers=missing,
            detail=(
                f"Path exists but is missing required marker(s): {', '.join(missing)}. "
                "Expected a MinUI SD card (typically also contains Roms/, Saves/, Bios/)."
            ),
        )

    return SDCardStatus(
        status="ok",
        path=str(sd_path),
        detail="SD card recognized as MinUI / Miyoo Mini Plus.",
    )
