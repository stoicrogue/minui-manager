# MinUI Game Manager — Project Plan

A local web application for curating a small, focused game library on a Miyoo Mini Plus running MinUI in the "Five Game Handheld" layout. Solves the "swap out a game for a new one" workflow end-to-end: upload a ROM, auto-detect its system, find and resize box art, write everything to the SD card in the exact layout MinUI expects for the curated-grid view, and remove old games cleanly (archiving the ROM, box art, and save back to the laptop so re-adding is one click).

---

## 1. Context

**Target device:** Miyoo Mini Plus running [MinUI](https://github.com/shauninman/MinUI) (BASE + EXTRAS), configured for the "Five Game Handheld" / Game View layout.
**Display resolution:** 640×480 (480p).
**Reference SD card:** the user's `D:\` drive is a working example of the target layout — use it as the canonical reference.
**Reference guide:** https://retrogamecorps.com/2025/10/24/minui-starter-guide/ (especially the "Box art Easter Egg" and "Five Game Handheld" sections).

**Why this app exists:** The Five Game Handheld layout looks great on the device but is fiddly to manage by hand:

- Each game lives in its own folder named `<Display Name> (CODE)` directly under `Roms/`.
- Each game folder must contain a `.m3u` file with the ROM's filename so saves bind correctly.
- Box art lives in a single shared `Roms/.res/` folder, named exactly after the game folder.
- Images must be PNG, 200×300 pixels.
- The device's save file naming depends on the `.m3u` filename, so getting it slightly wrong silently breaks save persistence.

Doing this manually is tedious and error-prone. The app automates it and treats the SD card as a destination only — the laptop holds the durable library and archive of swapped-out games.

---

## 2. Goals & Non-Goals

### Goals

- Single-user, local tool. Runs on the developer's laptop while the SD card is plugged in.
- Visualize the current state of the SD card (which games are on it, with box art status).
- Upload a ROM through the browser, auto-detect its system (overridable), look up box art, preview/select it, and write everything to the card in the Five Game Handheld layout.
- Remove a game cleanly, **archiving the ROM, `.m3u`, box art, and any save file** back into the project folder so re-adding it later is one click.
- Maintain a small persistent "library" of ROMs the user has uploaded but not currently on the card, so swaps don't require re-uploading.
- No-Surprise mode: nothing is written to the SD card without an explicit confirmation step.

### Non-Goals (initial release)

- Multi-device support (only Miyoo Mini Plus / Five Game layout). Code should not preclude others, but no other layouts ship in v1.
- Managing `Roms_systems/` (the parallel per-system tree). Acknowledged on the card but explicitly out of scope.
- Collections file management (Phase 8, optional).
- BIOS file management (out of scope; user handles manually).
- ROM scraping from external libraries / RetroAchievements / cloud storage.
- Network/SSH transfer to the device (SD card must be plugged into the host).
- SteamGridDB integration (Phase 8 only — libretro-thumbnails is the primary source).
- Multi-user / authentication.

---

## 3. Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│   Angular frontend       │  HTTP   │   FastAPI backend        │
│   (localhost:4200)       │ ◄────► │   (localhost:8000)        │
└──────────────────────────┘         └──────────┬───────────────┘
                                                │
                                  ┌─────────────┴──────────────┐
                                  │                            │
                            ┌─────▼──────┐            ┌────────▼─────────┐
                            │  ./data/   │            │  SD card path    │
                            │ (project)  │            │  (mounted volume)│
                            └────────────┘            └──────────────────┘
```

**Stack:** Python 3.11+, FastAPI, Pillow, httpx, rapidfuzz, pydantic v2, uvicorn. Angular 17+ standalone components + **Angular Material** (decided — no longer TBD).

**Deployment:** `make dev` (or PowerShell equivalent) spins up uvicorn + `ng serve`. A `make run` target builds the Angular bundle and serves it from FastAPI's static files so the whole thing runs on one port for daily use.

**Storage (all project-local, no `~/.minui-manager`):**

```
C:\Projects\minui-manager\
├── data/                        # gitignored
│   ├── library/<CODE>/<filename>          # uploaded ROMs not on the card
│   ├── library/<CODE>/.res/<game-folder>.png   # cached resized box art
│   ├── archive/<CODE>/<game-folder>/<timestamp>/   # removed games (rom + m3u + art + save)
│   ├── app.db                   # SQLite — library metadata, box art cache, sync log
│   ├── config.json              # user settings (SD path, slot cap, etc.)
│   └── sync.log                 # human-readable record of every write to the SD card
```

The SD card itself remains the source of truth for "what's currently on the device" — never duplicate that state into the DB; always read it live.

---

## 4. MinUI Filesystem Contract (Five Game Handheld layout)

This is the part that must be exactly right. Treat these as invariants the backend enforces. The user's `D:\` card is the canonical reference.

### SD card layout

```
<SD_ROOT>/
├── .system/, .userdata/, .tmp_update/    # MinUI internals — left untouched
├── Bios/<CODE>/                          # left untouched
├── Emus/<device>/<CODE>.pak/             # left untouched
├── em_ui.sh, README.txt                  # left untouched
├── Saves/<CODE>/                         # read; archive on remove
│   └── <game-folder>.m3u.sav             # save name = m3u filename + .sav
├── Roms/
│   ├── .res/                             # SINGLE shared folder for ALL box art
│   │   ├── Tetris (FC).png               # named after the game folder
│   │   └── Kirby's Dream Land 2 (GB).png
│   ├── Tetris (FC)/                      # one folder PER GAME
│   │   ├── Tetris.nes                    # ROM file (arbitrary filename)
│   │   └── Tetris (FC).m3u               # contains one line: the ROM filename
│   └── Kirby's Dream Land 2 (GB)/
│       ├── Kirby's Dream Land 2 (USA).gb
│       └── Kirby's Dream Land 2 (GB).m3u
├── Roms_systems/                         # parallel per-system tree — IGNORED by this app
└── Tools_hidden/                         # left untouched
```

### Rules the app must honor

1. **Game folder identity = the `(CODE)` suffix.** The folder name is `<Display Name> (CODE)`. Detection is by the parenthesized code at the end of the folder name.
2. **`.m3u` file is mandatory.** Each game folder must contain `<game-folder>.m3u` (same basename as the folder), and its single line is the ROM's filename relative to the folder. Saves are named `<game-folder>.m3u.sav`, so the m3u basename is load-bearing.
3. **Box art naming:** `<game-folder>.png` (e.g. `Tetris (FC).png`) placed in the **shared** `Roms/.res/` folder at the Roms root. **Do NOT include the ROM file extension** in the image name — this differs from the standard MinUI per-system layout.
4. **Image format:** PNG, **200×300 pixels exactly**. (Verified on the reference card; all four sampled box arts are 200×300.) Resize with letterbox/cover strategy — implementation choice in Phase 5.
5. **Never modify** files outside `Roms/` and `Saves/<CODE>/` (and only Saves on remove/archive). The `.system/`, `.userdata/`, `.tmp_update/`, `Bios/`, `Emus/`, `Tools_hidden/`, `Roms_systems/`, and root files are all off-limits.
6. **SD card validity check:** path exists AND contains `.system/` AND contains `Emus/`. (The standard MinUI `miyoo/` marker is not present on this layout.)
7. **The shared `.res/` may contain orphan art** for games not currently on the card. This is fine — leave orphans alone. The card's `.res/` acts as a local cache for past games. When removing a game, the app moves its art to `./data/archive/` (not into the orphan pile) so the archive is self-contained.
8. **Case-sensitive matching** for emulator codes.

### Supported systems

Derived from the reference card's `Emus/miyoomini/` and `Saves/` directories.

| Code  | Display name                  | Common ROM extensions      |
|-------|-------------------------------|----------------------------|
| FC    | Nintendo Entertainment System | `.nes`, `.fds`             |
| GB    | Game Boy                      | `.gb`                      |
| GBA   | Game Boy Advance              | `.gba`                     |
| GBC   | Game Boy Color                | `.gbc`                     |
| GG    | Sega Game Gear                | `.gg`                      |
| MD    | Sega Genesis                  | `.md`, `.gen`, `.bin`      |
| MGBA  | Game Boy Advance (mGBA core)  | `.gba`                     |
| NGP   | Neo Geo Pocket                | `.ngp`                     |
| NGPC  | Neo Geo Pocket Color          | `.ngc`, `.ngp`             |
| P8    | Pico-8                        | `.p8`, `.png`              |
| PCE   | TurboGrafx-16                 | `.pce`, `.chd`             |
| PKM   | Pokémon mini                  | `.min`                     |
| PS    | Sony PlayStation              | `.chd`, `.pbp`, `.cue`     |
| SFC   | Super Nintendo                | `.sfc`, `.smc`             |
| SGB   | Super Game Boy                | `.gb`, `.gbc`              |
| SMS   | Sega Master System            | `.sms`                     |
| SUPA  | Super Nintendo (alt core)     | `.sfc`, `.smc`             |
| VB    | Virtual Boy                   | `.vb`                      |

Store this as `systems.yaml` so adding/editing systems is data, not code. Each entry also notes whether the code is "preferred" for an ambiguous extension (e.g. `.gba` → GBA preferred over MGBA; `.sfc` → SFC preferred over SUPA) — used by the auto-detector in Phase 3.

---

## 5. Data Model

Pydantic / SQLAlchemy models. Keep flat and simple.

```python
class System:
    code: str               # "GB"
    display_name: str       # "Game Boy"
    extensions: list[str]   # [".gb"]
    libretro_repo: str      # "Nintendo_-_Game_Boy"
    extension_preference: int = 0   # higher wins when extensions overlap (GBA > MGBA, SFC > SUPA)

class LibraryGame:
    id: int
    system_code: str
    rom_filename: str           # "Tetris (World).gb" — actual file on disk
    display_name: str           # "Tetris" — editable, derived from filename by default
    game_folder_name: str       # "Tetris (GB)" — <display_name> (<system_code>); becomes the SD folder name
    size_bytes: int
    library_path: Path          # absolute path under ./data/library/<CODE>/
    boxart_path: Path | None    # 200x300 PNG cached under ./data/library/<CODE>/.res/
    added_at: datetime

class SDCardGame:
    # NOT persisted — built live from filesystem reads
    system_code: str
    game_folder_name: str       # "Tetris (FC)"
    folder_path: Path
    rom_filename: str           # parsed from the .m3u
    m3u_path: Path
    has_boxart: bool
    boxart_path: Path | None
    save_path: Path | None      # Saves/<CODE>/<game-folder>.m3u.sav if present
    matches_library_id: int | None

class ArchivedGame:
    id: int
    system_code: str
    game_folder_name: str
    archived_at: datetime
    archive_path: Path          # ./data/archive/<CODE>/<game-folder>/<timestamp>/
    has_save: bool

class Settings:
    sd_card_path: Path | None
    boxart_target_size: tuple[int, int] = (200, 300)
    boxart_resize_strategy: str = "cover"   # "cover" | "contain" | "stretch"
    max_games_total: int | None = 10        # default slot cap; None disables
    archive_on_remove: bool = True          # always archive rom+m3u+art+save by default
```

Notes:

- No per-system slot cap (removed from the plan — not useful for a curated 10-game device).
- `Settings.max_games_total` defaults to 10 per the latest decision; the user can raise/lower or set to `None`.

---

## 6. Project Structure

```
C:\Projects\minui-manager\
├── README.md
├── pyproject.toml
├── Makefile                    # plus a make.ps1 for Windows
├── minui-game-manager-plan.md  # this file
├── .gitignore                  # ignores data/
├── data/                       # all local state, gitignored
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app + static mount
│   │   ├── config.py           # Settings via pydantic-settings, reads ./data/config.json
│   │   ├── systems.yaml        # System metadata + extension preferences
│   │   ├── db.py               # SQLAlchemy/SQLModel setup, ./data/app.db
│   │   ├── models.py
│   │   ├── routers/
│   │   │   ├── sdcard.py       # GET /api/sdcard/..., POST /api/sdcard/sync, DELETE /api/sdcard/games/...
│   │   │   ├── library.py      # CRUD on uploaded ROMs
│   │   │   ├── boxart.py       # search/fetch/preview (libretro only in v1)
│   │   │   ├── archive.py      # list archived games, restore to library
│   │   │   └── settings.py
│   │   └── services/
│   │       ├── sdcard_reader.py
│   │       ├── sdcard_writer.py        # SafeSDCardWriter
│   │       ├── library_store.py
│   │       ├── archive_store.py
│   │       ├── system_detector.py      # NEW — see Phase 3
│   │       ├── boxart_libretro.py
│   │       └── image_processor.py
│   └── tests/
│       ├── test_sdcard_writer.py       # critical — see Phase 6
│       ├── test_system_detector.py     # critical — see Phase 3
│       └── test_archive_roundtrip.py   # critical — see Phase 7
├── frontend/
│   └── (Angular app, src/app/...)
└── scripts/
    └── seed_dev_sd.py          # builds a fake Five-Game SD layout in a temp dir
```

---

## 7. Phases

Each phase is independently shippable and has a demoable acceptance criterion. Build sequentially; don't start Phase N+1 until Phase N's acceptance criterion is met.

### Phase 1 — Foundation

- Scaffold FastAPI app, Angular app, Makefile, `.gitignore`.
- Settings page in UI for picking the SD card path. Use a directory picker on the frontend.
- **Backend validity check:** path exists AND contains `.system/` subfolder AND contains `Emus/` subfolder. Returns `not_set`, `not_found`, `invalid` (missing markers), or `ok`.
- Persist settings to `./data/config.json`.

**Acceptance:** App boots, user can point it at the real `D:\` card and see `ok`, or at an empty folder and see `invalid`. Settings persist between restarts.

### Phase 2 — Read SD card state

- `GET /api/sdcard/games` — returns all games on the card by scanning `Roms/*/`. For each:
  - Parse `<DisplayName> (CODE)` from folder name. Skip folders without a valid `(CODE)` suffix.
  - Read the `.m3u` to learn the ROM filename. If no `.m3u`, flag as malformed.
  - Verify the ROM file exists in the folder.
  - Check for `Roms/.res/<game-folder>.png` (the shared art folder).
  - Check for `Saves/<CODE>/<game-folder>.m3u.sav`.
- `GET /api/sdcard/orphan-art` — lists `.png` files in `Roms/.res/` that don't correspond to a current game folder (informational).
- Frontend: dashboard showing a grid of game cards (box art thumbnail, display name, system code, save indicator, malformed indicator). Plus a slot-count badge ("3 / 10").
- "Seed dev SD" script builds a realistic Five-Game layout under a temp dir for testing without the real card.

**Acceptance:** Plug in `D:\`; UI shows all 9 current games with correct box art, correct system codes, and save indicators where applicable. Slot count reads `9 / 10`.

### Phase 3 — Library upload (with system auto-detection)

- `POST /api/library` — multipart upload. After upload, the backend:
  1. Runs `system_detector.detect(filename)` (see below) to produce a **detected system code** plus a confidence level.
  2. Stores the file in `./data/library/_pending/` and returns `{library_draft_id, detected_system, confidence, candidates}` to the frontend.
- `POST /api/library/{draft_id}/confirm` — body: `{system_code, display_name}`. Moves the file to `./data/library/<CODE>/<filename>` and creates the DB row. The frontend always shows the dropdown — pre-populated with the detected code — and the display name field — pre-populated with a cleaned-up version of the filename — so the user can override before confirming.
- `GET /api/library` — list, filterable by system.
- `DELETE /api/library/{id}` — removes from library and cached box art.

**System detector logic** (`backend/app/services/system_detector.py`):

1. **Parenthesized code in filename.** Regex-match `\(([A-Z]+)\)` against the filename (case-sensitive). If any captured group matches a known system code from `systems.yaml`, that's a `high` confidence detection. Example: `Pokemon Unbound (GBA).gba` → GBA, high.
2. **Extension match, unambiguous.** If the file extension maps to exactly one system code (e.g. `.nes` → FC, `.sfc` → SFC), that's a `medium` confidence detection. Example: `Tetris.nes` → FC, medium.
3. **Extension match, ambiguous.** If the extension maps to multiple codes (e.g. `.gba` → GBA + MGBA, `.sfc` → SFC + SUPA, `.bin` → MD + PS + PCE), pick the one with the highest `extension_preference` and return it as `low` confidence with the other candidates listed.
4. **No match.** Return `null` detection and the full system list as candidates.

Frontend always shows the dropdown so the user can override regardless of confidence. The dropdown is pre-selected with the detected value; confidence is shown as a small indicator (high/medium/low/unknown) so the user knows when to look twice.

**Tests required:**

- `tests/test_system_detector.py`:
  - `Pokemon Unbound (GBA).gba` → GBA, high.
  - `Pokemon Unbound (MGBA).gba` → MGBA, high (parenthesized override wins over extension preference).
  - `Tetris.nes` → FC, medium.
  - `Game.gba` → GBA, low (preferred over MGBA).
  - `Game.bin` → MD, low (with PS and PCE listed as candidates).
  - `weirdname.xyz` → null, all candidates listed.

**Acceptance:** Upload a `.gb` (auto-fills GB), a `.gba` (auto-fills GBA, low-conf indicator since MGBA is an alternative), and a `.bin` (auto-fills MD, low-conf with PS/PCE alternatives). User can change the dropdown before confirming. Confirmed entries appear in the library list and survive restarts.

### Phase 4 — Box art lookup

Primary (and only, in v1) source: [libretro-thumbnails](https://github.com/libretro-thumbnails). Repo per system, e.g. `libretro-thumbnails/Nintendo_-_Game_Boy/tree/master/Named_Boxarts`. Files are PNG, named by No-Intro convention.

- Service `boxart_libretro.py`:
  - On first request for a system, fetch the repo's `Named_Boxarts` directory listing via the GitHub API (cache the listing for 24h in SQLite).
  - Match library ROM filename to thumbnail filename using `rapidfuzz` (token-set ratio). Return top 5 candidates with scores.
  - Provide a download URL for each candidate (raw.githubusercontent.com).
- `GET /api/boxart/search?library_id={id}` — returns ranked candidates.
- `POST /api/boxart/select` — body: `{library_id, source, source_id}` — downloads the chosen image to a temp file, returns a preview URL.

SteamGridDB is **deferred to Phase 8** — not in v1.

**Acceptance:** For `Tetris (World).gb` in the library, the search returns the correct libretro thumbnail in the top 3 results, the user clicks it, and a full-size preview renders.

### Phase 5 — Image processing

- `image_processor.py`:
  - Resize the chosen image to **200×300 PNG exactly**. Default strategy: `cover` (preserve aspect, crop to fill — best for typical vertical box art).
  - Convert to PNG if the source is JPG.
  - Strip metadata.
  - Save to library cache: `./data/library/<CODE>/.res/<game-folder>.png`. (Note: filename is the **game folder name**, not the ROM filename — this mirrors how it'll be written to the SD card.)
- Allow the user to re-select / replace box art for any library game.
- Settings expose `boxart_resize_strategy` (`cover` / `contain` / `stretch`) for power users.

**Acceptance:** After Phase 4 selection, a 200×300 PNG appears at `./data/library/GB/.res/Tetris (GB).png`, opens correctly, and looks visually correct.

### Phase 6 — Send to SD card  ⚠️ critical, write tests first

This is the only phase where the app modifies the SD card. Tests come first.

- `POST /api/sdcard/sync` — body: `{library_ids: [...]}`. For each:
  1. Verify global slot limit (`max_games_total`) won't be exceeded. If it would, return a conflict response listing current SD games so the user can pick which to remove.
  2. Create `Roms/<game-folder>/` (e.g. `Roms/Tetris (GB)/`).
  3. Copy ROM file into the new folder, preserving filename.
  4. Write `Roms/<game-folder>/<game-folder>.m3u` containing one line: the ROM filename.
  5. Ensure `Roms/.res/` exists.
  6. Copy box art from `./data/library/<CODE>/.res/<game-folder>.png` to `Roms/.res/<game-folder>.png`.
  7. Verify file sizes match (cheap integrity check).
  8. Append to `./data/sync.log`.
- All writes go through a `SafeSDCardWriter` that:
  - Refuses any path that doesn't resolve under the configured SD root.
  - Refuses any path that escapes `Roms/` (no writing to `.system/`, `.userdata/`, `Bios/`, `Emus/`, `Roms_systems/`, `Tools_hidden/`, etc.). The whitelist of writeable subtrees is `Roms/` and (for the remove path in Phase 7) `Saves/<CODE>/` — nothing else.
  - Logs every write to `./data/sync.log`.
- Dry-run mode: `POST /api/sdcard/sync?dry_run=true` returns the exact operations that would be performed, no writes.
- Frontend: "Send to device" flow shows the dry-run preview, requires explicit confirmation, then runs the real sync with a progress indicator.

**Tests required before shipping this phase:**

- `tests/test_sdcard_writer.py`:
  - Path escape attempts (`../../etc/passwd`-style filenames) are rejected.
  - Writing to `.system/`, `.userdata/`, `Bios/`, `Emus/`, `Roms_systems/`, root files are all rejected.
  - Writing succeeds: game folder is created, ROM copied, `.m3u` written with correct contents, art written to shared `.res/` with the **game folder name** (no ROM extension in PNG filename).
  - Slot limit is enforced; sync stops cleanly at the limit.
  - Dry-run produces no FS changes.

**Acceptance:** Run a dry-run, see the planned operations; confirm; verify the SD card now has `Roms/<Display> (CODE)/<rom>` + `Roms/<Display> (CODE)/<Display> (CODE).m3u` + `Roms/.res/<Display> (CODE).png`. Eject the card, put it in the Miyoo Mini Plus, and the new game shows up with its box art in Game View.

### Phase 7 — Remove from SD card (with archive)

- `DELETE /api/sdcard/games/{game_folder_name}` — for the named game:
  1. Find the game folder under `Roms/<game-folder>/`.
  2. Build archive dir: `./data/archive/<CODE>/<game-folder>/<YYYY-MM-DDTHH-MM-SS>/`.
  3. **Move** the entire game folder (`Roms/<game-folder>/`) into the archive.
  4. **Move** the box art (`Roms/.res/<game-folder>.png`) into the archive (alongside the folder contents).
  5. **Move** the save file if present (`Saves/<CODE>/<game-folder>.m3u.sav`) into the archive.
  6. Also scan for legacy-format saves (`Saves/<CODE>/<rom-filename>.sav`) and archive those too — the reference card has both `Pokemon Unbound (v2.1.1.1).gba.sav` and `Pokemon Unbound (GBA).m3u.sav`.
  7. Append a row to the `ArchivedGame` table and to `./data/sync.log`.
- `GET /api/archive` — list archived games with display name, system, archived date, save status.
- `POST /api/archive/{id}/restore-to-library` — move the archived ROM + box art back into `./data/library/<CODE>/` so the user can re-send to the card without re-uploading. Saves are kept in the archive (re-sending writes a fresh card; saves don't auto-restore — that's a separate confirmed action).
- Frontend:
  - Each game on the SD card has a "Remove" button → confirmation dialog showing what will be archived → executes.
  - Library view has a "Restore from archive" sidebar listing recently archived games with one-click restore.
  - Swap shortcut: from the library view, if the slot cap is at capacity, prompt "Remove which game to make room?" listing currently-on-card games, then chain remove + send in one operation.

**Tests required:**

- `tests/test_archive_roundtrip.py`:
  - Remove → archive contains rom, m3u, art, save.
  - Restore-to-library puts ROM + art back; archive row is marked restored but files remain (so re-restore is possible — make it idempotent or mark as moved? **decide in implementation: idempotent restore that re-copies wins**).
  - Both `.m3u.sav` and legacy `.sav` patterns are archived.

**Acceptance:** Remove a game; SD card no longer shows it; `./data/archive/<CODE>/<game-folder>/<timestamp>/` contains the full bundle (rom, m3u, png, save if any); UI shows it in archive list; clicking "Restore" puts the ROM and art back in the library and the user can send to card in one click.

### Phase 8 — Polish (pick & choose)

- **SteamGridDB integration:** add as a secondary box art source behind an API key in settings.
- **Collections support:** read/write `.txt` files in `Roms_systems/`? Actually for Five-Game the device doesn't typically use Collections — defer unless needed.
- **Save archive browser & restore:** UI for browsing/restoring archived saves separately from ROMs.
- **Library export/import:** zip the library directory for backup.
- **Slot-limit UI polish:** visible counter, warning state when approaching capacity, "0 slots left" empty state.
- **Optional Roms_systems/ support:** if the user later wants to manage the per-system tree too, add a second writer mode.
- **Orphan art cleanup:** UI button to clear `Roms/.res/` entries with no matching game folder (since the card already accumulates these).

---

## 8. Key Implementation Notes

### Box art naming gotcha (changed from standard MinUI)

In the Five Game layout the PNG name matches the **game folder name**, NOT the ROM filename + extension. So `Tetris (FC)/Tetris.nes` gets art at `Roms/.res/Tetris (FC).png` — not `Tetris.nes.png`. The `image_processor` service derives the PNG filename from the game folder name throughout.

### `.m3u` contents

A single line: the ROM filename relative to the game folder. No path, no other content. The reference card shows:

```
Tetris (FC)/Tetris (FC).m3u  →  "Tetris.nes"
Kirby's Dream Land 2 (GB)/Kirby's Dream Land 2 (GB).m3u  →  "Kirby's Dream Land 2 (USA).gb"
```

The m3u basename is what saves are bound to (`<m3u-basename>.sav`), so it must equal the game folder name.

### Fuzzy matching strategy (Phase 4)

ROM filenames in libretro-thumbnails follow [No-Intro](https://datomatic.no-intro.org/) naming. User uploads may not match exactly. Strategy:

1. Strip extension.
2. Tokenize on space, parens, brackets, hyphens.
3. Drop tokens that look like region/version codes (`(USA)`, `(Rev A)`, `[!]`, `[T-En]`, etc.) for matching only.
4. Use `rapidfuzz.fuzz.token_set_ratio` against the stripped thumbnail names.
5. Threshold at 75; return top 5 candidates.

Show the user the original thumbnail filenames so they can spot a wrong match.

### Five Game Handheld workflow

This app is agnostic about whether the Game View `.pak` is enabled on the device — it just manages the SD card layout in the per-game form. Document in the README that for the curated five/ten-game experience, the user enables Game View on the device once and lets this app handle ROM/box-art turnover.

### Display name derivation

When deriving a default display name from an uploaded filename: strip the extension, strip parenthesized tags that match the system code or look like region/version (`(USA)`, `(World)`, `(Rev 1)`, `(v1.2.3)`), trim whitespace. Examples:

- `Pokemon Unbound (v2.1.1.1).gba` → `Pokemon Unbound`
- `Kirby's Dream Land 2 (USA).gb` → `Kirby's Dream Land 2`
- `Tetris.nes` → `Tetris`

User can edit before confirming.

### What lives where

- **DB (`./data/app.db`):** library metadata, box art cache hits, archive entries, sync history. Nothing about live SD card contents — always read those fresh.
- **Filesystem (`./data/library/`):** ROM files, resized box art PNGs.
- **Filesystem (`./data/archive/`):** removed-from-card bundles (rom + m3u + art + save) per timestamp.
- **Filesystem (SD card):** treated as a destination, never as a cache for the app's own state.

---

## 9. Open Questions

- **Resize strategy default** — `cover` (crop to fill 200×300) vs `contain` (letterbox). Default `cover` since the reference card's art looks crop-filled; expose the toggle in settings for power users.
- **Orphan art on the reference card** — `Roms/.res/` already has art for ~9 games not currently in `Roms/` (Lunar, Legend of Dragoon, Chrono Trigger, Super Mario World, etc.). Plan ignores these on read and never removes them; user can clean up via Phase 8 polish.
- **Auto-detect SD card insertion** (e.g. via `psutil` polling mounts)? Nice-to-have, not required.
- **Restore-saves flow** — restoring a ROM from archive does NOT auto-restore the save (separate confirmed action in Phase 8). Confirm this is the desired default.
- **Display name in folder vs. file** — Phase 3 derives `game_folder_name` as `<display_name> (<code>)`. If two library games have the same display name + code, append a numeric suffix on the second; flag this case in the UI.

---

## 10. Definition of Done (v1)

- Phases 1–7 complete and demoable.
- Tests pass for `SafeSDCardWriter`, `system_detector`, and `archive_roundtrip`.
- README explains: install, run, point at SD card, upload a ROM, confirm/override detected system, find box art, send to device, remove a game, restore from archive.
- Manually verified end-to-end on the real Miyoo Mini Plus with MinUI in Five Game Handheld mode: uploaded game appears with box art on the device, plays correctly, save persists across reboot, can be removed cleanly, archived bundle on the laptop is complete, and restore-from-archive + re-send works in two clicks.
