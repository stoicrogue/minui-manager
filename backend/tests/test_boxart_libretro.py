"""Tests for the libretro-thumbnails fetcher + matcher + cache.

The fetcher is the only piece that touches the network, so it's the
swap-point for tests — we never hit GitHub here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _service():
    from app.services import boxart_libretro

    return boxart_libretro


def test_match_returns_topn_above_threshold(tmp_project_root: Path) -> None:
    bl = _service()
    entries = [
        bl.ThumbnailEntry(name="Tetris (World).png", download_url="u1"),
        bl.ThumbnailEntry(name="Tetris DX (USA, Europe).png", download_url="u2"),
        bl.ThumbnailEntry(name="Tetris 2 (USA).png", download_url="u3"),
        bl.ThumbnailEntry(name="Super Mario Land (World).png", download_url="u4"),
        bl.ThumbnailEntry(name="Kirby's Dream Land (USA).png", download_url="u5"),
    ]
    results = bl.match_thumbnails("Tetris", entries, limit=5)
    # All three Tetris variants beat the threshold.
    assert {r.name for r in results[:3]} >= {"Tetris (World).png", "Tetris 2 (USA).png", "Tetris DX (USA, Europe).png"}
    assert all(r.score >= bl.MATCH_THRESHOLD for r in results)


def test_match_strips_paren_noise_for_scoring(tmp_project_root: Path) -> None:
    """User query 'Pokemon Unbound' matches 'Pokemon Unbound (USA).png' cleanly."""
    bl = _service()
    entries = [
        bl.ThumbnailEntry(name="Pokemon Unbound (USA).png", download_url="u1"),
        bl.ThumbnailEntry(name="Pokemon FireRed (USA).png", download_url="u2"),
        bl.ThumbnailEntry(name="Pokemon Emerald (USA).png", download_url="u3"),
    ]
    results = bl.match_thumbnails("Pokemon Unbound", entries)
    assert results[0].name == "Pokemon Unbound (USA).png"
    assert results[0].score == 100  # perfect after stripping (USA)


def test_match_returns_empty_when_nothing_passes_threshold(tmp_project_root: Path) -> None:
    bl = _service()
    entries = [bl.ThumbnailEntry(name="Castlevania (USA).png", download_url="u")]
    results = bl.match_thumbnails("Final Fantasy VI", entries)
    assert results == []


def test_match_respects_limit(tmp_project_root: Path) -> None:
    bl = _service()
    entries = [
        bl.ThumbnailEntry(name=f"Tetris {n} (USA).png", download_url=f"u{n}")
        for n in range(10)
    ]
    assert len(bl.match_thumbnails("Tetris", entries, limit=3)) == 3
    assert len(bl.match_thumbnails("Tetris", entries, limit=7)) == 7


def test_cache_hit_when_fresh(tmp_project_root: Path) -> None:
    bl = _service()
    from app.db import session_scope

    entries = [bl.ThumbnailEntry(name="Tetris (World).png", download_url="u1")]
    with session_scope() as session:
        bl.store_cached(session, "Nintendo_-_Game_Boy", entries)

    with session_scope() as session:
        cached = bl.load_cached(session, "Nintendo_-_Game_Boy")
    assert cached is not None
    assert cached[0].name == "Tetris (World).png"


def test_cache_miss_when_stale(tmp_project_root: Path) -> None:
    bl = _service()
    from app.db import session_scope
    from app.models import LibretroListingCache

    entries = [bl.ThumbnailEntry(name="Tetris (World).png", download_url="u1")]
    with session_scope() as session:
        bl.store_cached(session, "Nintendo_-_Game_Boy", entries)

    # Backdate in a separate session so the row is durably committed first.
    with session_scope() as session:
        row = session.get(LibretroListingCache, "Nintendo_-_Game_Boy")
        assert row is not None
        row.fetched_at = datetime.now(timezone.utc) - timedelta(hours=25)

    with session_scope() as session:
        assert bl.load_cached(session, "Nintendo_-_Game_Boy") is None


def test_get_or_fetch_uses_cache_when_fresh(tmp_project_root: Path) -> None:
    bl = _service()
    from app.db import session_scope

    entries = [bl.ThumbnailEntry(name="Tetris (World).png", download_url="u1")]
    with session_scope() as session:
        bl.store_cached(session, "Repo_X", entries)

    fetcher_called: list[str] = []

    def fake_fetcher(repo: str) -> list:
        fetcher_called.append(repo)
        return []

    with session_scope() as session:
        result = bl.get_or_fetch_listing(session, "Repo_X", fetcher=fake_fetcher)
    assert [e.name for e in result] == ["Tetris (World).png"]
    assert fetcher_called == []  # fresh cache → no fetch


def test_get_or_fetch_falls_through_to_fetcher_when_no_cache(
    tmp_project_root: Path,
) -> None:
    bl = _service()
    from app.db import session_scope

    fresh = [bl.ThumbnailEntry(name="Fresh.png", download_url="u")]

    def fake_fetcher(repo: str) -> list:
        return fresh

    with session_scope() as session:
        result = bl.get_or_fetch_listing(session, "Repo_Y", fetcher=fake_fetcher)
    assert [e.name for e in result] == ["Fresh.png"]
    # And the cache should now be populated.
    with session_scope() as session:
        cached = bl.load_cached(session, "Repo_Y")
    assert cached is not None
    assert cached[0].name == "Fresh.png"
