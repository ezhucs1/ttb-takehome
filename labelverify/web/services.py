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
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

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
    ActivityRead,
    Application,
    ApplicationStatus,
    ApplicationView,
    Batch,
    BatchItem,
    Comment,
    LabelImage,
    Notice,
    Role,
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
    if app.is_decided:
        raise WorkflowError("This application has been decided; its threads are closed.")
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


# --------------------------------------------------------------------------- unread activity
#
# An inbox item is something the other party did on an application: a comment, a notice,
# or a decision/resubmission. It is unread for a user until one of three things happens:
#   * they click it in the inbox (a per-item read receipt in ``activity_reads``),
#   * they open the application directly from a list (``application_views`` records the
#     moment, and everything on that application before it counts as seen), or
#   * they use "Mark all as read".
# Reaching the application through an inbox link does not consume the other items on it.

# Status changes worth listing. Correction requests and rejections arrive as notices, so
# their status events would be duplicates; a plain submission is queue work, not a message.
FEED_STATUSES = {
    Role.APPLICANT: {ApplicationStatus.APPROVED},
    Role.SPECIALIST: {ApplicationStatus.RESUBMITTED},
}
ITEM_KINDS = ("comment", "notice", "status")


def other_role(user: User) -> str:
    return Role.SPECIALIST if user.role == Role.APPLICANT else Role.APPLICANT


ItemKey = tuple[str, str]  # (kind, item id)


@dataclass
class Unread:
    """Unread items on one application, for the page markers."""

    comment_ids: set[str] = field(default_factory=set)
    fields: dict[str, int] = field(default_factory=dict)  # field -> unread comments
    notice: bool = False
    status: bool = False
    count: int = 0

    def __bool__(self) -> bool:
        return self.count > 0


def _after(created_at: datetime, since: datetime | None) -> bool:
    return since is None or created_at > since


def _seen_map(db: Session, user: User, app_ids: Sequence[str]) -> dict[str, datetime]:
    if not app_ids:
        return {}
    return {
        v.application_id: v.seen_at
        for v in db.scalars(
            select(ApplicationView).where(
                ApplicationView.user_id == user.id, ApplicationView.application_id.in_(app_ids)
            )
        )
    }


def _read_keys(db: Session, user: User) -> set[ItemKey]:
    return {
        (kind, item_id)
        for kind, item_id in db.execute(
            select(ActivityRead.kind, ActivityRead.item_id).where(ActivityRead.user_id == user.id)
        )
    }


def last_seen(db: Session, app: Application, user: User) -> datetime | None:
    view = db.scalar(
        select(ApplicationView).where(
            ApplicationView.user_id == user.id, ApplicationView.application_id == app.id
        )
    )
    return view.seen_at if view else None


def mark_seen(db: Session, app: Application, user: User) -> datetime | None:
    """Record that ``user`` opened ``app`` now; returns when they last did, if ever."""
    view = db.scalar(
        select(ApplicationView).where(
            ApplicationView.user_id == user.id, ApplicationView.application_id == app.id
        )
    )
    previous = view.seen_at if view else None
    if view is None:
        db.add(ApplicationView(user_id=user.id, application_id=app.id, seen_at=utcnow()))
    else:
        view.seen_at = utcnow()
    db.flush()
    return previous


def mark_item_read(db: Session, user: User, kind: str, item_id: str) -> None:
    if kind not in ITEM_KINDS:
        raise WorkflowError("Unknown inbox item.")
    exists = db.scalar(
        select(ActivityRead).where(
            ActivityRead.user_id == user.id,
            ActivityRead.kind == kind,
            ActivityRead.item_id == item_id,
        )
    )
    if exists is None:
        db.add(ActivityRead(user_id=user.id, kind=kind, item_id=item_id))
        db.flush()


def _app_items(app: Application, user: User) -> list[tuple[str, str, datetime, str]]:
    """(kind, id, created_at, field) for every inbox-worthy item on one application."""
    other = other_role(user)
    items = [
        ("comment", c.id, c.created_at, c.field) for c in app.comments if c.author.role == other
    ]
    if user.role == Role.APPLICANT:
        items += [("notice", n.id, n.created_at, "") for n in app.notices]
    watched = FEED_STATUSES[Role(user.role)]
    items += [
        ("status", e.id, e.created_at, "")
        for e in app.events
        if e.actor is not None and e.actor.role == other and e.to_status in watched
    ]
    return items


def unread_for(db: Session, app: Application, user: User, *, consume: bool) -> Unread:
    """Unread items on ``app`` for the page. ``consume`` marks the application opened now."""
    since = mark_seen(db, app, user) if consume else last_seen(db, app, user)
    read = _read_keys(db, user)
    unread = Unread()
    for kind, item_id, created_at, fld in _app_items(app, user):
        if not _after(created_at, since) or (kind, item_id) in read:
            continue
        unread.count += 1
        if kind == "comment":
            unread.comment_ids.add(item_id)
            unread.fields[fld] = unread.fields.get(fld, 0) + 1
        elif kind == "notice":
            unread.notice = True
        else:
            unread.status = True
    return unread


def _scoped_app_ids(db: Session, user: User) -> list[str]:
    """Applications whose activity concerns this user: their own, or every non-draft one."""
    stmt = select(Application.id)
    if user.role == Role.APPLICANT:
        stmt = stmt.where(Application.applicant_id == user.id)
    else:
        stmt = stmt.where(Application.status != ApplicationStatus.DRAFT.value)
    return list(db.scalars(stmt))


def _item_rows(
    db: Session, user: User, app_ids: Sequence[str], *, limit: int | None = None
) -> list[tuple[str, str, str, datetime]]:
    """(kind, id, application id, created_at) across many applications, three queries."""
    if not app_ids:
        return []
    other = other_role(user)
    rows: list[tuple[str, str, str, datetime]] = []
    q = (
        select(Comment.id, Comment.application_id, Comment.created_at)
        .join(User, Comment.author_id == User.id)
        .where(User.role == other, Comment.application_id.in_(app_ids))
        .order_by(Comment.created_at.desc())
    )
    rows += [("comment", *r) for r in db.execute(q.limit(limit) if limit else q)]
    watched = [s.value for s in FEED_STATUSES[Role(user.role)]]
    q = (
        select(StatusEvent.id, StatusEvent.application_id, StatusEvent.created_at)
        .join(User, StatusEvent.actor_id == User.id)
        .where(
            User.role == other,
            StatusEvent.to_status.in_(watched),
            StatusEvent.application_id.in_(app_ids),
        )
        .order_by(StatusEvent.created_at.desc())
    )
    rows += [("status", *r) for r in db.execute(q.limit(limit) if limit else q)]
    if user.role == Role.APPLICANT:
        q = (
            select(Notice.id, Notice.application_id, Notice.created_at)
            .where(Notice.application_id.in_(app_ids))
            .order_by(Notice.created_at.desc())
        )
        rows += [("notice", *r) for r in db.execute(q.limit(limit) if limit else q)]
    return rows


def unread_counts(db: Session, user: User, app_ids: Sequence[str]) -> dict[str, int]:
    """Unread item count per application. Only applications with unread items appear."""
    seen = _seen_map(db, user, app_ids)
    read = _read_keys(db, user)
    counts: dict[str, int] = {}
    for kind, item_id, app_id, created_at in _item_rows(db, user, app_ids):
        if _after(created_at, seen.get(app_id)) and (kind, item_id) not in read:
            counts[app_id] = counts.get(app_id, 0) + 1
    return counts


def unread_total(db: Session, user: User) -> int:
    """Unread items across the user's applications (the sidebar badge)."""
    return sum(unread_counts(db, user, _scoped_app_ids(db, user)).values())


@dataclass
class ActivityItem:
    """One line in the inbox: something the other party did on an application."""

    kind: str  # comment | notice | status
    item_id: str
    app: Application
    created_at: datetime
    actor: User | None
    field: str  # comment field, "general", or "" for notices and decisions
    text: str
    unread: bool

    @property
    def anchor(self) -> str:
        if self.kind == "comment":
            return "thread-host-general" if self.field == "general" else f"field-{self.field}"
        return "notice" if self.kind == "notice" else "history"


def activity_feed(db: Session, user: User, *, limit: int = 40) -> list[ActivityItem]:
    """The other party's recent activity across the user's applications, newest first."""
    ids = _scoped_app_ids(db, user)
    if not ids:
        return []
    seen = _seen_map(db, user, ids)
    read = _read_keys(db, user)

    def is_unread(kind: str, item_id: str, app_id: str, created_at: datetime) -> bool:
        return _after(created_at, seen.get(app_id)) and (kind, item_id) not in read

    items: list[ActivityItem] = []
    other = other_role(user)
    for c in db.scalars(
        select(Comment)
        .join(User, Comment.author_id == User.id)
        .options(selectinload(Comment.application))
        .where(User.role == other, Comment.application_id.in_(ids))
        .order_by(Comment.created_at.desc())
        .limit(limit)
    ).unique():
        items.append(
            ActivityItem(
                "comment",
                c.id,
                c.application,
                c.created_at,
                c.author,
                c.field,
                c.body,
                is_unread("comment", c.id, c.application_id, c.created_at),
            )
        )
    watched = [s.value for s in FEED_STATUSES[Role(user.role)]]
    for e in db.scalars(
        select(StatusEvent)
        .join(User, StatusEvent.actor_id == User.id)
        .options(selectinload(StatusEvent.application))
        .where(
            User.role == other,
            StatusEvent.to_status.in_(watched),
            StatusEvent.application_id.in_(ids),
        )
        .order_by(StatusEvent.created_at.desc())
        .limit(limit)
    ).unique():
        items.append(
            ActivityItem(
                "status",
                e.id,
                e.application,
                e.created_at,
                e.actor,
                "",
                e.note,
                is_unread("status", e.id, e.application_id, e.created_at),
            )
        )
    if user.role == Role.APPLICANT:
        for n in db.scalars(
            select(Notice)
            .options(selectinload(Notice.application))
            .where(Notice.application_id.in_(ids))
            .order_by(Notice.created_at.desc())
            .limit(limit)
        ).unique():
            items.append(
                ActivityItem(
                    "notice",
                    n.id,
                    n.application,
                    n.created_at,
                    n.sent_by,
                    "",
                    n.body,
                    is_unread("notice", n.id, n.application_id, n.created_at),
                )
            )
    items.sort(key=lambda i: i.created_at, reverse=True)
    return items[:limit]


def open_item(db: Session, user: User, kind: str, item_id: str) -> tuple[Application, str]:
    """Mark one inbox item read and return its application and page anchor."""
    model = {"comment": Comment, "notice": Notice, "status": StatusEvent}.get(kind)
    item = db.get(model, item_id) if model else None
    if item is None:
        raise WorkflowError("That inbox item no longer exists.")
    app = item.application
    if user.role == Role.APPLICANT and app.applicant_id != user.id:
        raise WorkflowError("That inbox item no longer exists.")
    mark_item_read(db, user, kind, item_id)
    if kind == "comment":
        anchor = "thread-host-general" if item.field == "general" else f"field-{item.field}"
    else:
        anchor = "notice" if kind == "notice" else "history"
    return app, anchor


def mark_all_seen(db: Session, user: User) -> int:
    """Clear the inbox: every application with unread items counts as opened now."""
    unread_ids = unread_counts(db, user, _scoped_app_ids(db, user))
    now = utcnow()
    existing = {
        v.application_id: v
        for v in db.scalars(
            select(ApplicationView).where(
                ApplicationView.user_id == user.id,
                ApplicationView.application_id.in_(list(unread_ids)),
            )
        )
    }
    for app_id in unread_ids:
        if app_id in existing:
            existing[app_id].seen_at = now
        else:
            db.add(ApplicationView(user_id=user.id, application_id=app_id, seen_at=now))
    db.flush()
    return sum(unread_ids.values())


# --------------------------------------------------------------------------- queue and stats


@dataclass(frozen=True)
class QueueStats:
    open: int
    ready: int
    review: int
    corrections: int
    approved: int


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
        approved=count(Application.status == ApplicationStatus.APPROVED.value),
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
