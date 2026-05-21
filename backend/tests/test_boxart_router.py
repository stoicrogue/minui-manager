"""HTTP-level tests for the boxart router.

The libretro fetcher + downloader are monkeypatched so no live GitHub
traffic happens.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _real_png(size: tuple[int, int] = (400, 600), color: str = "red") -> bytes:
    """Build a real decodable PNG of the requested size — Phase 5 runs every
    downloaded image through PIL, so the fake \\x89PNG header bytes won't work
    anymore.
    """
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


def _add_library_entry(client: TestClient, filename: str, code: str, display: str) -> int:
    """Helper: upload + confirm a single library entry, return its id."""
    up = client.post(
        "/api/library/upload",
        files={"file": (filename, b"\x00" * 16, "application/octet-stream")},
    ).json()
    confirmed = client.post(
        f"/api/library/drafts/{up['draft_id']}/confirm",
        json={"system_code": code, "display_name": display},
    ).json()
    return confirmed["id"]


def _patch_fetcher(monkeypatch: pytest.MonkeyPatch, entries: list[tuple[str, str]]) -> None:
    """Make boxart_libretro.fetch_listing return ``entries`` (name, url)."""
    from app.services import boxart_libretro
    from app.routers import boxart as boxart_router

    def fake_fetch(repo: str, http_client=None):
        return [
            boxart_libretro.ThumbnailEntry(name=n, download_url=u) for n, u in entries
        ]

    monkeypatch.setattr(boxart_libretro, "fetch_listing", fake_fetch)
    # Some call sites import fetch_listing by reference into the router module —
    # not the case here (router calls get_or_fetch_listing which dispatches to
    # boxart_libretro.fetch_listing), but be defensive.
    monkeypatch.setattr(boxart_router.boxart_libretro, "fetch_listing", fake_fetch)


def test_search_returns_top_candidates(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    _patch_fetcher(
        monkeypatch,
        [
            ("Tetris (World).png", "https://raw/tetris-world.png"),
            ("Tetris DX (USA, Europe).png", "https://raw/tetris-dx.png"),
            ("Super Mario Land (World).png", "https://raw/sml.png"),
        ],
    )

    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    assert r.status_code == 200
    body = r.json()
    assert body["system_code"] == "GB"
    assert body["repo"] == "Nintendo_-_Game_Boy"
    names = [c["name"] for c in body["candidates"]]
    # 'Super Mario Land' shouldn't match 'Tetris'.
    assert "Super Mario Land (World).png" not in names
    assert "Tetris (World).png" in names


def test_search_404_when_library_id_missing(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_project_root)
    r = client.get("/api/boxart/search", params={"library_id": 999})
    assert r.status_code == 404


def test_search_returns_empty_with_note_when_no_repo(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pico-8 (P8) has libretro_repo: null."""
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "game.p8", "P8", "MyP8Game")

    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == []
    assert body["repo"] is None
    assert "P8" in body["note"]


def test_search_uses_query_override(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    _patch_fetcher(
        monkeypatch,
        [
            ("Tetris (World).png", "https://raw/tetris.png"),
            ("Final Fantasy VI (USA).png", "https://raw/ff6.png"),
        ],
    )
    r = client.get(
        "/api/boxart/search",
        params={"library_id": lib_id, "query": "Final Fantasy VI"},
    )
    body = r.json()
    assert body["query"] == "Final Fantasy VI"
    assert any(c["name"] == "Final Fantasy VI (USA).png" for c in body["candidates"])


def test_search_handles_github_404(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing repo (404 from GitHub) -> empty candidates with a note."""
    import httpx

    from app.services import boxart_libretro
    from app.routers import boxart as boxart_router

    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    def fake_fetch(repo: str, http_client=None):
        resp = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com"))
        raise httpx.HTTPStatusError("404", request=resp.request, response=resp)

    monkeypatch.setattr(boxart_libretro, "fetch_listing", fake_fetch)
    monkeypatch.setattr(boxart_router.boxart_libretro, "fetch_listing", fake_fetch)

    r = client.get("/api/boxart/search", params={"library_id": lib_id})
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] == []
    assert "not found" in body["note"].lower()


def test_select_downloads_processes_and_writes_box_art(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The downloaded image is run through the processor and saved as a
    200x300 PNG, not as the raw bytes that came back from GitHub."""
    from app.services import boxart_libretro
    from app.routers import boxart as boxart_router

    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    source = _real_png(size=(400, 600), color="red")

    def fake_dl(url: str, http_client=None) -> bytes:
        assert url == "https://raw/tetris.png"
        return source

    monkeypatch.setattr(boxart_libretro, "download_image", fake_dl)
    monkeypatch.setattr(boxart_router.boxart_libretro, "download_image", fake_dl)

    r = client.post(
        "/api/boxart/select",
        json={
            "library_id": lib_id,
            "source_url": "https://raw/tetris.png",
            "source_name": "Tetris (World).png",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == lib_id
    assert body["has_boxart"] is True
    assert body["boxart_path"] is not None

    saved = Path(body["boxart_path"])
    assert saved.is_file()
    # Saved file is the normalized output, not the raw download.
    assert saved.read_bytes() != source
    with Image.open(saved) as out:
        assert out.size == (200, 300)
        assert out.format == "PNG"


def test_select_422_on_undecodable_image(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The processor rejects garbage bytes and the router maps that to 422."""
    from app.services import boxart_libretro
    from app.routers import boxart as boxart_router

    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    monkeypatch.setattr(
        boxart_libretro, "download_image", lambda u, http_client=None: b"not an image"
    )
    monkeypatch.setattr(
        boxart_router.boxart_libretro,
        "download_image",
        lambda u, http_client=None: b"not an image",
    )

    r = client.post(
        "/api/boxart/select",
        json={"library_id": lib_id, "source_url": "https://raw/broken.png"},
    )
    assert r.status_code == 422
    assert "process" in r.json()["detail"].lower()


def test_select_honors_resize_strategy_from_settings(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A landscape source under 'contain' must letterbox with black; the
    selected strategy on settings is what the processor must use."""
    from app.services import boxart_libretro
    from app.routers import boxart as boxart_router

    client = _client(tmp_project_root)

    # Flip the strategy to 'contain' via the settings endpoint.
    client.patch("/api/settings", json={"boxart_resize_strategy": "contain"})

    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    source = _real_png(size=(600, 300), color="white")

    monkeypatch.setattr(
        boxart_libretro, "download_image", lambda u, http_client=None: source
    )
    monkeypatch.setattr(
        boxart_router.boxart_libretro, "download_image", lambda u, http_client=None: source
    )

    r = client.post(
        "/api/boxart/select",
        json={"library_id": lib_id, "source_url": "https://raw/tetris.png"},
    )
    assert r.status_code == 200
    saved = Path(r.json()["boxart_path"])
    with Image.open(saved) as out:
        rgb = out.convert("RGB")
        assert rgb.size == (200, 300)
        # Top strip of a letterboxed image is black; center is the white source.
        assert rgb.getpixel((100, 10)) == (0, 0, 0)
        assert rgb.getpixel((100, 150)) == (255, 255, 255)


def test_select_404_for_unknown_library_id(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_project_root)
    r = client.post(
        "/api/boxart/select",
        json={"library_id": 999, "source_url": "https://x"},
    )
    assert r.status_code == 404


def test_upload_normalizes_and_writes_box_art(tmp_project_root: Path) -> None:
    """User-uploaded image is run through the processor and saved as 200x300 PNG."""
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    source = _real_png(size=(400, 600), color="blue")
    r = client.post(
        "/api/boxart/upload",
        data={"library_id": str(lib_id)},
        files={"file": ("user-art.png", source, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == lib_id
    assert body["has_boxart"] is True

    saved = Path(body["boxart_path"])
    assert saved.is_file()
    assert saved.read_bytes() != source
    with Image.open(saved) as out:
        assert out.size == (200, 300)
        assert out.format == "PNG"


def test_upload_422_on_undecodable_image(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    r = client.post(
        "/api/boxart/upload",
        data={"library_id": str(lib_id)},
        files={"file": ("junk.png", b"not an image", "image/png")},
    )
    assert r.status_code == 422
    assert "process" in r.json()["detail"].lower()


def test_upload_404_for_unknown_library_id(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    r = client.post(
        "/api/boxart/upload",
        data={"library_id": "999"},
        files={"file": ("art.png", _real_png(), "image/png")},
    )
    assert r.status_code == 404


def test_upload_400_on_empty_file(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.post(
        "/api/boxart/upload",
        data={"library_id": str(lib_id)},
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert r.status_code == 400


def test_serve_box_art_streams_saved_png(
    tmp_project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import boxart_libretro
    from app.routers import boxart as boxart_router

    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")

    source = _real_png(size=(400, 600), color="green")
    monkeypatch.setattr(boxart_libretro, "download_image", lambda u, http_client=None: source)
    monkeypatch.setattr(
        boxart_router.boxart_libretro, "download_image", lambda u, http_client=None: source
    )

    client.post(
        "/api/boxart/select",
        json={"library_id": lib_id, "source_url": "https://raw/tetris.png"},
    )

    r = client.get(f"/api/library/{lib_id}/box-art")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # The served bytes are the processed 200x300 output, not the raw download.
    with Image.open(BytesIO(r.content)) as img:
        assert img.size == (200, 300)
        assert img.format == "PNG"


def test_serve_box_art_404_when_not_selected(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    lib_id = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.get(f"/api/library/{lib_id}/box-art")
    assert r.status_code == 404
