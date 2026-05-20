"""Tests for config load/save and settings API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_load_returns_defaults_when_no_config(tmp_project_root: Path) -> None:
    from app.config import load_settings

    s = load_settings()
    assert s.sd_card_path is None
    assert s.max_games_total == 10
    assert s.boxart_target_width == 200
    assert s.boxart_target_height == 300
    assert s.boxart_resize_strategy == "cover"
    assert s.archive_on_remove is True


def test_save_then_load_roundtrip(tmp_project_root: Path) -> None:
    from app.config import Settings, load_settings, save_settings

    s = Settings(sd_card_path=Path(r"D:\\"), max_games_total=5)
    save_settings(s)

    loaded = load_settings()
    assert loaded.sd_card_path == Path(r"D:\\")
    assert loaded.max_games_total == 5


def test_corrupt_config_falls_back_to_defaults(tmp_project_root: Path) -> None:
    from app.config import load_settings
    from app.paths import CONFIG_PATH, ensure_data_dirs

    ensure_data_dirs()
    CONFIG_PATH.write_text("{not valid json", encoding="utf-8")

    s = load_settings()
    # Should not raise; should return defaults.
    assert s.sd_card_path is None
    assert s.max_games_total == 10


def test_settings_api_get_and_patch(tmp_project_root: Path, fake_sd_card: Path) -> None:
    # Import app AFTER tmp_project_root has reloaded the paths module.
    from app.main import app

    client = TestClient(app)

    # GET defaults
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["sd_card_path"] is None
    assert body["max_games_total"] == 10

    # PATCH the SD path
    r = client.patch("/api/settings", json={"sd_card_path": str(fake_sd_card)})
    assert r.status_code == 200
    assert r.json()["sd_card_path"] == str(fake_sd_card)

    # GET reflects the new value
    r = client.get("/api/settings")
    assert r.json()["sd_card_path"] == str(fake_sd_card)


def test_settings_api_partial_update_preserves_other_fields(
    tmp_project_root: Path, fake_sd_card: Path
) -> None:
    from app.main import app

    client = TestClient(app)

    client.patch("/api/settings", json={"sd_card_path": str(fake_sd_card)})
    client.patch("/api/settings", json={"max_games_total": 7})

    r = client.get("/api/settings")
    body = r.json()
    assert body["sd_card_path"] == str(fake_sd_card)
    assert body["max_games_total"] == 7


def test_sdcard_status_endpoint_reports_each_state(
    tmp_project_root: Path, fake_sd_card: Path, tmp_path: Path
) -> None:
    from app.main import app

    client = TestClient(app)

    # not_set: no path
    assert client.get("/api/sdcard/status").json()["status"] == "not_set"

    # not_found: nonexistent path
    bogus = str(tmp_path / "does_not_exist")
    client.patch("/api/settings", json={"sd_card_path": bogus})
    assert client.get("/api/sdcard/status").json()["status"] == "not_found"

    # invalid: existing dir without markers
    bare = tmp_path / "bare"
    bare.mkdir()
    client.patch("/api/settings", json={"sd_card_path": str(bare)})
    body = client.get("/api/sdcard/status").json()
    assert body["status"] == "invalid"
    assert ".system" in body["missing_markers"]
    assert "Emus" in body["missing_markers"]

    # ok: fake SD card
    client.patch("/api/settings", json={"sd_card_path": str(fake_sd_card)})
    assert client.get("/api/sdcard/status").json()["status"] == "ok"


def test_config_file_is_human_readable_json(tmp_project_root: Path, fake_sd_card: Path) -> None:
    from app.config import load_settings, save_settings
    from app.paths import CONFIG_PATH

    s = load_settings()
    s_with_path = s.model_copy(update={"sd_card_path": fake_sd_card})
    save_settings(s_with_path)

    raw = CONFIG_PATH.read_text(encoding="utf-8")
    # Should be pretty-printed and parseable.
    parsed = json.loads(raw)
    assert parsed["sd_card_path"] == str(fake_sd_card)
    assert "\n" in raw  # indented
