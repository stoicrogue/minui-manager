"""Tests for the library store (drafts + confirm + list + delete).

These exercise the service layer directly, no HTTP. The router has its
own test file.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _import_store():
    """Import after tmp_project_root has reloaded the paths module."""
    from app.services import library_store

    return library_store


def test_save_pending_creates_isolated_draft(tmp_project_root: Path) -> None:
    store = _import_store()
    draft = store.save_pending_upload("Tetris.nes", b"abcdef")
    assert draft.file_path.is_file()
    assert draft.file_path.read_bytes() == b"abcdef"
    assert draft.original_filename == "Tetris.nes"
    # Two uploads of the same filename get different draft ids.
    other = store.save_pending_upload("Tetris.nes", b"xyz")
    assert other.draft_id != draft.draft_id


def test_pending_strips_path_components_from_filename(tmp_project_root: Path) -> None:
    """Don't let a malicious filename like '..\\evil.exe' control where the
    file lands. The draft folder is its sandbox; the filename gets reduced
    to its basename.
    """
    store = _import_store()
    draft = store.save_pending_upload(r"..\..\evil.exe", b"x")
    assert draft.original_filename == "evil.exe"
    # File lives under _pending/<id>/ — not somewhere up the tree.
    assert draft.file_path.parent.parent.name == "_pending"


def test_cancel_removes_draft(tmp_project_root: Path) -> None:
    store = _import_store()
    draft = store.save_pending_upload("Tetris.nes", b"x")
    assert store.cancel_draft(draft.draft_id) is True
    assert not draft.file_path.exists()
    # Idempotent on a second call.
    assert store.cancel_draft(draft.draft_id) is False


def test_cleanup_stale_wipes_pending_dir(tmp_project_root: Path) -> None:
    store = _import_store()
    store.save_pending_upload("a.gb", b"x")
    store.save_pending_upload("b.gb", b"x")
    count = store.cleanup_stale_drafts()
    assert count == 2


def test_confirm_moves_file_and_writes_db_row(tmp_project_root: Path) -> None:
    store = _import_store()
    from app.db import session_scope

    draft = store.save_pending_upload("Tetris.nes", b"NES")
    with session_scope() as session:
        row = store.confirm_draft(session, draft.draft_id, "FC", "Tetris")
    assert row.id is not None
    assert row.system_code == "FC"
    assert row.rom_filename == "Tetris.nes"
    assert row.display_name == "Tetris"
    assert row.size_bytes == 3
    # File now lives under <CODE>/, not _pending/.
    assert row.library_path.is_file()
    assert "_pending" not in str(row.library_path)


def test_confirm_rejects_unknown_draft(tmp_project_root: Path) -> None:
    store = _import_store()
    from app.db import session_scope

    with session_scope() as session:
        with pytest.raises(store.LibraryError) as exc:
            store.confirm_draft(session, "nope", "FC", "Tetris")
    assert exc.value.code == "draft_not_found"


def test_confirm_rejects_duplicate_filename_in_same_system(
    tmp_project_root: Path,
) -> None:
    store = _import_store()
    from app.db import session_scope

    d1 = store.save_pending_upload("Tetris.nes", b"x")
    d2 = store.save_pending_upload("Tetris.nes", b"x")  # same filename
    with session_scope() as session:
        store.confirm_draft(session, d1.draft_id, "FC", "Tetris")
    with session_scope() as session:
        with pytest.raises(store.LibraryError) as exc:
            store.confirm_draft(session, d2.draft_id, "FC", "Tetris (Alt)")
    assert exc.value.code == "duplicate_rom"


def test_confirm_rejects_duplicate_display_name(tmp_project_root: Path) -> None:
    store = _import_store()
    from app.db import session_scope

    d1 = store.save_pending_upload("a.nes", b"x")
    d2 = store.save_pending_upload("b.nes", b"x")
    with session_scope() as session:
        store.confirm_draft(session, d1.draft_id, "FC", "Tetris")
    with session_scope() as session:
        with pytest.raises(store.LibraryError) as exc:
            store.confirm_draft(session, d2.draft_id, "FC", "Tetris")
    assert exc.value.code == "duplicate_display_name"


def test_list_filters_by_system(tmp_project_root: Path) -> None:
    store = _import_store()
    from app.db import session_scope

    d_gb = store.save_pending_upload("Kirby.gb", b"x")
    d_fc = store.save_pending_upload("Tetris.nes", b"x")
    with session_scope() as session:
        store.confirm_draft(session, d_gb.draft_id, "GB", "Kirby")
        store.confirm_draft(session, d_fc.draft_id, "FC", "Tetris")
    with session_scope() as session:
        gb_only = store.list_library(session, system_code="GB")
        all_games = store.list_library(session)
    assert {g.rom_filename for g in gb_only} == {"Kirby.gb"}
    assert {g.system_code for g in all_games} == {"GB", "FC"}


def test_delete_removes_row_and_file(tmp_project_root: Path) -> None:
    store = _import_store()
    from app.db import session_scope

    draft = store.save_pending_upload("Tetris.nes", b"x")
    with session_scope() as session:
        row = store.confirm_draft(session, draft.draft_id, "FC", "Tetris")
        row_id = row.id
        rom_path = row.library_path
    assert rom_path.is_file()

    with session_scope() as session:
        ok = store.delete_library_game(session, row_id)
    assert ok is True
    assert not rom_path.exists()
    with session_scope() as session:
        assert store.get_library_game(session, row_id) is None
