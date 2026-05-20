# MinUI Game Manager

A local web app for curating a small, focused game library on a Miyoo Mini Plus running MinUI in the "Five Game Handheld" / Game View layout.

See [`minui-game-manager-plan.md`](./minui-game-manager-plan.md) for the full plan.

## Status

**Phase 1: Foundation** — backend complete (settings + SD card validity check + tests). Frontend pending Node.js install.

## Setup

### Backend (Python 3.10+)

```powershell
# from project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Frontend (Angular 17+, requires Node 20+)

Not yet scaffolded — install Node.js first, then see `frontend/README.md`.

## Run

### Backend dev server

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --app-dir backend --port 8000
```

API docs at <http://localhost:8000/docs>.

## Test

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

## Layout reference

The target SD-card layout is documented in detail in `minui-game-manager-plan.md` Section 4. Short version: every game lives in its own `Roms/<Display> (CODE)/` folder containing a ROM and a `.m3u` file; all box art lives in a shared `Roms/.res/` folder.
