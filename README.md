# MinUI Game Manager

A local web app for curating a small, focused game library on a Miyoo Mini Plus running MinUI in the "Five Game Handheld" / Game View layout.

See [`minui-game-manager-plan.md`](./minui-game-manager-plan.md) for the full plan.

## Status

**Phase 1: Foundation** — backend (settings + SD card validity check + 14 tests passing) and Angular frontend (Settings page with live SD card status) are both in. Acceptance criterion met against the real `D:\` reference card.

## Setup

One-time install (creates `.venv`, installs Python deps + npm deps):

```powershell
.\make.ps1 install
```

> Note: Node.js is expected at `C:\nodejs` (the location it's installed on this machine).
> Adjust the `Use-Node` function in `make.ps1` if your Node lives elsewhere.

## Run

Open two terminals from the project root:

```powershell
# terminal 1 — FastAPI backend on :8000
.\make.ps1 backend

# terminal 2 — Angular dev server on :4200 (proxies /api → :8000)
.\make.ps1 frontend
```

Then open <http://localhost:4200>. Backend OpenAPI docs at <http://localhost:8000/docs>.

## Test

```powershell
.\make.ps1 test          # backend pytest
.\make.ps1 build         # frontend production build (good smoke test)
```

## Layout reference

The target SD-card layout is documented in detail in `minui-game-manager-plan.md` Section 4. Short version: every game lives in its own `Roms/<Display> (CODE)/` folder containing a ROM and a `.m3u` file; all box art lives in a shared `Roms/.res/` folder. The `.system/` and `Emus/` folders at the SD root identify a card as valid for this app.

## Project layout

```
minui-manager/
├── backend/                 FastAPI app + pytest
│   └── app/
│       ├── routers/         sdcard.py, settings.py
│       ├── services/        sdcard_validator.py
│       ├── config.py        Settings model + JSON persistence
│       └── paths.py         project-local paths (./data/)
├── frontend/                Angular 19 + Material (Azure Blue theme)
│   └── src/app/
│       ├── pages/settings/  the Settings page (Phase 1 UI)
│       └── services/        settings.service.ts (typed API client)
├── data/                    user settings, library, archive (gitignored)
└── minui-game-manager-plan.md
```
