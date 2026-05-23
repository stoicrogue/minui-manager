# CLAUDE.md

## What this is

Local single-user web app that curates a Miyoo Mini Plus SD card running MinUI in the **Five Game Handheld** (Game View) layout. FastAPI backend serves the API *and* the built Angular UI on `:8000`. The SD card is a destination; the durable library + archive live in `./data/`.

## Commands (Windows-first; use `make.ps1`)

```powershell
.\make.ps1 install      # one-time: .venv + pip install -e ".[dev]" + npm install
.\make.ps1 run          # build frontend if needed, serve on :8000, open browser
.\make.ps1 dev          # backend on :8000 with --reload (pair with `frontend`)
.\make.ps1 backend      # same as dev, no auto-open
.\make.ps1 frontend     # `ng serve` on :4200, proxies /api → :8000
.\make.ps1 build        # `ng build` only (after pulling UI changes)
.\make.ps1 test         # pytest
.\make.ps1 lint         # ruff check backend
.\make.ps1 fmt          # ruff format backend
```

`make.ps1` injects `C:\nodejs` onto PATH — adjust `Use-Node` if Node lives elsewhere. The POSIX `Makefile` exists but isn't the primary path.

## Layout

```
backend/app/
  main.py                    FastAPI app + SPA fallback for Angular routes
  config.py                  Settings model (pydantic) + JSON load/save
  paths.py                   PROJECT_ROOT, DATA_DIR, LIBRARY_DIR, ARCHIVE_DIR, DB_PATH, SYNC_LOG_PATH
  db.py                      SQLAlchemy engine + session_scope() + light ALTER TABLE migrations
  models.py                  LibraryGame, ArchivedGame, LibretroListingCache
  systems.yaml               System registry (code, extensions, libretro_repo, extension_preference)
  routers/                   sdcard, library, boxart, archive, settings
  services/                  sdcard_validator, sdcard_reader, sdcard_writer, sdcard_remover,
                             sdcard_sync, system_registry, system_detector, library_store,
                             archive_store, library_backup, boxart_libretro, boxart_steamgriddb,
                             image_processor, folder_picker
backend/tests/               pytest suite — one module per service / router
frontend/src/app/
  pages/games                "On Card" dashboard + remove-game dialog
  pages/library              library page + upload, boxart-picker, send-to-device, delete-archive dialogs
  pages/settings             settings page
  services/                  typed API clients (one per router)
scripts/seed_dev_sd.py       seed a fake SD card for dev
data/                        gitignored: config.json, app.db, sync.log, library/, archive/
```

`./data/` is the only writable runtime root. Tests redirect it via `MINUI_MANAGER_ROOT`.

## Invariants — touch with care

- **All SD writes go through `SafeSDCardWriter`** (`Roms/` only). All SD removes go through `SafeSDCardRemover` (`Roms/` + `Saves/`). Both reject absolute paths, `..` escapes, NUL bytes, and paths that resolve outside the allow-list. Every mutation appends to `data/sync.log`. Never bypass.
- **On-card layout MinUI requires:**
  ```
  Roms/<Display Name> (CODE)/<rom>
  Roms/<Display Name> (CODE)/<Display Name> (CODE).m3u   # basename == folder name
  Roms/.res/<Display Name> (CODE).png                    # 200×300 PNG, named after FOLDER not ROM
  Saves/<CODE>/<Display Name> (CODE).m3u.sav             # save bound to m3u basename
  ```
  Getting the .m3u basename wrong silently orphans saves. Don't refactor `LibraryGame.game_folder_name` / `.m3u_content` without re-reading the consumers (sync, archive, reader).
- **Library on-disk layout (mirrors the card):**
  ```
  data/library/_pending/<draft_id>/...        # post-upload, pre-confirm
  data/library/<CODE>/<game-folder>/<disc(s)>
  data/library/<CODE>/<game-folder>/<game-folder>.m3u
  data/library/<CODE>/.res/<game-folder>.png
  ```
  `_pending/` drafts are wiped on startup (`cleanup_stale_drafts`). `migrate_legacy_flat_layout` runs on startup to move pre-multi-disk flat ROMs into per-game folders.
- **Multi-disk:** `LibraryGame.disc_filenames` is a JSON list; NULL means single-disk (falls back to `[rom_filename]`). `rom_filename` always = first disc, kept so the unique constraint still gives one row per logical game. Sync regenerates `.m3u` from this list; the uploaded `.m3u` (if any) is discarded.
- **Archive is save-only.** `archive_game` copies just the `.sav` file(s) into `data/archive/<CODE>/<game>/<timestamp>/`; the ROM folder and box art are deleted from the card without backup because the library is the canonical copy of both. If a game had no save, no archive directory is created (the DB row still records the event). Ordering: copy-save-to-archive → commit DB row → delete originals from card. Never reorder — a delete-without-archive is unrecoverable for the only thing the archive is responsible for (the save). `restore_save_to_card` requires the game folder to already be on the card (saves bind to the .m3u basename) and overwrites any existing save with the archived one.
- **SD card status precondition**: every mutating sdcard endpoint goes through `_require_ok_sd_path()` (400 with structured detail if not `ok`).
- **Settings**: `data/config.json`, atomic write (tmp + replace). PATCH is shallow merge with `exclude_unset` semantics.
- **Box-art normalization**: every saved PNG (libretro select, SGDB select, user upload) goes through `image_processor.process_image` → 200×300 PNG, metadata stripped, strategy from settings (`cover`/`contain`/`stretch`). Don't write art bytes directly.
- **System detection priority**: parenthesized `(CODE)` → unambiguous extension → ambiguous extension (highest `extension_preference` wins) → unknown. `_FOLDER_SUFFIX_RE` is case-sensitive on the code.

## DB

SQLite at `data/app.db`. Tables created via `Base.metadata.create_all` on startup. Schema bumps that are simple ADD COLUMN go in `_apply_lightweight_migrations` in `db.py`; anything heavier → Alembic.

Models live in `backend/app/models.py`. Datetime columns are wall-clock UTC; `_iso_utc()` forces the `+00:00` suffix on serialization so the browser doesn't render them as local time.

## Tests

`pytest` config in `pyproject.toml`: `testpaths = ["backend/tests"]`, `pythonpath = ["backend"]`, `asyncio_mode = "auto"`.

`conftest.py` provides a `tmp_project_root` fixture that redirects `data/` and rebuilds the SQLAlchemy engine (`reset_engine_for_tests`). When a test redirects paths, also clear `load_systems.cache_clear()` if it touches the registry.

Network is faked: `boxart_libretro.fetch_listing` and `boxart_steamgriddb.search_game` / `get_grids` are module-level so monkeypatch reaches the routers.

## Frontend notes

Angular 19 + Material (synthwave theme). Standalone components. Routes: `/games`, `/library`, `/settings` (default → `/games`). Box-art `<img>` tags cache-bust by appending `?t=` on refresh — preserve when editing.

CORS allows `localhost:4200` (the dev server proxy). The FastAPI SPA static-file mount falls back to `index.html` for unknown non-`/api/` paths so client-side routing works on hard reload.

## Conventions

- Service functions are sync; routers wrap blocking FS/IO via `asyncio.to_thread`.
- `LibraryError` / `ArchiveError` / `LibraryImportError` carry a `.code` field; routers map them to HTTP status via a `status_map` dict.
- Comments lead with *why*, not *what*. Don't add docstrings just to summarize the signature.
- `ruff` line-length 100, target-version `py310`. Select: `E F I B UP SIM`.
- Path handling: always `Path`, never string concatenation. POSIX-style for relative paths sent to the writer (e.g. `f"Roms/{folder}"`).
