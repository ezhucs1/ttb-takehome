"""Application lifecycle: create, verify, submit, review, decide, resubmit, batch.

Routes stay thin; every state change lives here so it can be tested without HTTP.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import secrets
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..engine.extractors import ExtractionError, Extractor
from ..engine.models import (
    ApplicationData,
    LabelExtraction,
    Recommendation,
    Verdict,
    VerificationResult,
)
from ..engine.notices import NoticeDraft, draft_notice, flagged_fields
from ..engine.preprocess import UnreadableImageError, prepare_image
from ..engine.verify import run_verification
from .models import (
    Application,
    ApplicationStatus,
    Batch,
    BatchItem,
    Comment,
    LabelImage,
    Notice,
    StatusEvent,
    User,
    VerificationRun,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
BATCH_CONCURRENCY = 5
BATCH_MAX_ROWS = 300

FIELD_LABELS = {
    "brand_name": "Brand Name",
    "class_type": "Class / Type",
    "alcohol_content": "Alcohol Content",
    "net_contents": "Net Contents",
    "producer_name": "Producer / Bottler Name",
    "producer_address": "Producer / Bottler Address",
    "country_of_origin": "Country of Origin",
    "health_warning": "Government Health Warning",
    "general": "General",
}

_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: {ApplicationStatus.SUBMITTED},
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.APPROVED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CORRECTION_REQUESTED,
    },
    ApplicationStatus.RESUBMITTED: {
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.APPROVED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CORRECTION_REQUESTED,
    },
    ApplicationStatus.UNDER_REVIEW: {
        ApplicationStatus.APPROVED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CORRECTION_REQUESTED,
    },
    ApplicationStatus.CORRECTION_REQUESTED: {ApplicationStatus.RESUBMITTED},
    ApplicationStatus.APPROVED: set(),
    ApplicationStatus.REJECTED: set(),
}


class WorkflowError(ValueError):
    """A state change that the workflow does not allow."""


# --------------------------------------------------------------------------- helpers


_SERIAL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def next_serial(db: Session) -> str:
    """Unique, human-readable application number, safe under concurrent batch inserts."""
    year = utcnow().year
    while True:
        candidate = f"COLA-{year}-{''.join(secrets.choice(_SERIAL_ALPHABET) for _ in range(6))}"
        if db.scalar(select(Application.id).where(Application.serial == candidate)) is None:
            return candidate


def to_application_data(app: Application) -> ApplicationData:
    return ApplicationData(**app.application_fields())


def result_of(run: VerificationRun | None) -> VerificationResult | None:
    if run is None or not run.result_json:
        return None
    return VerificationResult.model_validate_json(run.result_json)


def risk_score(result: VerificationResult) -> int:
    score = 0
    for f in result.fields:
        if f.verdict is Verdict.MISMATCH:
            score += 10
        elif f.verdict is Verdict.NEEDS_REVIEW:
            score += 3
    if not result.image_quality.readable:
        score += 20
    return score


def transition(
    db: Session, app: Application, to: ApplicationStatus, actor: User | None, note: str = ""
) -> None:
    current = ApplicationStatus(app.status)
    if to not in _TRANSITIONS[current]:
        raise WorkflowError(f"Cannot move an application from {current.value} to {to.value}.")
    app.events.append(
        StatusEvent(
            application_id=app.id,
            from_status=current.value,
            to_status=to.value,
            actor_id=actor.id if actor else None,
            note=note,
        )
    )
    app.status = to.value
    if to is ApplicationStatus.SUBMITTED:
        app.submitted_at = utcnow()
    if to in (ApplicationStatus.APPROVED, ApplicationStatus.REJECTED):
        app.decided_at = utcnow()


# --------------------------------------------------------------------------- images and runs


def validate_upload(data: bytes) -> None:
    if not data:
        raise UnreadableImageError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnreadableImageError("Image is larger than 10 MB. Please resize it and try again.")


Upload = tuple[bytes, str]  # (raw bytes, filename)
MAX_PANELS = 4


def attach_images(db: Session, app: Application, uploads: Sequence[Upload]) -> list[LabelImage]:
    """Store a new label set (front, back, neck ...) as the next version."""
    if not uploads:
        raise UnreadableImageError("Choose at least one label image.")
    if len(uploads) > MAX_PANELS:
        raise UnreadableImageError(f"Upload at most {MAX_PANELS} images per label set.")
    for data, _ in uploads:
        validate_upload(data)
    prepared = [
        (prepare_image(data), filename) for data, filename in uploads
    ]  # validates all first
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


def extract_for_prefill(extractor: Extractor, images: Sequence[LabelImage]) -> LabelExtraction:
    """Read fields from stored (already prepared) label images."""
    return extractor.extract_panels(panels_of(images))


def prefill_fields(extraction: LabelExtraction) -> dict[str, str]:
    return {
        "brand_name": extraction.brand_name.value or "",
        "class_type": extraction.class_type.value or "",
        "alcohol_content": extraction.alcohol_content.value or "",
        "net_contents": extraction.net_contents.value or "",
        "producer_name": extraction.producer_name.value or "",
        "producer_address": extraction.producer_address.value or "",
        "country_of_origin": extraction.country_of_origin.value or "",
    }


def record_run(
    db: Session,
    app: Application,
    extractor: Extractor,
    trigger: str,
) -> VerificationRun:
    """Run verification on the application's current label set and store the result."""
    images = app.current_images
    if not images:
        raise WorkflowError("This application has no label image.")
    image = images[0]
    try:
        result = run_verification(
            [img.data for img in images],
            to_application_data(app),
            extractor=extractor,
            prepare=False,
            media_type=image.media_type,
        )
    except ExtractionError as exc:
        run = VerificationRun(
            application_id=app.id,
            image_id=image.id,
            image_version=image.version,
            trigger=trigger,
            extractor=extractor.name,
            recommendation="error",
            result_json="",
            error=str(exc),
        )
        app.runs.append(run)
        db.flush()
        return run
    run = VerificationRun(
        application_id=app.id,
        image_id=image.id,
        image_version=image.version,
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
    return run


# --------------------------------------------------------------------------- applicant actions


def create_draft(
    db: Session, applicant: User, data: ApplicationData, uploads: Sequence[Upload]
) -> Application:
    app = Application(
        serial=next_serial(db),
        applicant_id=applicant.id,
        organization=applicant.organization,
        status=ApplicationStatus.DRAFT.value,
        **data.model_dump(),
    )
    db.add(app)
    db.flush()
    attach_images(db, app, uploads)
    app.events.append(
        StatusEvent(
            application_id=app.id,
            from_status=None,
            to_status=ApplicationStatus.DRAFT.value,
            actor_id=applicant.id,
            note="Draft created",
        )
    )
    return app


def update_fields(app: Application, data: ApplicationData) -> None:
    for key, value in data.model_dump().items():
        setattr(app, key, value)


def submit(db: Session, app: Application, actor: User) -> None:
    transition(db, app, ApplicationStatus.SUBMITTED, actor, "Submitted for review")


def resubmit(
    db: Session,
    app: Application,
    actor: User,
    data: ApplicationData,
    extractor: Extractor,
    *,
    uploads: Sequence[Upload] = (),
    message: str = "",
) -> VerificationRun:
    if ApplicationStatus(app.status) is not ApplicationStatus.CORRECTION_REQUESTED:
        raise WorkflowError("Only applications with a correction request can be resubmitted.")
    update_fields(app, data)
    if uploads:
        attach_images(db, app, uploads)
    run = record_run(db, app, extractor, "resubmit")
    if message.strip():
        add_comment(db, app, actor, "general", message)
    transition(db, app, ApplicationStatus.RESUBMITTED, actor, "Resubmitted with corrections")
    return run


# --------------------------------------------------------------------------- specialist actions


def claim_for_review(db: Session, app: Application, specialist: User) -> bool:
    """Move a freshly submitted application under review by this specialist."""
    if ApplicationStatus(app.status) in (
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.RESUBMITTED,
    ):
        app.specialist_id = specialist.id
        transition(
            db,
            app,
            ApplicationStatus.UNDER_REVIEW,
            specialist,
            f"Review started by {specialist.name}",
        )
        return True
    return False


def draft_correction(app: Application, *, use_ai: bool | None = None) -> NoticeDraft:
    result = result_of(app.latest_run)
    if result is None:
        raise WorkflowError("Run a verification before drafting a notice.")
    return draft_notice(
        to_application_data(app),
        result,
        serial=app.serial,
        applicant_org=app.organization,
        use_ai=use_ai,
    )


def decide(
    db: Session,
    app: Application,
    specialist: User,
    action: str,
    *,
    notice_body: str = "",
    notice_source: str = "template",
    note: str = "",
) -> None:
    if action == "approve":
        app.specialist_id = specialist.id
        transition(db, app, ApplicationStatus.APPROVED, specialist, note or "Label approved")
    elif action == "reject":
        app.specialist_id = specialist.id
        transition(db, app, ApplicationStatus.REJECTED, specialist, note or "Application rejected")
        if notice_body.strip():
            app.notices.append(
                Notice(
                    application_id=app.id,
                    body=notice_body.strip(),
                    drafted_by=notice_source,
                    sent_by_id=specialist.id,
                )
            )
    elif action == "request_correction":
        if not notice_body.strip():
            raise WorkflowError("A correction request needs a notice for the applicant.")
        app.specialist_id = specialist.id
        app.notices.append(
            Notice(
                application_id=app.id,
                body=notice_body.strip(),
                drafted_by=notice_source,
                sent_by_id=specialist.id,
            )
        )
        result = result_of(app.latest_run)
        if result is not None:
            for f in flagged_fields(result):
                add_comment(db, app, specialist, f.field, f.reason)
        transition(
            db,
            app,
            ApplicationStatus.CORRECTION_REQUESTED,
            specialist,
            note or "Correction requested",
        )
    else:
        raise WorkflowError(f"Unknown decision '{action}'.")


def bulk_approve(db: Session, specialist: User, ids: list[str]) -> int:
    approved = 0
    for app_id in ids:
        app = db.get(Application, app_id)
        if app is None or not app.is_open or app.recommendation != Recommendation.APPROVE.value:
            continue
        decide(db, app, specialist, "approve", note="Approved in bulk (all fields matched)")
        approved += 1
    return approved


# --------------------------------------------------------------------------- comments


def add_comment(db: Session, app: Application, author: User, field: str, body: str) -> Comment:
    if field not in FIELD_LABELS:
        raise WorkflowError("Unknown field.")
    if not body.strip():
        raise WorkflowError("Comment cannot be empty.")
    comment = Comment(application_id=app.id, field=field, author_id=author.id, body=body.strip())
    app.comments.append(comment)
    db.flush()
    return comment


def resolve_comment(db: Session, comment: Comment, resolved: bool = True) -> None:
    comment.resolved = resolved


def comments_by_field(app: Application) -> dict[str, list[Comment]]:
    grouped: dict[str, list[Comment]] = {}
    for c in app.comments:
        grouped.setdefault(c.field, []).append(c)
    return grouped


# --------------------------------------------------------------------------- queue and stats


@dataclass(frozen=True)
class QueueStats:
    open: int
    ready: int
    review: int
    corrections: int
    approved_today: int


QUEUE_TABS = {
    "open": "All open",
    "ready": "Ready to approve",
    "review": "Needs a look",
    "corrections": "Awaiting applicant",
    "decided": "Decided",
}


def queue(db: Session, tab: str = "open") -> list[Application]:
    stmt = select(Application)
    if tab == "ready":
        stmt = stmt.where(
            Application.status.in_(
                [
                    s.value
                    for s in (
                        ApplicationStatus.SUBMITTED,
                        ApplicationStatus.UNDER_REVIEW,
                        ApplicationStatus.RESUBMITTED,
                    )
                ]
            ),
            Application.recommendation == Recommendation.APPROVE.value,
        )
    elif tab == "review":
        stmt = stmt.where(
            Application.status.in_(
                [
                    s.value
                    for s in (
                        ApplicationStatus.SUBMITTED,
                        ApplicationStatus.UNDER_REVIEW,
                        ApplicationStatus.RESUBMITTED,
                    )
                ]
            ),
            Application.recommendation != Recommendation.APPROVE.value,
        )
    elif tab == "corrections":
        stmt = stmt.where(Application.status == ApplicationStatus.CORRECTION_REQUESTED.value)
    elif tab == "decided":
        stmt = stmt.where(
            Application.status.in_(
                [ApplicationStatus.APPROVED.value, ApplicationStatus.REJECTED.value]
            )
        )
        return list(db.scalars(stmt.order_by(Application.decided_at.desc())).unique())
    else:
        stmt = stmt.where(
            Application.status.in_(
                [
                    s.value
                    for s in (
                        ApplicationStatus.SUBMITTED,
                        ApplicationStatus.UNDER_REVIEW,
                        ApplicationStatus.RESUBMITTED,
                    )
                ]
            )
        )
    return list(
        db.scalars(
            stmt.order_by(Application.risk_score.asc(), Application.submitted_at.asc())
        ).unique()
    )


def queue_stats(db: Session) -> QueueStats:
    open_statuses = [
        s.value
        for s in (
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.UNDER_REVIEW,
            ApplicationStatus.RESUBMITTED,
        )
    ]

    def count(*conditions) -> int:
        return db.scalar(select(func.count()).select_from(Application).where(*conditions)) or 0

    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return QueueStats(
        open=count(Application.status.in_(open_statuses)),
        ready=count(
            Application.status.in_(open_statuses),
            Application.recommendation == Recommendation.APPROVE.value,
        ),
        review=count(
            Application.status.in_(open_statuses),
            Application.recommendation != Recommendation.APPROVE.value,
        ),
        corrections=count(Application.status == ApplicationStatus.CORRECTION_REQUESTED.value),
        approved_today=count(
            Application.status == ApplicationStatus.APPROVED.value, Application.decided_at >= today
        ),
    )


def applicant_applications(db: Session, applicant: User) -> list[Application]:
    stmt = (
        select(Application)
        .where(Application.applicant_id == applicant.id)
        .order_by(Application.updated_at.desc())
    )
    return list(db.scalars(stmt).unique())


# --------------------------------------------------------------------------- batch


BATCH_COLUMNS = [
    "image",
    "beverage_type",
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "producer_name",
    "producer_address",
    "is_import",
    "country_of_origin",
]


def split_image_names(cell: str) -> list[str]:
    """The image column holds one file name, or several separated by ';' or '|'."""
    return [n.strip() for n in re.split(r"[;|]", cell or "") if n.strip()]


def batch_template_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(BATCH_COLUMNS)
    writer.writerow(
        [
            "old-tom-bourbon.png",
            "distilled_spirits",
            "OLD TOM DISTILLERY",
            "Kentucky Straight Bourbon Whiskey",
            "45% Alc./Vol. (90 Proof)",
            "750 mL",
            "Old Tom Distillery",
            "Bardstown, KY 40004",
            "false",
            "",
        ]
    )
    return buffer.getvalue()


@dataclass
class ParsedBatch:
    rows: list[dict]
    images: dict[str, bytes]
    errors: list[str]


def parse_batch(csv_bytes: bytes, zip_bytes: bytes) -> ParsedBatch:
    errors: list[str] = []
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ParsedBatch([], {}, ["The CSV must be UTF-8 encoded."])
    reader = csv.DictReader(io.StringIO(text))
    missing = [
        c
        for c in ("image", "beverage_type", "brand_name", "class_type")
        if c not in (reader.fieldnames or [])
    ]
    if missing:
        return ParsedBatch([], {}, [f"CSV is missing required column(s): {', '.join(missing)}."])
    rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    if not rows:
        errors.append("The CSV has no data rows.")
    if len(rows) > BATCH_MAX_ROWS:
        errors.append(f"The CSV has {len(rows)} rows; the limit is {BATCH_MAX_ROWS} per batch.")

    images: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.rsplit("/", 1)[-1]
                if name.startswith(".") or not name.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".webp")
                ):
                    continue
                images[name] = zf.read(info)
    except zipfile.BadZipFile:
        errors.append("The images file is not a valid .zip archive.")
    return ParsedBatch(rows, images, errors)


def create_batch(db: Session, applicant: User, filename: str, parsed: ParsedBatch) -> Batch:
    batch = Batch(applicant_id=applicant.id, filename=filename, total=len(parsed.rows))
    db.add(batch)
    db.flush()
    for n, row in enumerate(parsed.rows, start=1):
        db.add(
            BatchItem(
                batch_id=batch.id,
                row_number=n,
                brand_name=row.get("brand_name", ""),
                image_name=row.get("image", ""),
            )
        )
    db.flush()
    return batch


def process_batch(
    session_factory: sessionmaker, batch_id: str, parsed: ParsedBatch, extractor: Extractor
) -> None:
    """Verify every row with bounded concurrency. Each row uses its own session."""
    with session_factory() as db:
        batch = db.get(Batch, batch_id)
        applicant = db.get(User, batch.applicant_id)
        items = [(item.id, item.row_number) for item in batch.items]

    def work(item_id: str, row_number: int) -> None:
        row = parsed.rows[row_number - 1]
        ok = False
        with session_factory() as db:
            item = db.get(BatchItem, item_id)
            try:
                names = split_image_names(row.get("image", ""))
                if not names:
                    raise WorkflowError("The image column is empty.")
                uploads = []
                for name in names:
                    image = parsed.images.get(name)
                    if image is None:
                        raise WorkflowError(f"Image '{name}' was not found in the zip.")
                    uploads.append((image, name))
                data = ApplicationData(
                    beverage_type=row.get("beverage_type", ""),
                    brand_name=row.get("brand_name", ""),
                    class_type=row.get("class_type", ""),
                    alcohol_content=row.get("alcohol_content", ""),
                    net_contents=row.get("net_contents", ""),
                    producer_name=row.get("producer_name", ""),
                    producer_address=row.get("producer_address", ""),
                    is_import=row.get("is_import", "").lower() in ("true", "yes", "1", "y"),
                    country_of_origin=row.get("country_of_origin", ""),
                )
                app = create_draft(db, applicant, data, uploads)
                app.batch_id = batch_id
                record_run(db, app, extractor, "batch")
                submit(db, app, applicant)
                item.application_id = app.id
                item.status = "done"
                ok = True
            except Exception as exc:  # one bad row must not sink the batch
                log.exception("batch row %s failed", row_number)
                db.rollback()
                item = db.get(BatchItem, item_id)
                item.status = "error"
                item.error = str(exc)
            db.commit()
        with session_factory() as db:  # atomic counter so concurrent rows never lose an update
            column = Batch.completed if ok else Batch.failed
            db.execute(update(Batch).where(Batch.id == batch_id).values({column.key: column + 1}))
            db.commit()

    with ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY) as pool:
        list(pool.map(lambda pair: work(*pair), items))

    with session_factory() as db:
        batch = db.get(Batch, batch_id)
        batch.status = "done"
        batch.finished_at = utcnow()
        db.commit()


def batch_summary(batch: Batch) -> dict[str, int]:
    counts = {"approve": 0, "needs_review": 0, "request_correction": 0, "error": 0}
    for item in batch.items:
        if item.status == "error" or item.application is None:
            counts["error"] += 1
        else:
            counts[item.application.recommendation or "error"] = (
                counts.get(item.application.recommendation or "error", 0) + 1
            )
    return counts


def serialize_result_for_ui(result: VerificationResult | None) -> str:
    return json.dumps(result.model_dump(mode="json")) if result else "null"
