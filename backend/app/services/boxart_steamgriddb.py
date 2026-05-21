"""SteamGridDB as a secondary box-art source.

Activated when ``settings.steamgriddb_api_key`` is set. Two-step lookup:

1. ``/search/autocomplete/{term}`` resolves a display name to an SGDB
   game id (we take the first match — SGDB ranks by relevance).
2. ``/grids/game/{id}`` returns the grid images for that game,
   filtered to ``types=static`` and portrait dimensions so we never
   get a 920×430 banner that ``image_processor`` would have to ugly-crop.

Failures here must never break the picker: if SGDB is down or the key
is rejected, the router falls back to libretro-only results with a
note. The key is never logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://www.steamgriddb.com/api/v2"
SEARCH_URL = API_BASE + "/search/autocomplete/{term}"
GRIDS_URL = API_BASE + "/grids/game/{game_id}"

GRID_TYPES = "static"  # exclude animated (PIL.Image.open + our processor can't handle them)
# Aspect ratio above which a grid counts as "portrait enough" for MinUI's
# 200x300 (2:3 = 0.67) slot. Anything wider than 0.95 looks like a banner
# and would have to be ugly-cropped by image_processor.
PORTRAIT_RATIO_MAX = 0.95
# Hard ceiling on candidates returned to the picker.
MAX_RESULTS_FROM_API = 60  # SGDB returns plenty; we'll filter then cap

MAX_CANDIDATES = 8  # show more than libretro (5) since SGDB has fewer perfect matches
HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class SgdbGame:
    id: int
    name: str


@dataclass(frozen=True)
class SgdbGrid:
    id: int
    url: str  # full-size image URL
    thumb_url: str  # smaller preview, good for the picker
    width: int
    height: int
    score: int  # SGDB community score
    author: str | None = None


@dataclass(frozen=True)
class SgdbCandidate:
    """Shape that mirrors ThumbnailCandidate so the router can list them
    side-by-side with libretro results without per-source branching."""

    name: str  # human label for the picker
    score: int
    source_url: str  # what we'll feed to /api/boxart/select
    thumb_url: str  # what the dialog renders inline
    source: str = "steamgriddb"


# ---------------------------------------------------------------------------
# Fetchers — swappable for tests (module-level so monkeypatch reaches them)
# ---------------------------------------------------------------------------


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def search_game(
    query: str, api_key: str, http_client: httpx.Client | None = None
) -> SgdbGame | None:
    """Resolve ``query`` to the top SGDB game match, or None.

    Raises :class:`httpx.HTTPStatusError` on 4xx/5xx — caller decides
    how to surface that (the router degrades to libretro-only).
    """
    term = query.strip()
    if not term:
        return None
    # Path-segment encoding: SGDB autocomplete takes the term as a URL
    # path component, so `&`, `?`, `/`, `#`, `'`, etc. must be percent-encoded
    # or the request 400s. ``quote(safe="")`` leaves nothing untouched.
    url = SEARCH_URL.format(term=quote(term, safe=""))
    client = http_client or httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)
    try:
        resp = client.get(url, headers=_auth_headers(api_key))
        if resp.status_code >= 400:
            logger.warning(
                "SGDB /search/autocomplete returned %s for term=%r: %s",
                resp.status_code,
                term,
                resp.text[:500],
            )
            resp.raise_for_status()
        data = resp.json()
    finally:
        if http_client is None:
            client.close()

    if not data.get("success"):
        return None
    items = data.get("data") or []
    if not items:
        return None
    top = items[0]
    return SgdbGame(id=int(top["id"]), name=str(top["name"]))


def get_grids(
    game_id: int, api_key: str, http_client: httpx.Client | None = None
) -> list[SgdbGrid]:
    """Fetch static grids for an SGDB game id, filtered to portrait
    aspect client-side and ranked by score.

    Why no server-side ``dimensions=`` filter: SGDB's API rejects some
    combinations of values from its own documented allow-list with
    ``{"errors":["Invalid asset dimensions specified"]}``. Filtering
    client-side by aspect ratio is more robust and gives us a wider
    pool of candidates anyway.
    """
    url = GRIDS_URL.format(game_id=game_id) + f"?types={GRID_TYPES}"
    client = http_client or httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)
    try:
        resp = client.get(url, headers=_auth_headers(api_key))
        if resp.status_code >= 400:
            logger.warning(
                "SGDB /grids/game/%s returned %s for url=%s body=%s",
                game_id,
                resp.status_code,
                str(resp.request.url),
                resp.text[:500],
            )
            resp.raise_for_status()
        data = resp.json()
    finally:
        if http_client is None:
            client.close()

    if not data.get("success"):
        return []

    out: list[SgdbGrid] = []
    for item in (data.get("data") or [])[:MAX_RESULTS_FROM_API]:
        url_full = item.get("url")
        if not url_full:
            continue
        width = int(item.get("width", 0) or 0)
        height = int(item.get("height", 0) or 0)
        # Skip landscape banners — they look terrible cropped to 200x300.
        if width == 0 or height == 0:
            continue
        if width / height > PORTRAIT_RATIO_MAX:
            continue
        out.append(
            SgdbGrid(
                id=int(item.get("id", 0)),
                url=str(url_full),
                thumb_url=str(item.get("thumb") or url_full),
                width=width,
                height=height,
                score=int(item.get("score") or 0),
                author=(item.get("author") or {}).get("name") if isinstance(item.get("author"), dict) else None,
            )
        )
    # Sort by SGDB score descending. Ties: prefer taller (more pixels to crop from).
    out.sort(key=lambda g: (-g.score, -g.height))
    return out


# ---------------------------------------------------------------------------
# High-level: resolve query → list of candidates the picker can render
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SgdbLookup:
    """Result of a complete lookup. ``game`` is None when search returned
    nothing; ``candidates`` may be empty even when ``game`` is set."""

    game: SgdbGame | None
    candidates: list[SgdbCandidate]
    note: str | None = None


def find_candidates(
    query: str,
    api_key: str,
    limit: int = MAX_CANDIDATES,
    http_client: httpx.Client | None = None,
) -> SgdbLookup:
    """Run the full two-step lookup. Returns an empty lookup with a
    note when SGDB can't help — never raises.
    """
    if not api_key:
        return SgdbLookup(game=None, candidates=[], note="No SteamGridDB API key set.")
    try:
        game = search_game(query, api_key, http_client=http_client)
    except httpx.HTTPStatusError as exc:
        # 401/403 → bad key. 429 → rate limit. Anything else → server-side hiccup.
        return SgdbLookup(
            game=None,
            candidates=[],
            note=_friendly_error(exc.response.status_code, "search"),
        )
    except httpx.HTTPError:
        return SgdbLookup(
            game=None, candidates=[], note="SteamGridDB search couldn't be reached."
        )
    if game is None:
        return SgdbLookup(
            game=None,
            candidates=[],
            note=f"No SteamGridDB game matched {query!r}.",
        )
    try:
        grids = get_grids(game.id, api_key, http_client=http_client)
    except httpx.HTTPStatusError as exc:
        return SgdbLookup(
            game=game,
            candidates=[],
            note=_friendly_error(exc.response.status_code, "grids"),
        )
    except httpx.HTTPError:
        return SgdbLookup(
            game=game,
            candidates=[],
            note="SteamGridDB grids couldn't be reached.",
        )

    candidates = [
        SgdbCandidate(
            name=f"{g.width}×{g.height} grid",
            score=g.score,
            source_url=g.url,
            thumb_url=g.thumb_url,
        )
        for g in grids[:limit]
    ]
    note = None if candidates else "SteamGridDB has no portrait grids for this game yet."
    return SgdbLookup(game=game, candidates=candidates, note=note)


def _friendly_error(status: int, stage: str) -> str:
    if status in (401, 403):
        return "SteamGridDB rejected the API key — check Settings."
    if status == 429:
        return "SteamGridDB rate limit hit. Try again in a moment."
    return (
        f"SteamGridDB {stage} returned {status}. Check the backend terminal "
        "for the SGDB response body."
    )
