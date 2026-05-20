"""Unit tests for the box-art image processor (Phase 5)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.services.image_processor import (
    DEFAULT_SIZE,
    ImageProcessingError,
    process_image,
)


def _png_bytes(size: tuple[int, int], color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpg_bytes(size: tuple[int, int], color: str = "blue") -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _open(out: bytes) -> Image.Image:
    return Image.open(BytesIO(out))


def test_default_output_is_200x300_png() -> None:
    out = process_image(_png_bytes((400, 600)))
    img = _open(out)
    assert img.size == DEFAULT_SIZE == (200, 300)
    assert img.format == "PNG"


def test_jpg_input_is_converted_to_png() -> None:
    out = process_image(_jpg_bytes((300, 450)))
    img = _open(out)
    assert img.format == "PNG"
    assert img.size == (200, 300)


def test_cover_crops_landscape_source() -> None:
    """A 600x300 landscape source under 'cover' to 200x300 must crop the sides,
    leaving the center column intact rather than letterboxing."""
    # Paint left/right red, center column green, so we can verify the center wins.
    src = Image.new("RGB", (600, 300), "red")
    Image.new("RGB", (200, 300), "green").save(
        buf := BytesIO(), format="PNG"
    )
    src.paste(Image.open(buf), (200, 0))
    src_buf = BytesIO()
    src.save(src_buf, format="PNG")

    out = process_image(src_buf.getvalue(), strategy="cover")
    img = _open(out).convert("RGB")
    assert img.size == (200, 300)
    # Every pixel after crop should be green; cover crops the red wings.
    assert img.getpixel((100, 150)) == (0, 128, 0)
    assert img.getpixel((10, 10)) == (0, 128, 0)
    assert img.getpixel((190, 290)) == (0, 128, 0)


def test_contain_letterboxes_landscape_source_with_black() -> None:
    """600x300 source under 'contain' → 200x100 scaled image centered in a
    200x300 black canvas; top + bottom strips must be black."""
    out = process_image(_png_bytes((600, 300), "white"), strategy="contain")
    img = _open(out).convert("RGB")
    assert img.size == (200, 300)
    # Top strip is letterbox (black).
    assert img.getpixel((100, 10)) == (0, 0, 0)
    # Bottom strip is letterbox (black).
    assert img.getpixel((100, 290)) == (0, 0, 0)
    # Center is the actual image (white).
    assert img.getpixel((100, 150)) == (255, 255, 255)


def test_stretch_fills_target_distorting_aspect() -> None:
    out = process_image(_png_bytes((100, 100), "white"), strategy="stretch")
    img = _open(out).convert("RGB")
    assert img.size == (200, 300)
    # Every pixel is the source color since we just stretched it.
    assert img.getpixel((0, 0)) == (255, 255, 255)
    assert img.getpixel((199, 299)) == (255, 255, 255)


def test_custom_target_size_is_honored() -> None:
    out = process_image(_png_bytes((400, 600)), target_size=(100, 150))
    assert _open(out).size == (100, 150)


def test_metadata_is_stripped() -> None:
    """A PNG with a tEXt chunk in the input should not survive in the output."""
    from PIL.PngImagePlugin import PngInfo

    src = Image.new("RGB", (300, 450), "red")
    info = PngInfo()
    info.add_text("Author", "Test Author")
    info.add_text("Comment", "Should be stripped")
    buf = BytesIO()
    src.save(buf, format="PNG", pnginfo=info)

    out = process_image(buf.getvalue())
    out_img = _open(out)
    # PIL exposes PNG text chunks via .text; an empty/missing dict means stripped.
    text = getattr(out_img, "text", {}) or {}
    assert "Author" not in text
    assert "Comment" not in text


def test_empty_input_raises() -> None:
    with pytest.raises(ImageProcessingError):
        process_image(b"")


def test_nonsense_input_raises() -> None:
    with pytest.raises(ImageProcessingError):
        process_image(b"not an image, just words")


def test_invalid_strategy_raises() -> None:
    with pytest.raises(ImageProcessingError):
        process_image(_png_bytes((400, 600)), strategy="bogus")  # type: ignore[arg-type]


def test_zero_target_size_raises() -> None:
    with pytest.raises(ImageProcessingError):
        process_image(_png_bytes((400, 600)), target_size=(0, 300))


def test_output_is_valid_decodable_png() -> None:
    """Sanity: the bytes we return must round-trip through PIL cleanly."""
    out = process_image(_png_bytes((400, 600)))
    img = _open(out)
    img.verify()
