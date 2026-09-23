"""Native folder picker.

The browser's directory picker doesn't expose absolute filesystem paths to
JavaScript (by design — sandboxed). For a local-only tool we can instead pop
a native OS dialog from the backend process; the user is on the same machine
the FastAPI server runs on, so this works.

The Tk dialog runs in a separate Python process rather than a worker thread.
Tk/Xlib aren't thread-safe: driving them from a non-main thread of the server
trips an Xlib assertion (``_XReply``) on Linux/X11 and aborts the whole
backend, freezing the desktop while the half-drawn dialog hangs. A child
process gets its own main thread and X connection, and if Tk dies there the
server survives.

On Linux, zenity/kdialog are preferred when installed: Tk 8.6 can abort even
on its own main thread under some Xwayland compositors (e.g. Hyprland).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Runs in the child. Prints the chosen path (or nothing on cancel) to stdout.
_DIALOG_SCRIPT = """
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
# Without -topmost, Tk dialogs on Windows tend to appear behind the browser.
root.attributes("-topmost", True)
root.update_idletasks()
kwargs = {"title": "Select your MinUI SD card", "mustexist": True}
if len(sys.argv) > 1:
    kwargs["initialdir"] = sys.argv[1]
selected = filedialog.askdirectory(**kwargs)
root.destroy()
sys.stdout.write(selected or "")
"""


_TITLE = "Select your MinUI SD card"


def _dialog_command(initial_dir: Path | None) -> list[str] | None:
    start = str(initial_dir) if initial_dir is not None and initial_dir.exists() else None
    if sys.platform.startswith("linux"):
        if zenity := shutil.which("zenity"):
            cmd = [zenity, "--file-selection", "--directory", f"--title={_TITLE}"]
            if start:
                # Trailing slash makes zenity open *inside* the dir, not select it.
                cmd.append(f"--filename={start.rstrip('/')}/")
            return cmd
        if kdialog := shutil.which("kdialog"):
            return [kdialog, "--title", _TITLE, "--getexistingdirectory", start or str(Path.home())]
        if os.environ.get("WAYLAND_DISPLAY"):
            # Tk under Xwayland aborts and has been seen to wedge the compositor;
            # better to show nothing and let the user type the path.
            logger.warning("no folder picker on Wayland: install zenity or kdialog")
            return None
    cmd = [sys.executable, "-c", _DIALOG_SCRIPT]
    if start:
        cmd.append(start)
    return cmd


def open_folder_dialog(initial_dir: Path | None = None) -> str | None:
    """Show a native folder picker. Returns the selected absolute path,
    or None if the user cancelled or the dialog couldn't be shown.

    Blocks until the user makes a choice; call it from a worker thread.
    """
    args = _dialog_command(initial_dir)
    if args is None:
        return None

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as exc:  # pragma: no cover - depends on host install
        logger.warning("folder picker could not start: %s", exc)
        return None

    if result.returncode != 0:
        # zenity/kdialog exit 1 on cancel; anything else is a real failure.
        if result.returncode != 1:
            logger.warning(
                "folder picker exited with %s: %s", result.returncode, result.stderr.strip()
            )
        return None

    selected = result.stdout.strip()
    if not selected:
        return None
    return str(Path(selected))
