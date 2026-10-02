"""Label images and verification runs: upload validation, prefill, caching reads, storing results."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...engine.compare import resolve_beverage_type
from ...engine.compare import verify as compare_verify
from ...engine.extractors import ExtractionError, Extractor
from ...engine.models import (
    ApplicationData,
    LabelExtraction,
    VerificationResult,
)
from ...engine.preprocess import PreparedImage, UnreadableImageError, prepare_image
from ...engine.verify import run_verification
from ..models import (
    Application,
    LabelImage,
    VerificationRun,
)
from .common import MAX_UPLOAD_BYTES, WorkflowError, risk_score, to_application_data

log = logging.getLogger(__name__)


def validate_upload(data: bytes) -> None:
    if not data:
        raise UnreadableImageError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnreadableImageError("Image is larger than 10 MB. Please resize it and try again.")


Upload = tuple[bytes, str]  # (raw bytes, filename)
MAX_PANELS = 4


PreparedUpload = tuple[PreparedImage, str]  # (prepared image, filename)


def prepare_uploads(uploads: Sequence[Upload]) -> list[PreparedUpload]:
    """Validate and preprocess a label set. Pure CPU work, so callers that hold a
    database transaction (SQLite has one writer) can do it beforehand."""
    if not uploads:
        raise UnreadableImageError("Choose at least one label image.")
    if len(uploads) > MAX_PANELS:
        raise UnreadableImageError(f"Upload at most {MAX_PANELS} images per label set.")
    for data, _ in uploads:
        validate_upload(data)
    return [(prepare_image(data), filename) for data, filename in uploads]


def attach_images(
    db: Session,
    app: Application,
    uploads: Sequence[Upload],
    *,
    prepared: Sequence[PreparedUpload] | None = None,
) -> list[LabelImage]:
    """Store a new label set (front, back, neck ...) as the next version."""
    if prepared is None:
        prepared = prepare_uploads(uploads)
    version = app.current_version + 1
    images = []
    for panel, (image, filename) in enumerate(prepared, start=1):
        record = LabelImage(
            application_id=app.id,
            filename=filename or f"label-{panel}",
            media_type=image.media_type,
            data=image.data,
            width=image.width,
            height=image.height,
            version=version,
            panel=panel,
        )
        app.images.append(record)
        images.append(record)
    db.flush()
    return images


def panels_of(images: Sequence[LabelImage]) -> list[tuple[bytes, str]]:
    return [(img.data, img.media_type) for img in images]


def extract_for_prefill(
    extractor: Extractor, images: Sequence[LabelImage], *, fallback: Extractor | None = None
) -> tuple[LabelExtraction, str, str | None]:
    """Read fields from stored (already prepared) label images.

    Returns the read, the name of the reader that produced it, and, when the configured
    reader failed and the fallback read instead, the failure it recovered from.
    """
    panels = panels_of(images)
    try:
        return extractor.extract_panels(panels), extractor.name, None
    except ExtractionError as exc:
        if fallback is None:
            raise
        log.warning("%s failed (%s); reading with %s", extractor.name, exc, fallback.name)
        extraction = fallback.extract_panels(panels)
        return extraction, f"{fallback.name} (fallback after {extractor.name} failed)", str(exc)


def fallback_note(fallback_name: str, failure: str) -> str:
    """What the applicant is told when a read came from the fallback reader."""
    return (
        f"The vision model was unavailable ({failure}), so the label was read with "
        f"{EXTRACTOR_NOTE_NAMES.get(fallback_name, fallback_name)}, which is less accurate. "
        "The form below is filled from that read: correct every field to match your "
        "application before checking, or use Read again once the model is back."
    )


EXTRACTOR_NOTE_NAMES = {"tesseract": "local OCR (Tesseract)"}


def prefill_fields(extraction: LabelExtraction) -> dict[str, str]:
    """Form values read off the label, including the class it belongs to."""
    detected, _ = resolve_beverage_type(
        ApplicationData(beverage_type=None, brand_name="", class_type=""), extraction
    )
    return {
        "beverage_type": detected.value if detected else "",
        "brand_name": extraction.brand_name.value or "",
        "class_type": extraction.class_type.value or "",
        "alcohol_content": extraction.alcohol_content.value or "",
        "net_contents": extraction.net_contents.value or "",
        "producer_name": extraction.producer_name.value or "",
        "producer_address": extraction.producer_address.value or "",
        "country_of_origin": extraction.country_of_origin.value or "",
    }


# Statements the reader fills in for step 2 (the applicant files them as printed).
# Checklist field -> attribute of the read.
LABEL_ONLY_ITEMS = {
    "qualifying_phrase": "qualifying_phrase",
    "sulfite_declaration": "sulfite_declaration",
    "age_statement": "age_statement",
    "bottled_in_bond": "bottled_in_bond_claim",
    "blend_percentage": "blend_percentage",
    "appellation": "appellation",
    "vintage_year": "vintage_year",
    "estate_bottled": "estate_bottled_claim",
    "strength_claim": "strength_claim",
}


def label_carries(extraction: LabelExtraction) -> dict[str, str | None]:
    """What the read found for every label statement, to prefill step 2 so the applicant can see
    it in step 2 before running the check; None when nothing was printed."""
    found = {
        item: (getattr(extraction, attr).value or "").strip() or None
        for item, attr in LABEL_ONLY_ITEMS.items()
    }
    warning = extraction.health_warning
    found["health_warning"] = (warning.text or "").strip() or None if warning.present else None
    return found


@dataclass(frozen=True)
class LabelSet:
    """The bytes of an application's current label set, detached from any session, so a
    model call can run with no database transaction open (SQLite has one writer)."""

    image_id: str
    image_version: int
    media_type: str
    panels: tuple[bytes, ...]
    data: ApplicationData
    cached_extraction: LabelExtraction | None = None  # an earlier read of these images
    cached_ms: int = 0
    cached_extractor: str = ""


def cached_extraction(app: Application) -> LabelExtraction | None:
    """The stored read of the current label set, if one exists for this image version."""
    if not app.extraction_json or app.extraction_version != app.current_version:
        return None
    try:
        return LabelExtraction.model_validate_json(app.extraction_json)
    except ValueError:
        return None


def remember_extraction(
    app: Application, extraction: LabelExtraction, extractor_name: str, elapsed_ms: int
) -> None:
    """Keep a read of the current images so later comparisons need no second model call."""
    app.extraction_json = extraction.model_dump_json()
    app.extraction_version = app.current_version
    app.extraction_ms = elapsed_ms
    app.extraction_extractor = extractor_name


def label_set_of(app: Application, *, reuse: bool = True) -> LabelSet:
    images = app.current_images
    if not images:
        raise WorkflowError("This application has no label image.")
    first = images[0]
    cached = cached_extraction(app) if reuse else None
    return LabelSet(
        image_id=first.id,
        image_version=first.version,
        media_type=first.media_type,
        panels=tuple(img.data for img in images),
        data=to_application_data(app),
        cached_extraction=cached,
        cached_ms=app.extraction_ms if cached else 0,
        cached_extractor=app.extraction_extractor if cached else "",
    )


def verify_label_set(
    label_set: LabelSet, extractor: Extractor, *, fallback: Extractor | None = None
) -> tuple[VerificationResult | None, str | None]:
    """The comparison, with a model call only when no earlier read of these images exists.
    Returns (result, None) or (None, error message)."""
    if label_set.cached_extraction is not None:
        started = time.perf_counter()
        result = compare_verify(label_set.data, label_set.cached_extraction)
        result.extraction = label_set.cached_extraction
        result.extractor = label_set.cached_extractor or extractor.name
        result.extraction_ms = label_set.cached_ms
        result.reused_read = True
        result.total_ms = int((time.perf_counter() - started) * 1000)
        return result, None
    try:
        return (
            run_verification(
                list(label_set.panels),
                label_set.data,
                extractor=extractor,
                fallback=fallback,
                prepare=False,
                media_type=label_set.media_type,
            ),
            None,
        )
    except ExtractionError as exc:
        return None, str(exc)


def store_run(
    db: Session,
    app: Application,
    label_set: LabelSet,
    extractor_name: str,
    trigger: str,
    result: VerificationResult | None,
    error: str | None,
) -> VerificationRun:
    """Persist the outcome of ``verify_label_set`` on the application."""
    if result is None:
        run = VerificationRun(
            application_id=app.id,
            image_id=label_set.image_id,
            image_version=label_set.image_version,
            trigger=trigger,
            extractor=extractor_name,
            recommendation="error",
            result_json="",
            error=error or "The label could not be read.",
        )
        app.runs.append(run)
        db.flush()
        app.latest_run_id = run.id
        app.recommendation = "error"  # listed under "Needs a look", never under "Ready"
        app.risk_score = 20
        return run
    run = VerificationRun(
        application_id=app.id,
        image_id=label_set.image_id,
        image_version=label_set.image_version,
        trigger=trigger,
        extractor=result.extractor,
        recommendation=result.recommendation.value,
        result_json=result.model_dump_json(),
        extraction_ms=result.extraction_ms,
        total_ms=result.total_ms,
    )
    app.runs.append(run)
    db.flush()
    app.latest_run_id = run.id
    app.recommendation = run.recommendation
    app.risk_score = risk_score(result)
    if not app.beverage_type and result.beverage_type is not None:
        # The applicant left the type unstated; keep what the label said so the record,
        # the queue, and later comparisons all use the same class.
        app.beverage_type = result.beverage_type.value
        app.beverage_type_inferred = True
    if result.extraction is not None and not result.reused_read:
        remember_extraction(app, result.extraction, result.extractor, result.extraction_ms)
    return run


def record_run(
    db: Session,
    app: Application,
    extractor: Extractor,
    trigger: str,
    *,
    fresh: bool = False,
    fallback: Extractor | None = None,
) -> VerificationRun:
    """Compare the application against its current label set and store the result.

    The images are read by the model once per upload: the read made when the applicant
    uploaded them is reused by the pre-check and the submission. ``fresh`` forces a new
    read, which is what the specialist's "Re-check label" does. The batch worker splits
    the same steps across two short transactions so a model call never holds the
    database's write lock.
    """
    label_set = label_set_of(app, reuse=not fresh)
    result, error = verify_label_set(label_set, extractor, fallback=fallback)
    return store_run(db, app, label_set, extractor.name, trigger, result, error)
