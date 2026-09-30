"""End-to-end verification of one label image against one application."""

from __future__ import annotations

import time

from .compare import verify
from .extractors import ExtractionError, Extractor, TesseractExtractor, get_extractor
from .models import ApplicationData, LabelExtraction, VerificationResult
from .preprocess import PreparedImage, prepare_image


def run_verification(
    image: bytes,
    application: ApplicationData,
    *,
    extractor: Extractor | None = None,
    fallback: Extractor | None = None,
    prepare: bool = True,
) -> VerificationResult:
    """Prepare the image, extract fields, compare against the application, and time it.

    When ``fallback`` is given and the primary extractor fails (timeout, network,
    refusal), the fallback runs instead and the result records which one produced it.
    """
    started = time.perf_counter()
    extractor = extractor or get_extractor()

    prepared: PreparedImage | None = prepare_image(image) if prepare else None
    payload = prepared.data if prepared else image
    media_type = prepared.media_type if prepared else "image/jpeg"

    extraction_started = time.perf_counter()
    extraction, used = _extract_with_fallback(extractor, fallback, payload, media_type)
    extraction_ms = int((time.perf_counter() - extraction_started) * 1000)

    result = verify(application, extraction)
    result.extractor = used
    result.extraction_ms = extraction_ms
    result.total_ms = int((time.perf_counter() - started) * 1000)
    return result


def _extract_with_fallback(
    primary: Extractor, fallback: Extractor | None, payload: bytes, media_type: str
) -> tuple[LabelExtraction, str]:
    try:
        return primary.extract(payload, media_type), primary.name
    except ExtractionError:
        if fallback is None:
            raise
        extraction = fallback.extract(payload, media_type)
        return extraction, f"{fallback.name} (fallback after {primary.name} failed)"


def default_fallback() -> Extractor:
    return TesseractExtractor()
