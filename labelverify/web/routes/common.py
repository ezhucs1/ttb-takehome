"""Helpers shared by route modules."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ...engine.models import ApplicationData
from ...engine.preprocess import UnreadableImageError
from ..models import Application, Role, User
from ..render import Renderer


def renderer(request: Request) -> Renderer:
    return request.app.state.render


def load_application(db: Session, app_id: str, user: User) -> Application:
    """Fetch an application the user may see: specialists see all, applicants their own."""
    app = db.get(Application, app_id)
    if app is None or (user.role == Role.APPLICANT and app.applicant_id != user.id):
        raise HTTPException(404, "Application not found.")
    return app


def application_from_form(form: dict) -> ApplicationData:
    def text(key: str) -> str:
        return str(form.get(key, "") or "").strip()

    is_import = str(form.get("is_import", "")).lower() in ("true", "on", "1", "yes")
    try:
        return ApplicationData(
            beverage_type=text("beverage_type") or "distilled_spirits",
            brand_name=text("brand_name"),
            class_type=text("class_type"),
            alcohol_content=text("alcohol_content"),
            net_contents=text("net_contents"),
            producer_name=text("producer_name"),
            producer_address=text("producer_address"),
            is_import=is_import,
            country_of_origin=text("country_of_origin"),
        )
    except ValueError as exc:
        raise HTTPException(422, f"Application data is invalid: {exc}") from exc


async def read_upload(upload) -> tuple[bytes, str] | None:
    if upload is None or not getattr(upload, "filename", ""):
        return None
    data = await upload.read()
    if not data:
        raise UnreadableImageError("The uploaded file is empty.")
    return data, upload.filename


def sample_image(request: Request, sample_id: str) -> tuple[bytes, str]:
    from ...engine.extractors.demo import SAMPLES_DIR

    sample = request.app.state.samples_by_id.get(sample_id)
    if sample is None:
        raise HTTPException(400, "Unknown sample label.")
    return (SAMPLES_DIR / sample["file"]).read_bytes(), sample["file"]
