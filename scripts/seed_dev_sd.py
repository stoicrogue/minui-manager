"""Build a fake MinUI Five-Game SD card layout under a target directory.

Useful for developing/testing without a real SD card.

Usage:
    python scripts/seed_dev_sd.py <target-dir>

Layout produced (matches the reference D:\\ card):

    <target>/
    ├── .system/                      (empty marker)
    ├── Emus/miyoomini/GB.pak/         (empty marker)
    ├── Bios/                          (empty)
    ├── Saves/<CODE>/                  (some .sav placeholders)
    ├── Roms/
    │   ├── .res/<game>.png            (PNGs for each game + a few orphans)
    │   ├── Tetris (FC)/
    │   │   ├── Tetris.nes
    │   │   └── Tetris (FC).m3u
    │   ├── Kirby's Dream Land 2 (GB)/...
    │   └── ...
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# Each entry: (display_name, system_code, rom_filename, has_save_m3u_style,
#              has_save_rom_style, malformed_kind)
# malformed_kind: None | "no_m3u" | "empty_m3u" | "missing_rom"
GAMES = [
    ("Tetris", "FC", "Tetris.nes", True, False, None),
    ("Mike Tyson's Punch-Out!!", "FC", "Punch-Out (USA).nes", False, False, None),
    ("Kirby's Dream Land 2", "GB", "Kirby's Dream Land 2 (USA).gb", True, False, None),
    ("Pokemon Unbound", "GBA", "Pokemon Unbound (v2.1.1.1).gba", True, True, None),
    ("F-Zero", "SFC", "F-Zero (USA).sfc", True, False, None),
    ("Thunder Force IV", "MD", "Thunder Force IV (World).md", False, False, None),
    # A malformed entry — folder named correctly, but no .m3u file.
    ("Broken Game", "GB", "broken.gb", False, False, "no_m3u"),
]

ORPHAN_ART = [
    ("Lunar - Silver Star Story", "PS"),
    ("Chrono Trigger", "SFC"),
    ("Advance Wars", "GBA"),
]


def _draw_placeholder_png(path: Path, title: str, code: str) -> None:
    """Draw a 200x300 placeholder PNG with the title + system code."""
    img = Image.new("RGB", (200, 300), color=(40, 20, 80))
    draw = ImageDraw.Draw(img)
    # Use the default PIL font; explicit font files vary by platform.
    try:
        title_font = ImageFont.truetype("arial.ttf", 16)
        code_font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        title_font = ImageFont.load_default()
        code_font = ImageFont.load_default()

    # Title wrapped manually-ish; just truncate for placeholders.
    if len(title) > 18:
        title = title[:17] + "…"
    draw.rectangle([0, 0, 200, 300], outline=(255, 20, 147), width=2)
    draw.text((100, 80), title, fill=(255, 255, 255), anchor="mm", font=title_font)
    draw.text((100, 180), f"({code})", fill=(0, 240, 255), anchor="mm", font=code_font)
    img.save(path, "PNG")


def seed(target: Path, force: bool = False) -> None:
    if target.exists():
        if not force:
            print(
                f"ERROR: target {target} already exists. Pass --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)
        shutil.rmtree(target)

    # MinUI markers
    (target / ".system").mkdir(parents=True)
    (target / "Emus" / "miyoomini" / "GB.pak").mkdir(parents=True)
    (target / "Bios").mkdir(parents=True)

    # Per-system saves dir for every code that has a save below.
    needed_save_dirs: set[str] = set()
    for _name, code, _rom, m3u_save, rom_save, _mal in GAMES:
        if m3u_save or rom_save:
            needed_save_dirs.add(code)
    for code in sorted(needed_save_dirs):
        (target / "Saves" / code).mkdir(parents=True, exist_ok=True)

    # Roms tree
    roms = target / "Roms"
    (roms / ".res").mkdir(parents=True)

    for display, code, rom, save_m3u, save_rom, mal in GAMES:
        folder_name = f"{display} ({code})"
        folder = roms / folder_name
        folder.mkdir()

        if mal != "missing_rom":
            (folder / rom).write_bytes(b"\x00" * 64)  # tiny placeholder

        if mal != "no_m3u":
            m3u_content = "" if mal == "empty_m3u" else f"{rom}\n"
            (folder / f"{folder_name}.m3u").write_text(m3u_content, encoding="utf-8")

        # Box art for everything except the "Broken Game" malformed entry,
        # so we can verify the reader handles each independently.
        if mal != "no_m3u":
            _draw_placeholder_png(roms / ".res" / f"{folder_name}.png", display, code)

        if save_m3u:
            (target / "Saves" / code / f"{folder_name}.m3u.sav").write_bytes(b"\x00" * 16)
        if save_rom:
            (target / "Saves" / code / f"{rom}.sav").write_bytes(b"\x00" * 16)

    # Orphan art — PNGs with no matching game folder.
    for display, code in ORPHAN_ART:
        _draw_placeholder_png(roms / ".res" / f"{display} ({code}).png", display, code)

    print(f"Seeded fake SD layout at {target}")
    print(f"  {len(GAMES)} game folders, {len(ORPHAN_ART)} orphan-art entries")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a fake MinUI SD card layout.")
    parser.add_argument("target", type=Path, help="Directory to populate.")
    parser.add_argument("--force", action="store_true", help="Overwrite if target exists.")
    args = parser.parse_args()
    seed(args.target, force=args.force)


if __name__ == "__main__":
    main()
