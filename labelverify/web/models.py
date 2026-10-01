"""ORM models for the applicant / specialist workflow."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(enum.StrEnum):
    APPLICANT = "applicant"
    SPECIALIST = "specialist"


class ApplicationStatus(enum.StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    CORRECTION_REQUESTED = "correction_requested"
    RESUBMITTED = "resubmitted"
    APPROVED = "approved"
    REJECTED = "rejected"


OPEN_STATUSES = (
    ApplicationStatus.SUBMITTED,
    ApplicationStatus.UNDER_REVIEW,
    ApplicationStatus.RESUBMITTED,
)
DECIDED_STATUSES = (ApplicationStatus.APPROVED, ApplicationStatus.REJECTED)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    organization: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def initials(self) -> str:
        return "".join(part[0] for part in self.name.split()[:2]).upper()


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    serial: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    organization: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default=ApplicationStatus.DRAFT, index=True)

    beverage_type: Mapped[str] = mapped_column(String(30), default="")  # "" = not stated
    beverage_type_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    brand_name: Mapped[str] = mapped_column(String(200), default="")
    class_type: Mapped[str] = mapped_column(String(200), default="")
    alcohol_content: Mapped[str] = mapped_column(String(100), default="")
    net_contents: Mapped[str] = mapped_column(String(100), default="")
    producer_name: Mapped[str] = mapped_column(String(200), default="")
    producer_address: Mapped[str] = mapped_column(String(300), default="")
    is_import: Mapped[bool] = mapped_column(Boolean, default=False)
    country_of_origin: Mapped[str] = mapped_column(String(100), default="")

    # The most recent read of the current label set, kept so the pre-check and the
    # submission compare against it instead of paying for a second model call.
    extraction_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_version: Mapped[int] = mapped_column(Integer, default=0)
    extraction_ms: Mapped[int] = mapped_column(Integer, default=0)
    extraction_extractor: Mapped[str] = mapped_column(String(80), default="")

    latest_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    specialist_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    applicant: Mapped[User] = relationship(foreign_keys=[applicant_id], lazy="joined")
    specialist: Mapped[User | None] = relationship(foreign_keys=[specialist_id], lazy="joined")
    images: Mapped[list[LabelImage]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="LabelImage.created_at"
    )
    runs: Mapped[list[VerificationRun]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="VerificationRun.created_at",
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    events: Mapped[list[StatusEvent]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="StatusEvent.created_at",
    )
    notices: Mapped[list[Notice]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Notice.created_at"
    )

    @property
    def current_version(self) -> int:
        return max((img.version for img in self.images), default=0)

    @property
    def current_images(self) -> list[LabelImage]:
        """All panels (front, back, neck) of the latest uploaded label set."""
        version = self.current_version
        return sorted((i for i in self.images if i.version == version), key=lambda i: i.panel)

    @property
    def current_image(self) -> LabelImage | None:
        images = self.current_images
        return images[0] if images else None

    def images_by_version(self) -> list[tuple[int, list[LabelImage]]]:
        grouped: dict[int, list[LabelImage]] = {}
        for img in self.images:
            grouped.setdefault(img.version, []).append(img)
        return [(v, sorted(imgs, key=lambda i: i.panel)) for v, imgs in sorted(grouped.items())]

    @property
    def latest_run(self) -> VerificationRun | None:
        return self.runs[-1] if self.runs else None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_decided(self) -> bool:
        return self.status in DECIDED_STATUSES

    def application_fields(self) -> dict:
        return {
            "beverage_type": self.beverage_type or None,
            "brand_name": self.brand_name,
            "class_type": self.class_type,
            "alcohol_content": self.alcohol_content,
            "net_contents": self.net_contents,
            "producer_name": self.producer_name,
            "producer_address": self.producer_address,
            "is_import": self.is_import,
            "country_of_origin": self.country_of_origin,
        }


class LabelImage(Base):
    __tablename__ = "label_images"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(60))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)  # loaded only when read
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)  # upload set (1 = original)
    panel: Mapped[int] = mapped_column(Integer, default=1)  # order within the set
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="images")

    @property
    def panel_label(self) -> str:
        return {1: "Front", 2: "Back", 3: "Neck"}.get(self.panel, f"Panel {self.panel}")


class VerificationRun(Base):
    __tablename__ = "verification_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    image_id: Mapped[str] = mapped_column(ForeignKey("label_images.id"))
    image_version: Mapped[int] = mapped_column(Integer, default=1)
    trigger: Mapped[str] = mapped_column(String(20))  # precheck, submit, resubmit, batch, rerun
    extractor: Mapped[str] = mapped_column(String(80))
    recommendation: Mapped[str] = mapped_column(String(30))
    result_json: Mapped[str] = mapped_column(Text)
    extraction_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="runs")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    field: Mapped[str] = mapped_column(String(40), default="general")
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(lazy="joined")


class ApplicationView(Base):
    """When a user last opened an application; activity after that is unread for them."""

    __tablename__ = "application_views"
    __table_args__ = (UniqueConstraint("user_id", "application_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActivityRead(Base):
    """A single inbox item (comment, notice, or status event) the user has read."""

    __tablename__ = "activity_reads"
    __table_args__ = (UniqueConstraint("user_id", "kind", "item_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # comment | notice | status
    item_id: Mapped[str] = mapped_column(String(32))
    read_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="events")
    actor: Mapped[User | None] = relationship(lazy="joined")


class Notice(Base):
    __tablename__ = "notices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    drafted_by: Mapped[str] = mapped_column(String(20))  # ai | template
    sent_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    application: Mapped[Application] = relationship(back_populates="notices")
    sent_by: Mapped[User] = relationship(lazy="joined")


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing | done
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    applicant: Mapped[User] = relationship(lazy="joined")
    items: Mapped[list[BatchItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="BatchItem.row_number"
    )


class BatchItem(Base):
    __tablename__ = "batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "row_number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    brand_name: Mapped[str] = mapped_column(String(200), default="")
    image_name: Mapped[str] = mapped_column(String(300), default="")
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    batch: Mapped[Batch] = relationship(back_populates="items")
    application: Mapped[Application | None] = relationship(lazy="joined")
