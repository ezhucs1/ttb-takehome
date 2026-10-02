"""Applicant and specialist actions: draft, submit, review, decide, resubmit, comments."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ...engine.extractors import Extractor
from ...engine.models import (
    ApplicationData,
    Recommendation,
)
from ...engine.notices import NoticeDraft, draft_notice, flagged_fields
from ..models import (
    Application,
    ApplicationStatus,
    ApplicationView,
    BatchItem,
    Comment,
    Notice,
    StatusEvent,
    User,
    VerificationRun,
)
from .common import (
    FIELD_LABELS,
    WorkflowError,
    _column_values,
    next_serial,
    result_of,
    to_application_data,
    transition,
)
from .runs import PreparedUpload, Upload, attach_images, record_run


def create_draft(
    db: Session,
    applicant: User,
    data: ApplicationData,
    uploads: Sequence[Upload],
    *,
    prepared: Sequence[PreparedUpload] | None = None,
) -> Application:
    app = Application(
        serial=next_serial(db),
        applicant_id=applicant.id,
        organization=applicant.organization,
        status=ApplicationStatus.DRAFT.value,
        **_column_values(data),
    )
    db.add(app)
    db.flush()
    attach_images(db, app, uploads, prepared=prepared)
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


def discard_draft(db: Session, app: Application) -> bool:
    """Delete an unsubmitted draft with everything hanging off it. False if not a draft.

    The runs reference the images and the views reference the application, so they go
    first, explicitly; the ORM's cascade would otherwise delete the images while a run
    still points at one and trip the foreign key.
    """
    if app.status != ApplicationStatus.DRAFT.value:
        return False
    db.execute(delete(VerificationRun).where(VerificationRun.application_id == app.id))
    db.execute(delete(ApplicationView).where(ApplicationView.application_id == app.id))
    db.execute(delete(BatchItem).where(BatchItem.application_id == app.id))
    app.latest_run_id = None
    db.flush()
    db.expire(app, ["runs"])
    db.delete(app)  # images, comments, events and notices cascade
    db.flush()
    return True


def update_fields(app: Application, data: ApplicationData) -> None:
    for key, value in _column_values(data).items():
        setattr(app, key, value)
    if data.beverage_type is not None:
        app.beverage_type_inferred = False  # the applicant chose it


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
    fallback: Extractor | None = None,
) -> VerificationRun:
    if ApplicationStatus(app.status) is not ApplicationStatus.CORRECTION_REQUESTED:
        raise WorkflowError("Only applications with a correction request can be resubmitted.")
    update_fields(app, data)
    if uploads:
        attach_images(db, app, uploads)
    run = record_run(db, app, extractor, "resubmit", fallback=fallback)
    if message.strip():
        add_comment(db, app, actor, "general", message)
    transition(db, app, ApplicationStatus.RESUBMITTED, actor, "Resubmitted with corrections")
    return run


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


def resolve_comment(comment: Comment, resolved: bool = True) -> None:
    comment.resolved = resolved


def comments_by_field(app: Application) -> dict[str, list[Comment]]:
    grouped: dict[str, list[Comment]] = {}
    for c in app.comments:
        grouped.setdefault(c.field, []).append(c)
    return grouped
