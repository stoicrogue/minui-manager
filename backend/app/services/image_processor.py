"""Box-art image processor (Phase 5).

Takes raw image bytes from any source (libretro PNG, user-uploaded JPG,
etc.) and produces a normalized 200x300 PNG with metadata stripped.

The output dimensions and resize strategy are read from user settings
(`boxart_target_width`, `boxart_target_height`, `boxart_resize_strategy`)
so power users can tune the result. Defaults match the MinUI Five Game
Handheld layout contract: 200x300 PNG, ``cover`` (center-crop) — see the
project plan section 4 for the why.

Strategies
----------
- ``cover``    — scale to fill the target box (preserving aspect),
                 center-crop the overflow. Best for typical vertical box art.
- ``contain``  — scale to fit inside the target box (preserving aspect),
                 letterbox with a black background. Chosen over transparent
                 because MinUI's UI is dark and a transparent PNG would show
                 whatever the device renders behind it.
- ``stretch``  — resize directly, distorting aspect. Power-user escape hatch.
"""

from __future__ import annotations

from io import BytesIO
from typing import Literal

from PIL import Image

Strategy = Literal["cover", "contain", "stretch"]

DEFAULT_SIZE: tuple[int, int] = (200, 300)
DEFAULT_STRATEGY: Strategy = "cover"
LETTERBOX_BG: tuple[int, int, int] = (0, 0, 0)


class ImageProcessingError(ValueError):
    """Input bytes weren't a decodable image."""


def process_image(
    raw: bytes,
    target_size: tuple[int, int] = DEFAULT_SIZE,
    strategy: Strategy = DEFAULT_STRATEGY,
) -> bytes:
    """Decode → resize per ``strategy`` → strip metadata → return PNG bytes.

    Raises :class:`ImageProcessingError` if ``raw`` isn't a decodable image.
    """
    if not raw:
        raise ImageProcessingError("Empty image payload.")

    try:
        with Image.open(BytesIO(raw)) as src:
            src.load()
            resized = _resize(src, target_size, strategy)
    except (OSError, ValueError) as exc:
        raise ImageProcessingError(f"Could not decode image: {exc}") from exc

    # Build a fresh image from pixel data so EXIF / iCCP / tEXt chunks
    # don't ride along into the output.
    clean = Image.new(resized.mode, resized.size)
    clean.paste(resized)

    buf = BytesIO()
    clean.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _resize(src: Image.Image, target: tuple[int, int], strategy: Strategy) -> Image.Image:
    target_w, target_h = target
    if target_w <= 0 or target_h <= 0:
        raise ImageProcessingError(f"Target size must be positive, got {target}.")

    if strategy == "stretch":
        return src.convert("RGB").resize(target, Image.Resampling.LANCZOS)

    if strategy == "cover":
        return _resize_cover(src, target_w, target_h)

    if strategy == "contain":
        return _resize_contain(src, target_w, target_h)

    raise ImageProcessingError(f"Unknown resize strategy: {strategy!r}")


def _resize_cover(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale to fully cover the target box, then center-crop the overflow."""
    src = src.convert("RGB")
    src_w, src_h = src.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = max(target_w, round(src_w * scale))
    new_h = max(target_h, round(src_h * scale))
    scaled = src.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return scaled.crop((left, top, left + target_w, top + target_h))


def _resize_contain(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale to fit inside the target, letterbox the remainder with black."""
    src_rgba = src.convert("RGBA") if src.mode in ("RGBA", "LA", "P") else src.convert("RGB")
    src_w, src_h = src_rgba.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    scaled = src_rgba.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_w, target_h), LETTERBOX_BG)
    offset = ((target_w - new_w) // 2, (target_h - new_h) // 2)
    if scaled.mode == "RGBA":
        canvas.paste(scaled, offset, mask=scaled.split()[-1])
    else:
        canvas.paste(scaled, offset)
    return canvas
