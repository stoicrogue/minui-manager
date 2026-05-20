"""Native folder picker.

The browser's directory picker doesn't expose absolute filesystem paths to
JavaScript (by design — sandboxed). For a local-only tool we can instead pop
a native OS dialog from the backend process; the user is on the same machine
the FastAPI server runs on, so this works.

Implemented with tkinter, which ships with Python on Windows. The dialog
runs in a worker thread so it doesn't block the FastAPI event loop.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def open_folder_dialog(initial_dir: Path | None = None) -> str | None:
    """Show a native folder picker. Returns the selected absolute path,
    or None if the user cancelled or the dialog couldn't be shown.

    This function blocks until the user makes a choice; call it from a
    worker thread (see ``pick_folder_async`` in the router).
    """
    try:
        # Imports are local so we don't pay tkinter startup cost on every
        # backend boot, and so missing-Tk environments fail gracefully at
        # call time rather than at import time.
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - depends on host install
        logger.warning("tkinter unavailable: %s", exc)
        return None

    root = tk.Tk()
    try:
        root.withdraw()
        # Bring the dialog to the front; without this, Tk dialogs on Windows
        # tend to appear behind the browser window.
        root.attributes("-topmost", True)
        root.update_idletasks()

        kwargs: dict[str, object] = {
            "title": "Select your MinUI SD card",
            "mustexist": True,
        }
        if initial_dir is not None and initial_dir.exists():
            kwargs["initialdir"] = str(initial_dir)

        selected = filedialog.askdirectory(**kwargs)
    finally:
        try:
            root.destroy()
        except Exception:  # pragma: no cover - cleanup best-effort
            pass

    # askdirectory returns '' on cancel, a path on success.
    if not selected:
        return None
    return str(Path(selected))
