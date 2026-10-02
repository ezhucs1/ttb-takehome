"""Shared constants, the status machine, and small helpers the other service modules build on."""

from __future__ import annotations

import os
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engine.models import (
    ApplicationData,
    Verdict,
    VerificationResult,
)
from ..models import (
    Application,
    ApplicationStatus,
    StatusEvent,
    User,
    VerificationRun,
    utcnow,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
BATCH_CONCURRENCY = 5
BATCH_MAX_ROWS = int(os.environ.get("LABELVERIFY_BATCH_MAX_ROWS", "").strip() or 300)

FIELD_LABELS = {
    "brand_name": "Brand Name",
    "class_type": "Class / Type",
    "beverage_type": "Type of Product",
    "alcohol_content": "Alcohol Content",
    "net_contents": "Net Contents",
    "producer_name": "Producer / Bottler Name",
    "producer_address": "Producer / Bottler Address",
    "country_of_origin": "Country of Origin",
    "sulfite_declaration": "Sulfite Declaration",
    "qualifying_phrase": "Qualifying Phrase",
    "age_statement": "Age Statement",
    "bottled_in_bond": "Bottled in Bond",
    "blend_percentage": "Blend Percentage",
    "appellation": "Appellation of Origin",
    "vintage_year": "Vintage Year",
    "estate_bottled": "Estate Bottled",
    "strength_claim": "Statement of Strength",
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


def _column_values(data: ApplicationData) -> dict:
    """ApplicationData as table columns: an unstated type is stored as an empty string."""
    values = data.model_dump()
    values["beverage_type"] = data.beverage_type.value if data.beverage_type else ""
    return values


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
