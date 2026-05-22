"""Plan + execute tests for the Phase 6 sync orchestration.

The writer's safety is covered separately in test_sdcard_writer; here we
focus on:
    - plan content (ops are correct, m3u says the right thing)
    - slot-cap conflict shape
    - dry-run produces no FS changes
    - executor isolates per-game failures
    - end-to-end sync via the HTTP router
"""

from __future__ import annotations

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
    with_boxart: bool = False,
) -> dict:
    up = client.post(
        "/api/library/upload",
        files={"files": (filename, b"\x00" * 32, "application/octet-stream")},
    ).json()
    confirmed = client.post(
        f"/api/library/drafts/{up['draft_id']}/confirm",
        json={"system_code": code, "display_name": display},
    ).json()
    if with_boxart:
        # Drop a real PNG straight into the library .res/ cache.
        art_path = Path(confirmed["library_path"]).parent / ".res" / (
            confirmed["game_folder_name"] + ".png"
        )
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_bytes(_png_bytes())
    return confirmed


def _set_sd_card(client: TestClient, sd_path: Path) -> None:
    r = client.patch("/api/settings", json={"sd_card_path": str(sd_path)})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_plan_writes_rom_m3u_and_box_art(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=True)

    r = client.post(
        "/api/sdcard/sync?dry_run=true", json={"library_ids": [g["id"]]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    plan = body["plan"]
    assert plan["new_slot_count"] == 1
    assert plan["current_slot_count"] == 0

    (game,) = plan["games"]
    assert game["game_folder_name"] == "Tetris (GB)"
    assert game["is_replacement"] is False
    assert game["has_boxart"] is True

    actions = [op["action"] for op in game["ops"]]
    # mkdir game folder, copy rom, write m3u, mkdir .res, copy art.
    assert "mkdir" in actions
    assert "copy" in actions
    assert "write_text" in actions
    # m3u destination uses the game folder name (not the ROM name).
    m3u_op = next(o for o in game["ops"] if o["action"] == "write_text")
    assert m3u_op["dest_rel"] == "Roms/Tetris (GB)/Tetris (GB).m3u"
    assert m3u_op["note"] == "Tetris.gb\n"  # m3u content == disc filenames + newline
    # Box-art destination uses game folder name, not the ROM extension.
    art_copy = [
        o for o in game["ops"] if o["action"] == "copy" and "/.res/" in o["dest_rel"]
    ][0]
    assert art_copy["dest_rel"] == "Roms/.res/Tetris (GB).png"


def test_plan_marks_replacement_when_folder_already_on_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """An existing Roms/Tetris (GB)/ folder makes the planner mark this
    sync as a replacement (and adds a remove_tree op up front)."""
    (fake_sd_card / "Roms" / "Tetris (GB)").mkdir(parents=True)
    (fake_sd_card / "Roms" / "Tetris (GB)" / "Tetris (GB).m3u").write_text(
        "old-rom.gb"
    )
    (fake_sd_card / "Roms" / "Tetris (GB)" / "old-rom.gb").write_bytes(b"\x00")

    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=False)

    r = client.post(
        "/api/sdcard/sync?dry_run=true", json={"library_ids": [g["id"]]}
    )
    plan = r.json()["plan"]
    (game,) = plan["games"]
    assert game["is_replacement"] is True
    # Slot count unchanged: one before, one after.
    assert plan["current_slot_count"] == 1
    assert plan["new_slot_count"] == 1
    # First op must be remove_tree.
    assert game["ops"][0]["action"] == "remove_tree"


def test_plan_skips_missing_box_art_with_reason(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=False)

    r = client.post(
        "/api/sdcard/sync?dry_run=true", json={"library_ids": [g["id"]]}
    )
    (game,) = r.json()["plan"]["games"]
    assert game["has_boxart"] is False
    assert "placeholder" in (game["boxart_missing_reason"] or "")
    # No .res copy op.
    assert not any(
        op["action"] == "copy" and "/.res/" in op["dest_rel"] for op in game["ops"]
    )


# ---------------------------------------------------------------------------
# Slot cap conflict
# ---------------------------------------------------------------------------


def test_slot_cap_conflict_returns_409_with_structured_body(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    client.patch("/api/settings", json={"max_games_total": 1})

    # Pre-load the card so it's already at the cap.
    (fake_sd_card / "Roms" / "Existing (GB)").mkdir(parents=True)
    (fake_sd_card / "Roms" / "Existing (GB)" / "Existing (GB).m3u").write_text(
        "existing.gb"
    )
    (fake_sd_card / "Roms" / "Existing (GB)" / "existing.gb").write_bytes(b"\x00")

    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.post(
        "/api/sdcard/sync?dry_run=true", json={"library_ids": [g["id"]]}
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "slot_cap_exceeded"
    assert body["cap"] == 1
    assert body["current_slot_count"] == 1
    assert body["projected_slot_count"] == 2
    assert "Tetris (GB)" in body["new_folder_names"]
    # The frontend needs the current games to render a "remove which?" picker.
    names = [g["game_folder_name"] for g in body["current_games"]]
    assert "Existing (GB)" in names


def test_replacement_within_cap_is_not_a_conflict(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """If we're overwriting an existing slot, the slot count doesn't grow,
    so a cap of 1 doesn't block the sync."""
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    client.patch("/api/settings", json={"max_games_total": 1})

    (fake_sd_card / "Roms" / "Tetris (GB)").mkdir(parents=True)
    (fake_sd_card / "Roms" / "Tetris (GB)" / "Tetris (GB).m3u").write_text("old.gb")
    (fake_sd_card / "Roms" / "Tetris (GB)" / "old.gb").write_bytes(b"\x00")

    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.post(
        "/api/sdcard/sync?dry_run=true", json={"library_ids": [g["id"]]}
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Dry-run vs real
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_filesystem_changes(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=True)

    # Snapshot Roms/ before.
    roms = fake_sd_card / "Roms"
    before = sorted(p.relative_to(fake_sd_card).as_posix() for p in roms.rglob("*"))

    r = client.post(
        "/api/sdcard/sync?dry_run=true", json={"library_ids": [g["id"]]}
    )
    assert r.status_code == 200

    after = sorted(p.relative_to(fake_sd_card).as_posix() for p in roms.rglob("*"))
    assert after == before


def test_real_sync_writes_rom_m3u_and_art_to_card(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris", with_boxart=True)

    r = client.post("/api/sdcard/sync", json={"library_ids": [g["id"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["result"]["ok_count"] == 1
    assert body["result"]["error_count"] == 0

    folder = fake_sd_card / "Roms" / "Tetris (GB)"
    assert folder.is_dir()
    rom = folder / "Tetris.gb"
    m3u = folder / "Tetris (GB).m3u"
    art = fake_sd_card / "Roms" / ".res" / "Tetris (GB).png"

    assert rom.is_file()
    assert m3u.read_text(encoding="utf-8") == "Tetris.gb\n"
    assert art.is_file()
    # Art bytes are the on-disk library PNG.
    assert art.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_real_sync_overwrites_existing_game_folder(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Re-syncing the same game_folder_name replaces the old contents."""
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)

    # Seed the card with a stale rom under Tetris (GB)/.
    folder = fake_sd_card / "Roms" / "Tetris (GB)"
    folder.mkdir(parents=True)
    (folder / "stale.gb").write_bytes(b"OLD")
    (folder / "Tetris (GB).m3u").write_text("stale.gb")

    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.post("/api/sdcard/sync", json={"library_ids": [g["id"]]})
    assert r.status_code == 200, r.text

    # Old rom is gone, new rom is there, m3u points at the new file.
    assert not (folder / "stale.gb").exists()
    assert (folder / "Tetris.gb").is_file()
    assert (folder / "Tetris (GB).m3u").read_text(encoding="utf-8") == "Tetris.gb\n"


def test_sync_returns_207_when_some_games_fail(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    """Per-game failure isolation: a missing source ROM doesn't poison
    the rest of the batch. Status code 207 (Multi-Status)."""
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)

    good = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    bad = _add_library_entry(client, "Mario.gb", "GB", "Mario")
    # Delete the bad ROM from the library so the executor's copy fails.
    # library_path is now a folder; nuke the disc file inside.
    import shutil
    shutil.rmtree(Path(bad["library_path"]))

    r = client.post(
        "/api/sdcard/sync", json={"library_ids": [good["id"], bad["id"]]}
    )
    assert r.status_code == 207
    body = r.json()
    assert body["result"]["ok_count"] == 1
    assert body["result"]["error_count"] == 1

    # Good game still landed.
    assert (fake_sd_card / "Roms" / "Tetris (GB)" / "Tetris.gb").is_file()


def test_sync_400_when_sd_card_not_ready(tmp_project_root: Path) -> None:
    """No SD card configured → 400, not a crash."""
    client = _client(tmp_project_root)
    # SD path is unset by default in fresh fixture.
    g = _add_library_entry(client, "Tetris.gb", "GB", "Tetris")
    r = client.post("/api/sdcard/sync", json={"library_ids": [g["id"]]})
    assert r.status_code == 400


def test_sync_400_for_empty_library_ids(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    r = client.post("/api/sdcard/sync", json={"library_ids": []})
    assert r.status_code == 400


def test_sync_400_for_unknown_library_id(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    client = _client(tmp_project_root)
    _set_sd_card(client, fake_sd_card)
    r = client.post("/api/sdcard/sync", json={"library_ids": [9999]})
    assert r.status_code == 400
