"""Seed demo users and a populated queue so the prototype is meaningful on first run."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.extractors import DemoExtractor
from ..engine.extractors.demo import SAMPLES_DIR
from ..engine.models import ApplicationData
from . import services
from .auth import hash_password
from .models import Application, Role, User

log = logging.getLogger(__name__)

# The shared demo password; a deployment may set its own in LABELVERIFY_DEMO_PASSWORD.
DEMO_PASSWORD = os.environ.get("LABELVERIFY_DEMO_PASSWORD", "").strip() or "labelverify"

# Two demo accounts, one per role. The applicant is a label-compliance agent who files
# COLAs on behalf of several producers, which is why one account holds labels from Old
# Tom Distillery, Stone's Throw Vineyards, and Caledonia Imports.
USERS = [
    {
        "email": "sarah.chen@ttb.gov",
        "name": "Sarah Chen",
        "role": Role.SPECIALIST,
        "organization": "TTB Label Compliance",
    },
    {
        "email": "maria@alvarezlabels.com",
        "name": "Maria Alvarez",
        "role": Role.APPLICANT,
        "organization": "Alvarez Label Services",
    },
]
SPECIALIST_EMAIL = USERS[0]["email"]
APPLICANT_EMAIL = USERS[1]["email"]


# Samples whose seeded state is fixed rather than spread by _default_state. The beer with
# no Government Warning at all is rejected outright, so the demo shows a rejection as well
# as corrections and approvals.
SEED_STATES = {"harbor-light-ipa-missing-warning": "rejected"}
REJECTION_NOTICE = (
    "Re: COLA application for Harbor Light IPA\n\n"
    "The label carries no Government Warning Statement. 27 CFR 16.21 requires the full "
    "statement on every container, so this application is rejected rather than returned "
    "for correction. Please file a new application with corrected artwork."
)

# Applicant replies seeded on the correction-requested samples, so the specialist's queue
# shows unread activity on first login. Keyed by sample id: (field, message).
SEED_REPLIES = {
    "old-tom-title-case-warning": (
        "health_warning",
        "Understood. Our printer is resetting the heading in capitals; revised artwork "
        "will be uploaded this week.",
    ),
}


def ensure_user(db: Session, *, email: str, name: str, role: str, organization: str = "") -> User:
    """Create an account with the demo password if it does not exist yet."""
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            name=name,
            role=role,
            organization=organization,
            password_hash=hash_password(DEMO_PASSWORD),
        )
        db.add(user)
        db.flush()
    return user


def seed_users(db: Session) -> dict[str, User]:
    users = {spec["email"]: ensure_user(db, **spec) for spec in USERS}
    db.flush()
    return users


def _applicant_for(sample_id: str, users: dict[str, User]) -> User:
    return users[APPLICANT_EMAIL]


def _default_state(expected: str, seen: dict[str, int]) -> str:
    """Spread samples across workflow states so every screen has content on first run."""
    n = seen.get(expected, 0)
    seen[expected] = n + 1
    if expected == "approve":
        return "approved" if n == 0 else "submitted"
    if expected == "request_correction":
        return "correction_requested" if n == 0 else "submitted"
    if expected == "needs_review":
        return "under_review" if n == 0 else "submitted"
    return "submitted"


def seed_applications(db: Session, users: dict[str, User], samples_dir: Path = SAMPLES_DIR) -> int:
    """Create one application per sample, in a mix of workflow states."""
    if db.scalar(select(Application).limit(1)) is not None:
        return 0
    manifest_path = samples_dir / "manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text())
    extractor = DemoExtractor(samples_dir)
    sarah = users["sarah.chen@ttb.gov"]
    created = 0
    seen: dict[str, int] = {}
    for n, sample in enumerate(manifest):
        applicant = _applicant_for(sample["id"], users)
        image = (samples_dir / sample["file"]).read_bytes()
        data = ApplicationData.model_validate(sample["application"])
        app = services.create_draft(db, applicant, data, [(image, sample["file"])])
        services.record_run(db, app, extractor, "precheck")
        state = SEED_STATES.get(sample["id"]) or _default_state(sample.get("expected", ""), seen)
        if state == "draft":
            continue
        services.submit(db, app, applicant)
        if state == "approved":
            services.decide(db, app, sarah, "approve")
        elif state == "rejected":
            services.claim_for_review(db, app, sarah)
            services.decide(db, app, sarah, "reject", notice_body=REJECTION_NOTICE)
        elif state == "correction_requested":
            draft = services.draft_correction(app, use_ai=False)
            services.decide(
                db,
                app,
                sarah,
                "request_correction",
                notice_body=draft.body,
                notice_source=draft.source,
            )
            if sample["id"] in SEED_REPLIES:
                reply_field, reply = SEED_REPLIES[sample["id"]]
                services.add_comment(db, app, applicant, reply_field, reply)
        elif state == "under_review":
            services.claim_for_review(db, app, sarah)
        if state != "submitted":
            services.mark_seen(db, app, sarah)  # she acted on it, so its submission is not news
        created += 1
        if n % 3 == 0:
            db.flush()
    return created


def seed(db: Session) -> None:
    users = seed_users(db)
    count = seed_applications(db, users)
    db.commit()
    if count:
        log.info("seeded %d applications", count)
