"""Image preparation before extraction.

Phone photos arrive rotated (EXIF orientation), oversized, and sometimes washed out.
Fixing that here keeps extraction fast (fewer image tokens, smaller uploads) and more
accurate regardless of which extractor runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_LONG_EDGE = 1500


def configured_max_edge() -> int:
    """Long-edge size in pixels; LABELVERIFY_IMAGE_MAX_EDGE trades read time for detail."""
    raw = os.environ.get("LABELVERIFY_IMAGE_MAX_EDGE", "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 600 else MAX_LONG_EDGE


JPEG_QUALITY = 85


class UnreadableImageError(ValueError):
    """Raised when the uploaded bytes are not an image we can open."""


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    media_type: str
    width: int
    height: int
    original_width: int
    original_height: int


def prepare_image(data: bytes, *, max_long_edge: int | None = None) -> PreparedImage:
    """Apply EXIF rotation, downscale to ``max_long_edge``, and re-encode as JPEG."""
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnreadableImageError("The file is not a readable image.") from exc

    original_width, original_height = image.size
    image = ImageOps.exif_transpose(image) or image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        # Artwork is often exported on a transparent background; flatten onto white, or
        # black text on a transparent ground becomes black on black.
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    max_long_edge = max_long_edge or configured_max_edge()
    longest = max(image.size)
    if longest > max_long_edge:
        scale = max_long_edge / longest
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return PreparedImage(
        data=buffer.getvalue(),
        media_type="image/jpeg",
        width=image.width,
        height=image.height,
        original_width=original_width,
        original_height=original_height,
    )
