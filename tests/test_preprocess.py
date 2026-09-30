from io import BytesIO

import pytest
from PIL import Image

from labelverify.engine.preprocess import UnreadableImageError, prepare_image


def _png(width: int, height: int, exif_orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = BytesIO()
    if exif_orientation:
        exif = image.getexif()
        exif[0x0112] = exif_orientation
        image.save(buffer, format="JPEG", exif=exif.tobytes())
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_large_image_is_downscaled_to_long_edge():
    prepared = prepare_image(_png(4000, 3000))
    assert prepared.media_type == "image/jpeg"
    assert max(prepared.width, prepared.height) == 1500
    assert prepared.width == 1500 and prepared.height == 1125
    assert (prepared.original_width, prepared.original_height) == (4000, 3000)
    assert len(prepared.data) < len(_png(4000, 3000))


def test_small_image_is_not_upscaled():
    prepared = prepare_image(_png(600, 400))
    assert (prepared.width, prepared.height) == (600, 400)


def test_exif_rotation_is_applied():
    # Orientation 6 means the camera was rotated 90 degrees; the stored 800x600 should become 600x800.
    prepared = prepare_image(_png(800, 600, exif_orientation=6))
    assert (prepared.width, prepared.height) == (600, 800)


def test_output_is_a_valid_jpeg(label_png):
    prepared = prepare_image(label_png)
    reopened = Image.open(BytesIO(prepared.data))
    assert reopened.format == "JPEG"


def test_garbage_bytes_raise_a_clear_error():
    with pytest.raises(UnreadableImageError):
        prepare_image(b"definitely not an image")
