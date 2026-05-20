"""HTTP-level tests for the library router."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_project_root: Path) -> TestClient:
    from app.main import app

    return TestClient(app)


def test_upload_returns_draft_and_detection(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    r = client.post(
        "/api/library/upload",
        files={"file": ("Pokemon Unbound (GBA).gba", b"\x00" * 32, "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["original_filename"] == "Pokemon Unbound (GBA).gba"
    assert body["size_bytes"] == 32
    det = body["detection"]
    assert det["detected_code"] == "GBA"
    assert det["confidence"] == "high"
    assert det["suggested_display_name"] == "Pokemon Unbound"


def test_upload_rejects_empty_file(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    r = client.post(
        "/api/library/upload",
        files={"file": ("empty.gba", b"", "application/octet-stream")},
    )
    assert r.status_code == 400


def test_confirm_creates_library_entry(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    upload = client.post(
        "/api/library/upload",
        files={"file": ("Tetris.nes", b"\x00" * 16, "application/octet-stream")},
    ).json()
    draft_id = upload["draft_id"]

    r = client.post(
        f"/api/library/drafts/{draft_id}/confirm",
        json={"system_code": "FC", "display_name": "Tetris"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["system_code"] == "FC"
    assert body["display_name"] == "Tetris"
    assert body["game_folder_name"] == "Tetris (FC)"
    assert body["id"] is not None


def test_confirm_rejects_unknown_system_code(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    upload = client.post(
        "/api/library/upload",
        files={"file": ("x.nes", b"\x00", "application/octet-stream")},
    ).json()
    r = client.post(
        f"/api/library/drafts/{upload['draft_id']}/confirm",
        json={"system_code": "ZZZ", "display_name": "Mystery"},
    )
    assert r.status_code == 400


def test_confirm_409_on_duplicate(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    upload = client.post(
        "/api/library/upload",
        files={"file": ("Tetris.nes", b"\x00", "application/octet-stream")},
    ).json()
    client.post(
        f"/api/library/drafts/{upload['draft_id']}/confirm",
        json={"system_code": "FC", "display_name": "Tetris"},
    )

    # Second upload with the same filename.
    upload2 = client.post(
        "/api/library/upload",
        files={"file": ("Tetris.nes", b"\x00", "application/octet-stream")},
    ).json()
    r = client.post(
        f"/api/library/drafts/{upload2['draft_id']}/confirm",
        json={"system_code": "FC", "display_name": "Tetris (Alt name)"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "duplicate_rom"


def test_cancel_draft_removes_it(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    upload = client.post(
        "/api/library/upload",
        files={"file": ("x.gb", b"\x00", "application/octet-stream")},
    ).json()
    draft_id = upload["draft_id"]
    r = client.delete(f"/api/library/drafts/{draft_id}")
    assert r.status_code == 200
    # Confirming a cancelled draft should fail.
    r2 = client.post(
        f"/api/library/drafts/{draft_id}/confirm",
        json={"system_code": "GB", "display_name": "X"},
    )
    assert r2.status_code == 404


def test_list_library_returns_added_games(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    for fn, code, name in [
        ("Tetris.nes", "FC", "Tetris"),
        ("Kirby.gb", "GB", "Kirby"),
    ]:
        u = client.post(
            "/api/library/upload",
            files={"file": (fn, b"\x00", "application/octet-stream")},
        ).json()
        client.post(
            f"/api/library/drafts/{u['draft_id']}/confirm",
            json={"system_code": code, "display_name": name},
        )

    r = client.get("/api/library")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    codes = {g["system_code"] for g in body["games"]}
    assert codes == {"FC", "GB"}

    # Filter by system
    r = client.get("/api/library", params={"system_code": "FC"})
    body = r.json()
    assert body["total"] == 1
    assert body["games"][0]["display_name"] == "Tetris"


def test_delete_library_entry(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    u = client.post(
        "/api/library/upload",
        files={"file": ("Tetris.nes", b"\x00", "application/octet-stream")},
    ).json()
    created = client.post(
        f"/api/library/drafts/{u['draft_id']}/confirm",
        json={"system_code": "FC", "display_name": "Tetris"},
    ).json()

    r = client.delete(f"/api/library/{created['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    r2 = client.get("/api/library")
    assert r2.json()["total"] == 0


def test_get_draft_info_recovers_detection(tmp_project_root: Path) -> None:
    client = _client(tmp_project_root)
    upload = client.post(
        "/api/library/upload",
        files={"file": ("Pokemon Unbound (GBA).gba", b"\x00", "application/octet-stream")},
    ).json()
    draft_id = upload["draft_id"]

    r = client.get(f"/api/library/drafts/{draft_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["original_filename"] == "Pokemon Unbound (GBA).gba"
    assert body["detection"]["detected_code"] == "GBA"
