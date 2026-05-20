# Frontend (pending Node.js install)

The Angular + Material UI is not yet scaffolded because Node.js was not installed
when Phase 1 started. To unblock:

1. Install Node.js 20+ (LTS) from <https://nodejs.org/>.
2. Install the Angular CLI globally:
   ```powershell
   npm install -g @angular/cli@17
   ```
3. From the project root, scaffold the Angular app inside this folder:
   ```powershell
   cd frontend
   ng new minui-manager-ui --standalone --routing --style=scss --skip-git
   cd minui-manager-ui
   ng add @angular/material
   ```
4. Configure the dev server to proxy `/api/*` to the FastAPI backend on :8000
   (see `proxy.conf.json` to be added during Phase 1.5).

Once Node is installed, finish Phase 1 by building the Settings page:

- SD card path picker (text input + status indicator).
- Calls `GET /api/settings`, `PATCH /api/settings`, `GET /api/sdcard/status`.
- Shows the status (`not_set` / `not_found` / `invalid` / `ok`) with a friendly
  message + the list of missing markers if invalid.

Backend API is already live and testable via `http://localhost:8000/docs`.
