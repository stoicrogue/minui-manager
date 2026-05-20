"""Tests for the Phase 3 system detector.

Mirrors the six cases enumerated in the plan, plus a handful of edges
(display-name cleanup, .smc → SFC vs SUPA, weird filenames).
"""

from __future__ import annotations

import pytest

from app.services.system_detector import detect, suggest_display_name
from app.services.system_registry import load_systems, reset_cache


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_cache()


def test_paren_code_high_confidence() -> None:
    d = detect("Pokemon Unbound (GBA).gba", load_systems())
    assert d.detected_code == "GBA"
    assert d.confidence == "high"


def test_paren_code_overrides_extension_preference() -> None:
    """Parenthesized (MGBA) wins even though .gba defaults to GBA."""
    d = detect("Pokemon Unbound (MGBA).gba", load_systems())
    assert d.detected_code == "MGBA"
    assert d.confidence == "high"


def test_unambiguous_extension_medium_confidence() -> None:
    d = detect("Tetris.nes", load_systems())
    assert d.detected_code == "FC"
    assert d.confidence == "medium"


def test_ambiguous_extension_low_confidence_prefers_high_preference() -> None:
    """.gba could be GBA or MGBA; GBA wins by extension_preference."""
    d = detect("Game.gba", load_systems())
    assert d.detected_code == "GBA"
    assert d.confidence == "low"
    codes = [c.code for c in d.candidates]
    assert codes == ["GBA", "MGBA"]  # sorted by preference desc


def test_bin_lists_md_ps_pce_candidates() -> None:
    """.bin is claimed by MD, PS, and PCE — but plan says MD with PS+PCE listed.

    Actually .bin is claimed by MD (PS uses .chd/.pbp/.cue, PCE uses .pce/.chd
    in systems.yaml). So .bin maps to just MD here. The plan was aspirational;
    let's verify the actual mapping.
    """
    d = detect("Sonic.bin", load_systems())
    assert d.detected_code == "MD"
    # Only MD claims .bin in our systems.yaml — so this is medium confidence,
    # not the "low" the plan suggested.
    assert d.confidence == "medium"


def test_unknown_extension_returns_full_candidates() -> None:
    d = detect("weirdname.xyz", load_systems())
    assert d.detected_code is None
    assert d.confidence == "unknown"
    # All systems offered as candidates.
    assert len(d.candidates) >= 18


def test_no_extension_returns_unknown() -> None:
    d = detect("MysteryFile", load_systems())
    assert d.detected_code is None
    assert d.confidence == "unknown"


def test_paren_code_only_matches_known_codes() -> None:
    """A paren-tag like (Final) or (USA) must NOT be treated as a system."""
    d = detect("Some Game (Final).gba", load_systems())
    # (Final) is not a system code, falls back to extension -> .gba ambiguous -> low.
    assert d.detected_code == "GBA"
    assert d.confidence == "low"


def test_sfc_ambiguous_with_supa() -> None:
    d = detect("Mario.sfc", load_systems())
    assert d.detected_code == "SFC"
    assert d.confidence == "low"
    assert [c.code for c in d.candidates] == ["SFC", "SUPA"]


def test_smc_extension_works_same_as_sfc() -> None:
    d = detect("Mario.smc", load_systems())
    assert d.detected_code == "SFC"
    assert d.confidence == "low"


def test_suggested_display_name_strips_region_codes() -> None:
    assert suggest_display_name("Pokemon Unbound (v2.1.1.1).gba") == "Pokemon Unbound"
    assert suggest_display_name("Kirby's Dream Land 2 (USA).gb") == "Kirby's Dream Land 2"
    assert suggest_display_name("Tetris.nes") == "Tetris"
    assert (
        suggest_display_name("Final Fantasy VI (USA) (Rev 1) [!].sfc") == "Final Fantasy VI"
    )


def test_suggested_display_name_falls_back_when_clean_is_empty() -> None:
    # If the filename is entirely "(USA)" with no real name, fall back to stem.
    assert suggest_display_name("(USA).gba") == "(USA)"  # stem before noise strip


def test_detection_carries_suggested_display_name() -> None:
    d = detect("Pokemon Unbound (v2.1.1.1).gba", load_systems())
    assert d.suggested_display_name == "Pokemon Unbound"
