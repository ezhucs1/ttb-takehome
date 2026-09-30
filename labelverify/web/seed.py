"""Seed demo users and a populated queue so the prototype is meaningful on first run."""

from __future__ import annotations

import json
import logging
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

DEMO_PASSWORD = "labelverify"

USERS = [
    {
        "email": "sarah.chen@ttb.gov",
        "name": "Sarah Chen",
        "role": Role.SPECIALIST,
        "organization": "TTB Label Compliance",
    },
    {
        "email": "jenny.park@ttb.gov",
        "name": "Jenny Park",
        "role": Role.SPECIALIST,
        "organization": "TTB Label Compliance",
    },
    {
        "email": "labels@oldtomdistillery.com",
        "name": "Maria Alvarez",
        "role": Role.APPLICANT,
        "organization": "Old Tom Distillery",
    },
    {
        "email": "compliance@stonesthrow.wine",
        "name": "Devin Okafor",
        "role": Role.APPLICANT,
        "organization": "Stone's Throw Vineyards",
    },
    {
        "email": "imports@caledonia-imports.com",
        "name": "Priya Natarajan",
        "role": Role.APPLICANT,
        "organization": "Caledonia Imports",
    },
]


def seed_users(db: Session) -> dict[str, User]:
    users: dict[str, User] = {}
    for spec in USERS:
        user = db.scalar(select(User).where(User.email == spec["email"]))
        if user is None:
            user = User(password_hash=hash_password(DEMO_PASSWORD), **spec)
            db.add(user)
        users[spec["email"]] = user
    db.flush()
    return users


def _applicant_for(sample_id: str, users: dict[str, User]) -> User:
    if sample_id.startswith("stones-throw") or sample_id.startswith("sunset"):
        return users["compliance@stonesthrow.wine"]
    if sample_id.startswith("glen") or sample_id.startswith("harbor"):
        return users["imports@caledonia-imports.com"]
    return users["labels@oldtomdistillery.com"]


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
        state = sample.get("seed_state") or _default_state(sample.get("expected", ""), seen)
        if state == "draft":
            continue
        services.submit(db, app, applicant)
        if state == "approved":
            services.decide(db, app, sarah, "approve")
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
            if sample.get("seed_reply"):
                services.add_comment(
                    db,
                    app,
                    applicant,
                    sample.get("seed_reply_field", "general"),
                    sample["seed_reply"],
                )
        elif state == "under_review":
            services.claim_for_review(db, app, sarah)
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
