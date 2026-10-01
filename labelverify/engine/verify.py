"""End-to-end verification of a label image set against one application."""

from __future__ import annotations

import time
from collections.abc import Sequence

from .compare import verify
from .extractors import ExtractionError, Extractor, TesseractExtractor, get_extractor
from .extractors.base import Panel
from .models import ApplicationData, LabelExtraction, VerificationResult
from .preprocess import prepare_image


def run_verification(
    images: bytes | Sequence[bytes],
    application: ApplicationData,
    *,
    extractor: Extractor | None = None,
    fallback: Extractor | None = None,
    prepare: bool = True,
    media_type: str = "image/jpeg",
) -> VerificationResult:
    """Prepare the image(s), extract fields, compare against the application, and time it.

    ``images`` is one image or several panels of the same label set (front, back, neck).
    When ``fallback`` is given and the primary extractor fails (timeout, network,
    refusal), the fallback runs instead and the result records which one produced it.
    """
    started = time.perf_counter()
    extractor = extractor or get_extractor()
    raw_panels: list[bytes] = [images] if isinstance(images, bytes | bytearray) else list(images)
    if not raw_panels:
        raise ExtractionError("No label images were provided.")

    panels: list[Panel] = []
    for raw in raw_panels:
        if prepare:
            prepared = prepare_image(raw)
            panels.append((prepared.data, prepared.media_type))
        else:
            panels.append((bytes(raw), media_type))

    extraction_started = time.perf_counter()
    extraction, used = _extract_with_fallback(extractor, fallback, panels)
    extraction_ms = int((time.perf_counter() - extraction_started) * 1000)

    result = verify(application, extraction)
    result.extraction = extraction
    result.extractor = used
    result.extraction_ms = extraction_ms
    result.total_ms = int((time.perf_counter() - started) * 1000)
    return result


def _extract_with_fallback(
    primary: Extractor, fallback: Extractor | None, panels: list[Panel]
) -> tuple[LabelExtraction, str]:
    try:
        return primary.extract_panels(panels), primary.name
    except ExtractionError:
        if fallback is None:
            raise
        extraction = fallback.extract_panels(panels)
        return extraction, f"{fallback.name} (fallback after {primary.name} failed)"


def default_fallback() -> Extractor:
    return TesseractExtractor()
