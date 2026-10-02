"""Unread activity and the inbox feed for both roles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    ActivityRead,
    Application,
    ApplicationStatus,
    ApplicationView,
    Batch,
    Comment,
    Notice,
    Role,
    StatusEvent,
    User,
    utcnow,
)
from .common import WorkflowError
from .queue import batch_decisions

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
    Role.SPECIALIST: {ApplicationStatus.SUBMITTED, ApplicationStatus.RESUBMITTED},
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
    """Unread items across the user's applications (the sidebar badge). A batch counts
    once, however many of its rows have something new, matching the inbox's one line."""
    counts = unread_counts(db, user, _scoped_app_ids(db, user))
    if not counts:
        return 0
    batch_of = dict(
        db.execute(
            select(Application.id, Application.batch_id).where(
                Application.id.in_(list(counts)), Application.batch_id.is_not(None)
            )
        ).all()
    )
    total = sum(n for app_id, n in counts.items() if app_id not in batch_of)
    return total + len(set(batch_of.values()))


@dataclass
class ActivityItem:
    """One line in the inbox: something the other party did on an application."""

    kind: str  # comment | notice | status
    item_id: str
    app: Application | None  # None on a batch line
    created_at: datetime
    actor: User | None
    field: str  # comment field, "general", or "" for notices and decisions
    text: str
    unread: bool
    status: str = ""  # the status reached, for status items
    batch: Batch | None = None  # set on a "batch" line that stands for a whole batch's activity
    count: int = 1  # items folded into a batch line

    @property
    def anchor(self) -> str:
        if self.kind == "comment":
            return "thread-host-general" if self.field == "general" else f"field-{self.field}"
        return "notice" if self.kind == "notice" else "history"


def _batch_lines(
    db: Session,
    user: User,
    batch_of: dict[str, str],
    unread_apps: set[str],
) -> list[ActivityItem]:
    """One inbox line per batch, from grouped counts rather than per-row items: three
    hundred submissions or decisions are one line, and the queries stay three however
    many rows the batch has. ``batch_of`` maps each batch row's application id to its
    batch; ``unread_apps`` is the set of those rows with something unread."""
    if not batch_of:
        return []
    app_ids = list(batch_of)
    other = other_role(user)
    watched = [s.value for s in FEED_STATUSES[Role(user.role)]]
    per: dict[str, dict] = {}

    def note(app_id: str, key: str, n: int, latest: datetime | None) -> None:
        b = per.setdefault(
            batch_of[app_id],
            {"comments": 0, "notices": 0, "resubmitted": 0, "latest": None},
        )
        b[key] = b.get(key, 0) + n
        if latest is not None and (b["latest"] is None or latest > b["latest"]):
            b["latest"] = latest

    for app_id, n, latest in db.execute(
        select(Comment.application_id, func.count(), func.max(Comment.created_at))
        .join(User, Comment.author_id == User.id)
        .where(User.role == other, Comment.application_id.in_(app_ids))
        .group_by(Comment.application_id)
    ):
        note(app_id, "comments", n, latest)
    for app_id, to_status, n, latest in db.execute(
        select(
            StatusEvent.application_id,
            StatusEvent.to_status,
            func.count(),
            func.max(StatusEvent.created_at),
        )
        .join(User, StatusEvent.actor_id == User.id)
        .where(
            User.role == other,
            StatusEvent.to_status.in_(watched),
            StatusEvent.application_id.in_(app_ids),
        )
        .group_by(StatusEvent.application_id, StatusEvent.to_status)
    ):
        note(app_id, to_status, n, latest)
    if user.role == Role.APPLICANT:
        for app_id, n, latest in db.execute(
            select(Notice.application_id, func.count(), func.max(Notice.created_at))
            .where(Notice.application_id.in_(app_ids))
            .group_by(Notice.application_id)
        ):
            note(app_id, "notices", n, latest)
    if not per:
        return []
    batches = {
        b.id: b
        for b in db.scalars(
            select(Batch).where(Batch.id.in_(list(per))).options(selectinload(Batch.applicant))
        )
    }
    unread_batches = {batch_of[a] for a in unread_apps if a in batch_of}
    lines = []
    for batch_id, counts in per.items():
        batch = batches.get(batch_id)
        if batch is None or counts["latest"] is None:
            continue
        actor = batch.applicant if user.role == Role.SPECIALIST else _batch_specialist(db, batch)
        lines.append(
            ActivityItem(
                "batch",
                batch_id,
                None,
                counts["latest"],
                actor,
                "",
                _batch_activity_text(db, batch, counts, user),
                batch_id in unread_batches,
                status="",
                batch=batch,
                count=sum(v for k, v in counts.items() if k != "latest"),
            )
        )
    return lines


def _batch_specialist(db: Session, batch: Batch) -> User | None:
    """Whoever decided a row of the batch, for the applicant's inbox line."""
    return db.scalar(
        select(User)
        .join(Application, Application.specialist_id == User.id)
        .where(Application.batch_id == batch.id)
        .limit(1)
    )


def _batch_activity_text(db: Session, batch: Batch, counts: dict, user: User) -> str:
    """One sentence for a batch line: what happened across its rows."""
    replies = counts.get("comments", 0)
    if user.role == Role.SPECIALIST:
        parts = [f"a batch of {batch.total} labels, {batch.completed} checked"]
        if counts.get("resubmitted"):
            parts.append(f"{counts['resubmitted']} resubmitted")
        if replies:
            parts.append(f"{replies} repl{'ies' if replies != 1 else 'y'}")
        return "; ".join(parts)
    decided = batch_decisions(db, batch)
    parts = []
    if decided["approved"]:
        parts.append(f"{decided['approved']} approved")
    if decided["correction_requested"]:
        n = decided["correction_requested"]
        parts.append(f"{n} correction request{'s' if n != 1 else ''}")
    if decided["rejected"]:
        parts.append(f"{decided['rejected']} rejected")
    if replies:
        parts.append(f"{replies} question{'s' if replies != 1 else ''} on fields")
    if decided["open"]:
        parts.append(f"{decided['open']} still waiting")
    return ", ".join(parts) or "activity on the batch"


def activity_feed(db: Session, user: User, *, limit: int = 40) -> list[ActivityItem]:
    """The other party's recent activity across the user's applications, newest first."""
    scoped = _scoped_app_ids(db, user)
    if not scoped:
        return []
    # Rows of a batch are folded into one line per batch from grouped counts; only single
    # applications are listed item by item, so a large batch never crowds them out.
    batch_of = dict(
        db.execute(
            select(Application.id, Application.batch_id).where(
                Application.id.in_(scoped), Application.batch_id.is_not(None)
            )
        ).all()
    )
    ids = [i for i in scoped if i not in batch_of]
    seen = _seen_map(db, user, ids)
    read = _read_keys(db, user)
    unread_rows = set(unread_counts(db, user, list(batch_of))) if batch_of else set()
    items: list[ActivityItem] = _batch_lines(db, user, batch_of, unread_rows)
    if not ids:
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[:limit]

    def is_unread(kind: str, item_id: str, app_id: str, created_at: datetime) -> bool:
        return _after(created_at, seen.get(app_id)) and (kind, item_id) not in read

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
                status=e.to_status,
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


def open_batch_items(db: Session, user: User, batch_id: str) -> Batch:
    """Opening a batch line reads every row's items for this user."""
    batch = db.get(Batch, batch_id)
    if batch is None or (user.role == Role.APPLICANT and batch.applicant_id != user.id):
        raise WorkflowError("That inbox item no longer exists.")
    for item in batch.items:
        if item.application is not None:
            mark_seen(db, item.application, user)
    return batch


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
