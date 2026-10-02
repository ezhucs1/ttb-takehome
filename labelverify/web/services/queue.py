"""The specialist queue, batch rollups, and dashboard stats."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ...engine.models import (
    Recommendation,
)
from ..models import (
    OPEN_STATUSES,
    Application,
    ApplicationStatus,
    Batch,
    BatchItem,
    User,
)


@dataclass(frozen=True)
class QueueStats:
    open: int
    ready: int
    review: int
    corrections: int
    approved: int


QUEUE_TABS = {
    "open": "All open",
    "ready": "Ready",
    "review": "Needs a look",
    "corrections": "Awaiting applicant",
    "approved": "Approved",
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
    elif tab == "approved":
        stmt = stmt.where(Application.status == ApplicationStatus.APPROVED.value)
        return list(
            db.scalars(
                stmt.where(Application.batch_id.is_(None)).order_by(Application.decided_at.desc())
            ).unique()
        )
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
            stmt.where(Application.batch_id.is_(None)).order_by(Application.submitted_at.desc())
        ).unique()
    )


def _tab_filter(tab: str):
    """The queue tab's criteria as a filter on Application, shared with the batch views."""
    open_values = [s.value for s in OPEN_STATUSES]
    if tab == "ready":
        return [
            Application.status.in_(open_values),
            Application.recommendation == Recommendation.APPROVE.value,
        ]
    if tab == "review":
        return [
            Application.status.in_(open_values),
            Application.recommendation != Recommendation.APPROVE.value,
        ]
    if tab == "corrections":
        return [Application.status == ApplicationStatus.CORRECTION_REQUESTED.value]
    if tab == "approved":
        return [Application.status == ApplicationStatus.APPROVED.value]
    return [Application.status.in_(open_values)]


@dataclass(frozen=True)
class BatchRollup:
    """A batch as the specialist sees it in the queue: whose, and how its rows stand."""

    batch: Batch
    open: int
    ready: int
    review: int
    corrections: int
    approved: int
    rejected: int
    failed: int
    matching: int  # rows that meet the current tab

    @property
    def decided(self) -> int:
        return self.approved + self.rejected + self.corrections


def batch_rollups(db: Session, tab: str = "open") -> list[BatchRollup]:
    """Every batch with at least one row meeting the tab, newest first."""
    batches = list(
        db.scalars(
            select(Batch).order_by(Batch.created_at.desc()).options(selectinload(Batch.items))
        )
    )
    if not batches:
        return []
    rows = db.execute(
        select(Application.batch_id, Application.status, Application.recommendation, func.count())
        .where(Application.batch_id.in_([b.id for b in batches]))
        .group_by(Application.batch_id, Application.status, Application.recommendation)
    ).all()
    matching = dict(
        db.execute(
            select(Application.batch_id, func.count())
            .where(Application.batch_id.in_([b.id for b in batches]), *_tab_filter(tab))
            .group_by(Application.batch_id)
        ).all()
    )
    per: dict[str, dict[str, int]] = {}
    open_values = {s.value for s in OPEN_STATUSES}
    for batch_id, status, recommendation, n in rows:
        c = per.setdefault(
            batch_id,
            {"open": 0, "ready": 0, "review": 0, "corrections": 0, "approved": 0, "rejected": 0},
        )
        if status in open_values:
            c["open"] += n
            c["ready" if recommendation == Recommendation.APPROVE.value else "review"] += n
        elif status == ApplicationStatus.CORRECTION_REQUESTED.value:
            c["corrections"] += n
        elif status == ApplicationStatus.APPROVED.value:
            c["approved"] += n
        elif status == ApplicationStatus.REJECTED.value:
            c["rejected"] += n
    result = []
    for batch in batches:
        c = per.get(batch.id, {})
        found = matching.get(batch.id, 0)
        if not found:
            continue
        result.append(
            BatchRollup(
                batch=batch,
                open=c.get("open", 0),
                ready=c.get("ready", 0),
                review=c.get("review", 0),
                corrections=c.get("corrections", 0),
                approved=c.get("approved", 0),
                rejected=c.get("rejected", 0),
                failed=batch.failed,
                matching=found,
            )
        )
    return result


def batch_rollup(db: Session, batch: Batch, tab: str = "open") -> BatchRollup:
    """One batch's rollup, whatever the tab's match count."""
    for rollup in batch_rollups(db, "all"):
        if rollup.batch.id == batch.id:
            matching = db.scalar(
                select(func.count()).where(Application.batch_id == batch.id, *_tab_filter(tab))
            )
            return BatchRollup(**{**rollup.__dict__, "matching": matching or 0})
    return BatchRollup(batch, 0, 0, 0, 0, 0, 0, batch.failed, 0)


def batch_items_for(batch: Batch, tab: str) -> list[BatchItem]:
    """The batch's rows that meet the tab, in row order; "all" keeps every row."""
    if tab == "all":
        return list(batch.items)
    open_values = {s.value for s in OPEN_STATUSES}

    def meets(app: Application | None) -> bool:
        if app is None:
            return False
        if tab == "ready":
            return app.status in open_values and app.recommendation == Recommendation.APPROVE.value
        if tab == "review":
            return app.status in open_values and app.recommendation != Recommendation.APPROVE.value
        if tab == "corrections":
            return app.status == ApplicationStatus.CORRECTION_REQUESTED.value
        if tab == "approved":
            return app.status == ApplicationStatus.APPROVED.value
        return app.status in open_values

    return [item for item in batch.items if meets(item.application)]


def batch_decisions(db: Session, batch: Batch) -> dict[str, int]:
    """How the specialist has dealt with a batch's rows, for the applicant's page."""
    counts = {"open": 0, "approved": 0, "correction_requested": 0, "rejected": 0, "failed": 0}
    open_values = {s.value for s in OPEN_STATUSES}
    for status, n in db.execute(
        select(Application.status, func.count())
        .where(Application.batch_id == batch.id)
        .group_by(Application.status)
    ).all():
        if status in open_values:
            counts["open"] += n
        elif status in counts:
            counts[status] += n
    counts["failed"] = batch.failed
    return counts


def queue_stats(db: Session) -> QueueStats:
    """The four stat cards, from one grouped count instead of one query per card."""
    open_statuses = {status.value for status in OPEN_STATUSES}
    rows = db.execute(
        select(Application.status, Application.recommendation, func.count()).group_by(
            Application.status, Application.recommendation
        )
    ).all()
    totals = {"open": 0, "ready": 0, "review": 0, "corrections": 0, "approved": 0}
    for status, recommendation, n in rows:
        if status in open_statuses:
            totals["open"] += n
            key = "ready" if recommendation == Recommendation.APPROVE.value else "review"
            totals[key] += n
        elif status == ApplicationStatus.CORRECTION_REQUESTED.value:
            totals["corrections"] += n
        elif status == ApplicationStatus.APPROVED.value:
            totals["approved"] += n
    return QueueStats(**totals)


def applicant_applications(db: Session, applicant: User) -> list[Application]:
    stmt = (
        select(Application)
        .where(Application.applicant_id == applicant.id)
        .order_by(Application.updated_at.desc())
    )
    return list(db.scalars(stmt).unique())
