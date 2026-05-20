"""Detect a ROM's system from its filename.

Used by Phase 3's upload flow to pre-populate the system dropdown. The
detector always returns a result (even if it's "unknown") and the frontend
always shows the dropdown so the user can override.

Detection priority (highest confidence first):

1. **Parenthesized code** in the filename (case-sensitive, e.g.
   ``Pokemon Unbound (GBA).gba``) — ``high``.
2. **Unambiguous extension** (e.g. ``.nes`` is only FC) — ``medium``.
3. **Ambiguous extension** (e.g. ``.gba`` is GBA *or* MGBA) — ``low``,
   with the highest-preference code preselected and the alternatives
   listed as candidates.
4. **No match** — ``unknown``, dropdown defaults blank, full system list
   as candidates.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel

from app.services.system_registry import System, SystemRegistry

Confidence = Literal["high", "medium", "low", "unknown"]


class SystemCandidate(BaseModel):
    code: str
    display_name: str


class SystemDetection(BaseModel):
    detected_code: str | None
    confidence: Confidence
    candidates: list[SystemCandidate]
    suggested_display_name: str
    reason: str  # human-readable explanation, useful for tooltips


_PAREN_CODE_RE = re.compile(r"\(([A-Z][A-Z0-9]*)\)")

# Tokens we strip from filenames when suggesting a display name. Matches
# things like "(USA)", "(World)", "(Rev 1)", "(v1.2.3)", "(Beta)", "[!]",
# "[T-En]", etc.
_NOISE_RE = re.compile(
    r"""\s*(
        \([^)]*\)           # any parenthesized chunk
        |
        \[[^\]]*\]          # any bracketed chunk
    )""",
    re.VERBOSE,
)


def _candidate(s: System) -> SystemCandidate:
    return SystemCandidate(code=s.code, display_name=s.display_name)


def suggest_display_name(filename: str) -> str:
    """Strip extension and trailing region/version noise from a filename.

    Examples::

        suggest_display_name("Pokemon Unbound (v2.1.1.1).gba")
        # -> "Pokemon Unbound"

        suggest_display_name("Kirby's Dream Land 2 (USA).gb")
        # -> "Kirby's Dream Land 2"

        suggest_display_name("Tetris.nes")
        # -> "Tetris"
    """
    stem = PurePath(filename).stem
    cleaned = _NOISE_RE.sub("", stem).strip()
    return cleaned or stem  # fall back to raw stem if cleaning ate everything


def detect(filename: str, registry: SystemRegistry) -> SystemDetection:
    """Detect the system from ``filename``. Always returns a result."""
    suggested = suggest_display_name(filename)
    ext = PurePath(filename).suffix.lower()

    # 1. Parenthesized code in the filename
    for match in _PAREN_CODE_RE.findall(filename):
        if match in registry.codes:
            sys_match = registry.get(match)
            return SystemDetection(
                detected_code=match,
                confidence="high",
                candidates=[_candidate(sys_match)] if sys_match else [],
                suggested_display_name=suggested,
                reason=f"Filename contains the system code ({match}) in parentheses.",
            )

    # 2/3. Extension-based detection
    if ext:
        ext_systems = registry.systems_for_extension(ext)
        if len(ext_systems) == 1:
            chosen = ext_systems[0]
            return SystemDetection(
                detected_code=chosen.code,
                confidence="medium",
                candidates=[_candidate(chosen)],
                suggested_display_name=suggested,
                reason=f"{ext} maps unambiguously to {chosen.code} ({chosen.display_name}).",
            )
        if len(ext_systems) > 1:
            chosen = ext_systems[0]  # highest extension_preference
            others = ", ".join(s.code for s in ext_systems[1:])
            return SystemDetection(
                detected_code=chosen.code,
                confidence="low",
                candidates=[_candidate(s) for s in ext_systems],
                suggested_display_name=suggested,
                reason=(
                    f"{ext} could be {chosen.code} or {others}; "
                    f"defaulted to {chosen.code} ({chosen.display_name}). "
                    "Override if wrong."
                ),
            )

    # 4. No clue — return everything so the user can pick.
    return SystemDetection(
        detected_code=None,
        confidence="unknown",
        candidates=[_candidate(s) for s in registry.all],
        suggested_display_name=suggested,
        reason=(
            "Couldn't infer a system from the filename or extension. "
            "Please pick one."
        ),
    )
