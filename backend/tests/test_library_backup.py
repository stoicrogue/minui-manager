"""Library export / import tests (Phase 8)."""

from __future__ import annotations

import io
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (200, 300), "red").save(buf, format="PNG")
    return buf.getvalue()


def _add_library_entry(
    client: TestClient,
    filename: str,
    code: str,
    display: str,
    *,
    rom_payload: bytes = b"ROMBYTES",
    with_boxart: bool = False,
) -> dict:
    up = client.post(
        "/api/library/upload",
        files={"files": (filename, rom_payload, "application/octet-stream")},
    ).json()
    confirmed = client.post(
        f"/api/library/drafts/{up['draft_id']}/confirm",
        json={"system_code": code, "display_name": display},
    ).json()
    if with_boxart:
        art_path = Path(confirmed["library_path"]).parent / ".res" / (
            confirmed["game_folder_name"] + ".png"
        )
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_bytes(_png_bytes())
    return confirmed


def _open_export(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(content), mode="r")


def _build_zip(manifest: dict, files: dict[str, bytes]) -> bytes:
    """Construct a library backup zip in memory."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("library-manifest.json", json.dumps(manifest))
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_returns_zip_with_manifest_and_files(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=True)
    _add_library_entry(client, "Mario.gb", "GB", "Mario", with_boxart=False)

    r = client.get("/api/library/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    with _open_export(r.content) as zf:
        names = sorted(zf.namelist())
        assert "library-manifest.json" in names
        assert "GB/Tetris (GB)/Tetris.gb" in names
        assert "GB/.res/Tetris (GB).png" in names
        assert "GB/Mario (GB)/Mario.gb" in names
        # Mario has no art; no Mario.png expected.
        assert "GB/.res/Mario (GB).png" not in names

        manifest = json.loads(zf.read("library-manifest.json"))
        assert manifest["version"] == 2
        codes = [g["rom_filename"] for g in manifest["games"]]
        # Deterministic sort.
        assert codes == sorted(codes)
        tetris = next(g for g in manifest["games"] if g["rom_filename"] == "Tetris.gb")
        assert tetris["boxart_path"] == "GB/.res/Tetris (GB).png"
        assert tetris["disc_filenames"] == ["Tetris.gb"]
        mario = next(g for g in manifest["games"] if g["rom_filename"] == "Mario.gb")
        assert mario["boxart_path"] is None


def test_export_excludes_pending_uploads(tmp_project_root: Path) -> None:
    """A mid-flight upload in ``_pending/`` must never appear in an export."""
    client = _client(tmp_project_root)
    _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    # Simulate an in-flight upload by hitting /upload but NOT confirming.
    client.post(
        "/api/library/upload",
        files={"files": ("Pending.gb", b"PENDING", "application/octet-stream")},
    )

    r = client.get("/api/library/export")
    with _open_export(r.content) as zf:
        names = zf.namelist()
        assert not any(n.startswith("_pending") for n in names)
        assert not any("PENDING" in n for n in names)


def test_export_is_deterministic_across_calls(tmp_project_root: Path) -> None:
    """Same library, two exports, identical file structure + manifest entries."""
    client = _client(tmp_project_root)
    _add_library_entry(client, "B.gb", "GB", "Bee")
    _add_library_entry(client, "A.gb", "GB", "Aye")

    def snapshot():
        r = client.get("/api/library/export")
        with _open_export(r.content) as zf:
            names = sorted(zf.namelist())
            manifest = json.loads(zf.read("library-manifest.json"))
            game_keys = [
                (g["system_code"], g["rom_filename"]) for g in manifest["games"]
            ]
            return names, game_keys

    n1, m1 = snapshot()
    n2, m2 = snapshot()
    assert n1 == n2
    assert m1 == m2
    # And sorted.
    assert m1 == sorted(m1)


def test_export_flags_rows_whose_files_were_deleted(tmp_project_root: Path) -> None:
    """A library row that's lost its ROM on disk doesn't crash the export
    — it surfaces in ``skipped`` server-side. (User-visible only via the
    download succeeding without that file inside.)"""
    from app import paths as _paths
    from app.db import session_scope
    from app.services.library_backup import export_library

    client = _client(tmp_project_root)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=True)
    # Delete the ROM file underneath us. library_path is now the per-game folder.
    import shutil
    shutil.rmtree(Path(g["library_path"]))

    tmp_zip = _paths.DATA_DIR / "manual-export.zip"
    with session_scope() as session:
        result = export_library(session, tmp_zip)
    assert result.games_written == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["rom_filename"] == "Tetris.gb"


# ---------------------------------------------------------------------------
# Import — happy path + roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_export_then_import_recreates_library(tmp_project_root: Path) -> None:
    """The headline test: export → wipe → import yields the same rows + files."""
    from app import paths as _paths

    client = _client(tmp_project_root)
    g1 = _add_library_entry(
        client, "Tetris.gb", "GB", "Tetris", rom_payload=b"TET", with_boxart=True
    )
    g2 = _add_library_entry(client, "Mario.gb", "GB", "Mario", rom_payload=b"MAR")

    export_bytes = client.get("/api/library/export").content

    # Wipe the library: delete rows + on-disk files.
    client.delete(f"/api/library/{g1['id']}")
    client.delete(f"/api/library/{g2['id']}")
    assert client.get("/api/library").json()["total"] == 0
    assert not (_paths.LIBRARY_DIR / "GB" / "Tetris (GB)").exists()

    # Import.
    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", export_bytes, "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] == 2
    assert body["skipped"] == 0

    listing = client.get("/api/library").json()
    assert listing["total"] == 2
    names = sorted(g["rom_filename"] for g in listing["games"])
    assert names == ["Mario.gb", "Tetris.gb"]

    # Files are back, byte-identical, under per-game folders.
    assert (_paths.LIBRARY_DIR / "GB" / "Tetris (GB)" / "Tetris.gb").read_bytes() == b"TET"
    assert (_paths.LIBRARY_DIR / "GB" / "Mario (GB)" / "Mario.gb").read_bytes() == b"MAR"
    assert (_paths.LIBRARY_DIR / "GB" / ".res" / "Tetris (GB).png").is_file()


def test_import_preserves_display_name_and_added_at(tmp_project_root: Path) -> None:
    """display_name and added_at come from the manifest, not from filenames."""
    from datetime import datetime

    client = _client(tmp_project_root)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris (Custom Name)")
    original_added_at = g["added_at"]

    export_bytes = client.get("/api/library/export").content
    client.delete(f"/api/library/{g['id']}")

    client.post(
        "/api/library/import",
        files={"file": ("backup.zip", export_bytes, "application/zip")},
    )
    listing = client.get("/api/library").json()["games"]
    assert len(listing) == 1
    assert listing[0]["display_name"] == "Tetris (Custom Name)"
    # Compare as naive datetimes — the SQLite DateTime column doesn't store
    # tzinfo, so a round-trip through the DB drops it. That's pre-existing
    # behavior; what matters here is that the wall-clock time is preserved.
    parsed_original = datetime.fromisoformat(original_added_at).replace(tzinfo=None)
    parsed_restored = datetime.fromisoformat(listing[0]["added_at"]).replace(tzinfo=None)
    assert parsed_restored == parsed_original


# ---------------------------------------------------------------------------
# Import — per-entry skip cases
# ---------------------------------------------------------------------------


def test_import_skips_entry_when_rom_filename_collides(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    export_bytes = client.get("/api/library/export").content

    # Don't delete — re-import should skip the colliding entry.
    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", export_bytes, "application/zip")},
    )
    body = r.json()
    assert body["restored"] == 0
    assert body["skipped"] == 1
    reason = body["entries"][0]["reason"]
    assert "Tetris.gb" in reason


def test_import_skips_entry_when_display_name_collides(tmp_project_root: Path) -> None:
    """Same display name + system_code but different rom_filename in the manifest."""
    client = _client(tmp_project_root)
    _add_library_entry(client, "TetrisA.gb", "GB", "Tetris")

    # Hand-craft a zip whose manifest has a different ROM filename but same display.
    manifest = {
        "version": 1,
        "exported_at": "2026-01-01T00:00:00Z",
        "games": [
            {
                "system_code": "GB",
                "rom_filename": "TetrisB.gb",  # different filename
                "display_name": "Tetris",  # same display
                "size_bytes": 7,
                "added_at": "2026-01-01T00:00:00Z",
                "rom_path": "GB/TetrisB.gb",
                "boxart_path": None,
                "boxart_size_bytes": None,
            }
        ],
    }
    payload = _build_zip(manifest, {"GB/TetrisB.gb": b"PAYLOAD"})

    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", payload, "application/zip")},
    )
    body = r.json()
    assert body["restored"] == 0
    assert body["skipped"] == 1
    assert "Tetris" in body["entries"][0]["reason"]


def test_import_skips_entry_with_unknown_system_code(tmp_project_root: Path) -> None:
    """A manifest from a newer build with a system code we don't recognize
    skips that entry without poisoning the rest of the import."""
    client = _client(tmp_project_root)

    manifest = {
        "version": 1,
        "exported_at": "2026-01-01T00:00:00Z",
        "games": [
            {
                "system_code": "XYZ",
                "rom_filename": "future.bin",
                "display_name": "Future Game",
                "size_bytes": 7,
                "added_at": "2026-01-01T00:00:00Z",
                "rom_path": "XYZ/future.bin",
                "boxart_path": None,
                "boxart_size_bytes": None,
            },
            {
                "system_code": "GB",
                "rom_filename": "ok.gb",
                "display_name": "OK Game",
                "size_bytes": 3,
                "added_at": "2026-01-01T00:00:00Z",
                "rom_path": "GB/ok.gb",
                "boxart_path": None,
                "boxart_size_bytes": None,
            },
        ],
    }
    payload = _build_zip(manifest, {"XYZ/future.bin": b"FUT", "GB/ok.gb": b"OK!"})

    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", payload, "application/zip")},
    )
    body = r.json()
    assert body["restored"] == 1
    assert body["skipped"] == 1
    skipped = [e for e in body["entries"] if e["status"] == "skipped"][0]
    assert "XYZ" in skipped["reason"]


def test_import_rejects_zip_slip_attempt(tmp_project_root: Path) -> None:
    """An entry whose rom_path tries to escape the library dir must be
    rejected without writing anything outside LIBRARY_DIR."""
    from app import paths as _paths

    client = _client(tmp_project_root)
    manifest = {
        "version": 1,
        "exported_at": "2026-01-01T00:00:00Z",
        "games": [
            {
                "system_code": "GB",
                "rom_filename": "evil",
                "display_name": "Evil",
                "size_bytes": 5,
                "added_at": "2026-01-01T00:00:00Z",
                "rom_path": "../escape.bin",
                "boxart_path": None,
                "boxart_size_bytes": None,
            }
        ],
    }
    payload = _build_zip(manifest, {"../escape.bin": b"PWNED"})

    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", payload, "application/zip")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["restored"] == 0
    assert body["skipped"] == 1
    # And nothing landed outside the library dir.
    assert not (_paths.DATA_DIR / "escape.bin").exists()
    assert not (_paths.LIBRARY_DIR.parent / "escape.bin").exists()


def test_import_rejects_absolute_zip_member_path(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    manifest = {
        "version": 1,
        "exported_at": "2026-01-01T00:00:00Z",
        "games": [
            {
                "system_code": "GB",
                "rom_filename": "evil",
                "display_name": "Evil",
                "size_bytes": 5,
                "added_at": "2026-01-01T00:00:00Z",
                "rom_path": "/etc/passwd",
                "boxart_path": None,
                "boxart_size_bytes": None,
            }
        ],
    }
    payload = _build_zip(manifest, {"GB/evil.bin": b"x"})  # rom_path mismatched on purpose

    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", payload, "application/zip")},
    )
    body = r.json()
    assert body["restored"] == 0
    assert body["skipped"] == 1


# ---------------------------------------------------------------------------
# Import — whole-archive failures
# ---------------------------------------------------------------------------


def test_import_400_for_non_zip_upload(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", b"not a zip file", "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "not_a_zip"


def test_import_400_when_manifest_missing(tmp_project_root: Path) -> None:
    """A zip without ``library-manifest.json`` is rejected wholesale."""
    client = _client(tmp_project_root)

    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("GB/ok.gb", b"x")
    buf.seek(0)

    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", buf.read(), "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "missing_manifest"


def test_import_400_on_version_mismatch(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    payload = _build_zip({"version": 99, "exported_at": "x", "games": []}, {})
    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", payload, "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "version_mismatch"


def test_import_400_on_corrupt_manifest_json(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("library-manifest.json", b"{not valid json")
    buf.seek(0)
    r = client.post(
        "/api/library/import",
        files={"file": ("backup.zip", buf.read(), "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "corrupt_manifest"
