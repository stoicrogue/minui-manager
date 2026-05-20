"""libretro-thumbnails as the box-art source.

Each MinUI system code maps to a libretro-thumbnails GitHub repo
(see ``systems.yaml`` → ``libretro_repo``). For each repo we fetch the
``Named_Boxarts`` directory listing from the GitHub Contents API, cache
it for 24 hours, and fuzzy-match the user's library entry against the
thumbnail filenames using rapidfuzz token_set_ratio.

The fetcher uses httpx; tests inject a fake fetcher via the
``fetch_listing`` function reference so we never hit the real GitHub
during pytest.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from urllib.parse import quote

import httpx
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models import LibretroListingCache

logger = logging.getLogger(__name__)

LISTING_TTL = timedelta(hours=24)
MATCH_THRESHOLD = 75
MAX_RESULTS = 5

# Use the Git Trees API rather than the Contents API: Contents caps at 1000
# entries per directory, which truncates many libretro-thumbnails repos
# (Game Boy alone has > 1000 box arts and 'Tetris' falls past the cutoff).
# Trees can return the full repo in one call up to ~100k entries.
GITHUB_TREE_URL = (
    "https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/HEAD?recursive=1"
)
RAW_URL = (
    "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/{path}"
)
NAMED_BOXARTS_PREFIX = "Named_Boxarts/"


# Tokens we strip from filenames when matching: anything in parens or brackets
# (region, version, [!], translation tags, etc.).
_NOISE_RE = re.compile(r"\s*(\([^)]*\)|\[[^\]]*\])")


@dataclass(frozen=True)
class ThumbnailEntry:
    name: str           # e.g. "Tetris (World) (Rev A).png"
    download_url: str   # raw.githubusercontent.com/... URL


@dataclass(frozen=True)
class ThumbnailCandidate:
    name: str
    score: int
    source_url: str
    source: str = "libretro"


# ---------------------------------------------------------------------------
# Fetcher — swappable for tests
# ---------------------------------------------------------------------------


def fetch_listing(repo: str, http_client: httpx.Client | None = None) -> list[ThumbnailEntry]:
    """Fetch the full ``Named_Boxarts/`` listing for a libretro-thumbnails repo.

    Uses the Git Trees API (recursive) to avoid the 1000-entry cap on the
    Contents API. Raw download URLs are constructed from the path.

    Raises :class:`httpx.HTTPStatusError` on 4xx/5xx (caller maps to API errors).
    """
    url = GITHUB_TREE_URL.format(repo=repo)
    client = http_client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        resp = client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    finally:
        if http_client is None:
            client.close()

    if data.get("truncated"):
        logger.warning(
            "Tree truncated for libretro-thumbnails/%s — some box arts may be missing.",
            repo,
        )

    entries: list[ThumbnailEntry] = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path.startswith(NAMED_BOXARTS_PREFIX):
            continue
        name = path[len(NAMED_BOXARTS_PREFIX):]
        # Skip nested directories or non-PNG files.
        if "/" in name or not name.lower().endswith(".png"):
            continue
        download_url = RAW_URL.format(repo=repo, path=quote(path, safe="/"))
        entries.append(ThumbnailEntry(name=name, download_url=download_url))
    return entries


def download_image(url: str, http_client: httpx.Client | None = None) -> bytes:
    """Download bytes for a chosen candidate image."""
    client = http_client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content
    finally:
        if http_client is None:
            client.close()


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_fresh(fetched_at: datetime, now: datetime | None = None) -> bool:
    now = now or _utcnow()
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (now - fetched_at) < LISTING_TTL


def load_cached(session: Session, repo: str) -> list[ThumbnailEntry] | None:
    """Return the cached listing for ``repo`` if it's fresh, else None."""
    row = session.get(LibretroListingCache, repo)
    if row is None or not _is_fresh(row.fetched_at):
        return None
    try:
        data = json.loads(row.listing_json)
    except json.JSONDecodeError:
        return None
    return [
        ThumbnailEntry(name=item["name"], download_url=item["download_url"])
        for item in data
        if "name" in item and "download_url" in item
    ]


def store_cached(session: Session, repo: str, entries: list[ThumbnailEntry]) -> None:
    payload = json.dumps(
        [{"name": e.name, "download_url": e.download_url} for e in entries]
    )
    row = session.get(LibretroListingCache, repo)
    if row is None:
        row = LibretroListingCache(repo=repo, listing_json=payload, fetched_at=_utcnow())
        session.add(row)
    else:
        row.listing_json = payload
        row.fetched_at = _utcnow()
    # Flush so subsequent session.get() in the same session can find it.
    session.flush()


def get_or_fetch_listing(
    session: Session,
    repo: str,
    fetcher=None,
) -> list[ThumbnailEntry]:
    """Return the cached listing if fresh, otherwise fetch + cache + return.

    ``fetcher`` is resolved at call time (not as a default-arg binding)
    so test monkeypatching of ``fetch_listing`` reaches the router.
    """
    cached = load_cached(session, repo)
    if cached is not None:
        return cached
    if fetcher is None:
        fetcher = fetch_listing  # module-level lookup honors monkeypatching
    fresh = fetcher(repo)
    store_cached(session, repo, fresh)
    return fresh


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _strip_noise(text: str) -> str:
    return _NOISE_RE.sub("", text).strip()


def match_thumbnails(
    query: str,
    entries: list[ThumbnailEntry],
    limit: int = MAX_RESULTS,
    threshold: int = MATCH_THRESHOLD,
) -> list[ThumbnailCandidate]:
    """Score every entry against ``query`` and return the top ``limit``.

    Primary score is ``fuzz.token_set_ratio`` (handles re-ordered tokens
    and extra region tags). Ties are broken by:

      1. ``fuzz.ratio`` (Levenshtein) — penalizes long compilation entries
         like "Mani 4 in 1 - Tetris + ..." against a short query.
      2. Target length — shorter wins, since canonical entries like
         "Tetris (World).png" tend to be shorter than compilations.
      3. Name alphabetical, just for deterministic output.
    """
    query_clean = _strip_noise(query)
    scored: list[tuple[ThumbnailEntry, int, int, int]] = []
    for e in entries:
        target = _strip_noise(PurePath(e.name).stem)
        primary = int(fuzz.token_set_ratio(query_clean, target))
        if primary < threshold:
            continue
        secondary = int(fuzz.ratio(query_clean, target))
        scored.append((e, primary, secondary, len(target)))
    scored.sort(key=lambda x: (-x[1], -x[2], x[3], x[0].name))
    return [
        ThumbnailCandidate(name=e.name, score=score, source_url=e.download_url)
        for e, score, _sec, _len in scored[:limit]
    ]
