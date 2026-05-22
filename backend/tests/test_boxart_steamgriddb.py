"""Unit + integration tests for the SteamGridDB box-art source.

The HTTP layer is mocked via ``httpx.MockTransport`` so no live SGDB
traffic happens during tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import boxart_steamgriddb as sgdb


# ---------------------------------------------------------------------------
# httpx transport helpers
# ---------------------------------------------------------------------------


def _client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(json_body: dict) -> httpx.Response:
    return httpx.Response(200, json=json_body)


def _err(status: int, body: str = "nope") -> httpx.Response:
    return httpx.Response(status, content=body.encode("utf-8"))


# ---------------------------------------------------------------------------
# search_game
# ---------------------------------------------------------------------------


def test_search_game_returns_first_match() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer KEY"
        assert "search/autocomplete/Chrono%20Trigger" in str(req.url)
        return _ok(
            {
                "success": True,
                "data": [
                    {"id": 12345, "name": "Chrono Trigger", "types": ["snes"]},
                    {"id": 67890, "name": "Chrono Trigger DS", "types": ["nds"]},
                ],
            }
        )

    with _client_with(handler) as client:
        game = sgdb.search_game("Chrono Trigger", "KEY", http_client=client)
    assert game is not None
    assert game.id == 12345
    assert game.name == "Chrono Trigger"


def test_search_game_returns_none_on_empty_response() -> None:
    with _client_with(lambda req: _ok({"success": True, "data": []})) as client:
        game = sgdb.search_game("Made Up Game", "KEY", http_client=client)
    assert game is None


def test_search_game_returns_none_on_empty_query() -> None:
    """Empty query short-circuits without hitting the network."""
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _ok({"success": True, "data": []})

    with _client_with(handler) as client:
        game = sgdb.search_game("   ", "KEY", http_client=client)
    assert game is None
    assert called is False


def test_search_game_percent_encodes_special_characters() -> None:
    """Apostrophes and ampersands in the query must be URL-encoded into
    the path segment, otherwise SGDB returns 400."""
    captured: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(str(req.url))
        return _ok({"success": True, "data": []})

    with _client_with(handler) as client:
        sgdb.search_game("Kirby's Dream Land & Friends", "KEY", http_client=client)
    url = captured[0]
    # Encoded forms in the URL.
    assert "Kirby%27s" in url
    assert "%26" in url  # encoded &
    # Raw special chars must not be in the URL.
    assert "'" not in url
    assert " & " not in url


def test_search_game_raises_on_401() -> None:
    with _client_with(lambda req: _err(401, "unauth")) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            sgdb.search_game("Anything", "BADKEY", http_client=client)
    assert exc_info.value.response.status_code == 401


# ---------------------------------------------------------------------------
# get_grids
# ---------------------------------------------------------------------------


def test_get_grids_uses_static_type_filter_no_dimensions() -> None:
    """SGDB's API rejects some dimensions= combinations from its own
    documented allow-list, so we don't send the filter at all and filter
    portraits client-side. ``types=static`` is the one filter that matters
    on the wire — animated grids would break our PIL processor."""
    captured: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(str(req.url.raw_path, "utf-8"))
        return _ok({"success": True, "data": []})

    with _client_with(handler) as client:
        sgdb.get_grids(12345, "KEY", http_client=client)
    raw_path = captured[0]
    assert "types=static" in raw_path
    assert "dimensions=" not in raw_path


def test_get_grids_returns_portrait_only_sorted_by_score() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v2/grids/game/12345"
        return _ok(
            {
                "success": True,
                "data": [
                    {  # portrait, low score
                        "id": 1,
                        "url": "https://cdn/full-A.png",
                        "thumb": "https://cdn/thumb-A.jpg",
                        "width": 600,
                        "height": 900,
                        "score": 5,
                        "author": {"name": "alice"},
                    },
                    {  # portrait, higher score
                        "id": 2,
                        "url": "https://cdn/full-B.png",
                        "thumb": "https://cdn/thumb-B.jpg",
                        "width": 600,
                        "height": 900,
                        "score": 12,
                    },
                    {  # landscape banner — must be dropped
                        "id": 99,
                        "url": "https://cdn/banner.png",
                        "thumb": "https://cdn/banner-t.jpg",
                        "width": 920,
                        "height": 430,
                        "score": 100,
                    },
                ],
            }
        )

    with _client_with(handler) as client:
        grids = sgdb.get_grids(12345, "KEY", http_client=client)
    # Banner dropped; portraits sorted by score desc.
    assert [g.id for g in grids] == [2, 1]
    assert grids[0].url == "https://cdn/full-B.png"
    assert grids[1].author == "alice"


def test_get_grids_drops_square_and_banner_aspect_ratios() -> None:
    """1024x1024 (1.0 ratio) is technically valid SGDB dims, but it crops
    badly to a 2:3 portrait. Same for any width >= height. Both go."""

    def handler(req: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "success": True,
                "data": [
                    {"id": 1, "url": "https://cdn/sq.png", "width": 1024, "height": 1024, "score": 1},
                    {"id": 2, "url": "https://cdn/wide.png", "width": 460, "height": 215, "score": 1},
                    {"id": 3, "url": "https://cdn/portrait.png", "width": 600, "height": 900, "score": 1},
                ],
            }
        )

    with _client_with(handler) as client:
        grids = sgdb.get_grids(12345, "KEY", http_client=client)
    assert [g.id for g in grids] == [3]


def test_get_grids_drops_entries_missing_dimensions() -> None:
    """A grid with width=0 or no dims can't be aspect-classified; skip it."""

    def handler(req: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "success": True,
                "data": [
                    {"id": 1, "url": "https://cdn/x.png", "width": 0, "height": 900, "score": 1},
                    {"id": 2, "url": "https://cdn/y.png", "width": 600, "score": 1},  # no height
                    {"id": 3, "url": "https://cdn/z.png", "width": 600, "height": 900, "score": 1},
                ],
            }
        )

    with _client_with(handler) as client:
        grids = sgdb.get_grids(12345, "KEY", http_client=client)
    assert [g.id for g in grids] == [3]


def test_get_grids_skips_entries_without_url() -> None:
    bad_then_good = {
        "success": True,
        "data": [
            {"id": 1, "thumb": "x", "width": 600, "height": 900, "score": 5},  # no url
            {"id": 2, "url": "https://cdn/B.png", "width": 600, "height": 900, "score": 1},
        ],
    }
    with _client_with(lambda req: _ok(bad_then_good)) as client:
        grids = sgdb.get_grids(12345, "KEY", http_client=client)
    assert len(grids) == 1
    assert grids[0].id == 2


def test_get_grids_returns_empty_on_success_false() -> None:
    with _client_with(lambda req: _ok({"success": False, "errors": ["nope"]})) as client:
        grids = sgdb.get_grids(12345, "KEY", http_client=client)
    assert grids == []


# ---------------------------------------------------------------------------
# find_candidates — graceful degradation is the headline contract
# ---------------------------------------------------------------------------


def test_find_candidates_short_circuits_when_no_key() -> None:
    """No key → empty lookup with a friendly note, no network calls."""
    result = sgdb.find_candidates("Anything", api_key="")
    assert result.game is None
    assert result.candidates == []
    assert "API key" in (result.note or "")


def test_find_candidates_happy_path() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "search/autocomplete" in str(req.url):
            return _ok(
                {"success": True, "data": [{"id": 7, "name": "Chrono Trigger"}]}
            )
        if "grids/game/7" in str(req.url):
            return _ok(
                {
                    "success": True,
                    "data": [
                        {
                            "id": 100,
                            "url": "https://cdn/full.png",
                            "thumb": "https://cdn/thumb.jpg",
                            "width": 600,
                            "height": 900,
                            "score": 3,
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected URL {req.url}")

    with _client_with(handler) as client:
        result = sgdb.find_candidates("Chrono Trigger", "KEY", http_client=client)
    assert result.game is not None
    assert result.game.name == "Chrono Trigger"
    assert len(result.candidates) == 1
    assert result.candidates[0].source_url == "https://cdn/full.png"
    assert result.candidates[0].thumb_url == "https://cdn/thumb.jpg"
    assert result.candidates[0].source == "steamgriddb"
    assert result.note is None


def test_find_candidates_401_returns_friendly_note_not_exception() -> None:
    """A bad API key must NOT raise — it surfaces as a note so the
    libretro half of the picker keeps rendering."""
    with _client_with(lambda req: _err(401)) as client:
        result = sgdb.find_candidates("X", "BADKEY", http_client=client)
    assert result.candidates == []
    assert "API key" in (result.note or "")


def test_find_candidates_429_returns_rate_limit_note() -> None:
    with _client_with(lambda req: _err(429)) as client:
        result = sgdb.find_candidates("X", "KEY", http_client=client)
    assert "rate limit" in (result.note or "").lower()


def test_find_candidates_network_error_returns_friendly_note() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS broke")

    with _client_with(boom) as client:
        result = sgdb.find_candidates("X", "KEY", http_client=client)
    assert "couldn't be reached" in (result.note or "")


def test_find_candidates_no_match_returns_note() -> None:
    with _client_with(lambda req: _ok({"success": True, "data": []})) as client:
        result = sgdb.find_candidates("ZZZ-nonsense", "KEY", http_client=client)
    assert result.game is None
    assert "No SteamGridDB game" in (result.note or "")


def test_find_candidates_match_but_no_grids() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "search/autocomplete" in str(req.url):
            return _ok({"success": True, "data": [{"id": 5, "name": "Obscure"}]})
        return _ok({"success": True, "data": []})

    with _client_with(handler) as client:
        result = sgdb.find_candidates("Obscure", "KEY", http_client=client)
    assert result.game is not None
    assert result.candidates == []
    assert "no portrait grids" in (result.note or "").lower()


# ---------------------------------------------------------------------------
# Router integration — exercises /api/boxart/search end-to-end
# ---------------------------------------------------------------------------


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


def _add_library_entry(client: TestClient, filename: str, code: str, display: str) -> int:
    up = client.post(
        "/api/library/upload",
        files={"files": (filename, b"\x00" * 16, "application/octet-stream")},
    ).json()
    confirmed = client.post(
        f"/api/library/drafts/{up['draft_id']}/confirm",
        json={"system_code": code, "display_name": display},
    ).json()
    return confirmed["id"]


def _set_sgdb_key(client: TestClient, key: str | None) -> None:
    client.patch("/api/settings", json={"steamgriddb_api_key": key})


def _patch_libretro(monkeypatch: pytest.MonkeyPatch, entries: list[tuple[str, str]]) -> None:
    from app.routers import boxart as boxart_router
    from app.services import boxart_libretro

    def fake(repo: str, http_client=None):
        return [
            boxart_libretro.ThumbnailEntry(name=n, download_url=u) for n, u in entries
        ]

    monkeypatch.setattr(boxart_libretro, "fetch_listing", fake)
    monkeypatch.setattr(boxart_router.boxart_libretro, "fetch_listing", fake)


def test_search_excludes_sgdb_section_when_no_key(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No API key → no `steamgriddb` field. Existing libretro response shape unchanged."""
    client = _client(tmp_project_root)
    _patch_libretro(monkeypatch, [("Tetris (World).png", "https://raw/tetris.png")])
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    assert r.status_code == 200
    body = r.json()
    assert body["steamgriddb"] is None
    assert any(c["name"] == "Tetris (World).png" for c in body["candidates"])


def test_search_includes_sgdb_section_when_key_set(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routers import boxart as boxart_router
    from app.services import boxart_steamgriddb

    client = _client(tmp_project_root)
    _set_sgdb_key(client, "TESTKEY")
    _patch_libretro(monkeypatch, [("Tetris (World).png", "https://raw/tetris.png")])

    def fake_lookup(query: str, api_key: str, **kwargs):
        assert api_key == "TESTKEY"
        return boxart_steamgriddb.SgdbLookup(
            game=boxart_steamgriddb.SgdbGame(id=42, name="Tetris"),
            candidates=[
                boxart_steamgriddb.SgdbCandidate(
                    name="600×900 grid",
                    score=8,
                    source_url="https://cdn/tetris.png",
                    thumb_url="https://cdn/tetris-thumb.jpg",
                )
            ],
            note=None,
        )

    monkeypatch.setattr(boxart_steamgriddb, "find_candidates", fake_lookup)
    monkeypatch.setattr(boxart_router.boxart_steamgriddb, "find_candidates", fake_lookup)

    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    assert r.status_code == 200
    body = r.json()
    sgdb = body["steamgriddb"]
    assert sgdb["game_id"] == 42
    assert sgdb["game_name"] == "Tetris"
    assert len(sgdb["candidates"]) == 1
    assert sgdb["candidates"][0]["source"] == "steamgriddb"
    assert sgdb["candidates"][0]["thumb_url"] == "https://cdn/tetris-thumb.jpg"
    # Libretro half is unchanged.
    assert any(c["name"] == "Tetris (World).png" for c in body["candidates"])


def test_search_returns_sgdb_section_for_system_without_libretro_repo(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P8 has no libretro repo — SGDB should still run when a key is set."""
    from app.routers import boxart as boxart_router
    from app.services import boxart_steamgriddb

    client = _client(tmp_project_root)
    _set_sgdb_key(client, "K")

    def fake_lookup(query, api_key, **kwargs):
        return boxart_steamgriddb.SgdbLookup(
            game=boxart_steamgriddb.SgdbGame(id=1, name="Some Pico8 Game"),
            candidates=[
                boxart_steamgriddb.SgdbCandidate(
                    name="600×900 grid",
                    score=0,
                    source_url="https://cdn/p8.png",
                    thumb_url="https://cdn/p8t.jpg",
                )
            ],
        )

    monkeypatch.setattr(boxart_steamgriddb, "find_candidates", fake_lookup)
    monkeypatch.setattr(boxart_router.boxart_steamgriddb, "find_candidates", fake_lookup)

    lib_id = _add_library_entry(client, "game.p8", "P8", "Some Pico8 Game")
    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    body = r.json()
    assert body["repo"] is None
    assert body["candidates"] == []
    assert body["steamgriddb"]["game_name"] == "Some Pico8 Game"


def test_search_degrades_gracefully_when_sgdb_fails(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad SGDB key must NOT prevent the libretro candidates from rendering."""
    from app.routers import boxart as boxart_router
    from app.services import boxart_steamgriddb

    client = _client(tmp_project_root)
    _set_sgdb_key(client, "BADKEY")
    _patch_libretro(monkeypatch, [("Tetris (World).png", "https://raw/tetris.png")])

    def fake_lookup(query, api_key, **kwargs):
        return boxart_steamgriddb.SgdbLookup(
            game=None,
            candidates=[],
            note="SteamGridDB rejected the API key — check Settings.",
        )

    monkeypatch.setattr(boxart_steamgriddb, "find_candidates", fake_lookup)
    monkeypatch.setattr(boxart_router.boxart_steamgriddb, "find_candidates", fake_lookup)

    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    assert r.status_code == 200
    body = r.json()
    # Libretro candidates still present.
    assert any(c["name"] == "Tetris (World).png" for c in body["candidates"])
    # SGDB section has the explanatory note, no candidates.
    sgdb = body["steamgriddb"]
    assert sgdb["candidates"] == []
    assert "API key" in sgdb["note"]
