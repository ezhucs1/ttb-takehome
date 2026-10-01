"""Template rendering with shared context, flash messages, and display helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from ..engine.extractors import resolve_extractor_name
from . import services
from .models import ApplicationStatus
from .services import FIELD_LABELS

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
FLASH_COOKIE = "lv_flash"

STATUS_LABELS = {
    ApplicationStatus.DRAFT: "Draft",
    ApplicationStatus.SUBMITTED: "Submitted",
    ApplicationStatus.UNDER_REVIEW: "Under review",
    ApplicationStatus.CORRECTION_REQUESTED: "Correction requested",
    ApplicationStatus.RESUBMITTED: "Resubmitted",
    ApplicationStatus.APPROVED: "Approved",
    ApplicationStatus.REJECTED: "Rejected",
}

STATUS_TONES = {
    ApplicationStatus.DRAFT: "neutral",
    ApplicationStatus.SUBMITTED: "info",
    ApplicationStatus.UNDER_REVIEW: "info",
    ApplicationStatus.CORRECTION_REQUESTED: "warning",
    ApplicationStatus.RESUBMITTED: "info",
    ApplicationStatus.APPROVED: "success",
    ApplicationStatus.REJECTED: "danger",
}

RECOMMENDATION_LABELS = {
    "approve": "All fields match",
    "needs_review": "Needs a look",
    "request_correction": "Corrections needed",
    "error": "Could not read label",
    None: "Not checked",
}

RECOMMENDATION_TONES = {
    "approve": "success",
    "needs_review": "warning",
    "request_correction": "danger",
    "error": "neutral",
    None: "neutral",
}

VERDICT_LABELS = {
    "match": "Match",
    "needs_review": "Review",
    "mismatch": "Mismatch",
    "not_applicable": "N/A",
}

EXTRACTOR_LABELS = {
    "claude": "Claude vision model",
    "gemini": "Gemini vision model",
    "tesseract": "Local OCR (Tesseract)",
    "demo": "Demo mode",
    "fixture": "Test fixture",
}

BEVERAGE_LABELS = {
    "distilled_spirits": "Distilled spirits",
    "wine": "Wine",
    "malt_beverage": "Malt beverage",
}


def format_dt(value: datetime | None, fmt: str = "%b %d, %Y %H:%M") -> str:
    return value.strftime(fmt) if value else ""


def relative_time(value: datetime | None) -> str:
    if value is None:
        return ""
    delta = datetime.utcnow() - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return "yesterday" if days == 1 else f"{days} days ago"


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    env = templates.env
    env.filters["dt"] = format_dt
    env.filters["ago"] = relative_time
    env.filters["tojson_attr"] = lambda v: json.dumps(v)
    env.globals.update(
        STATUS_LABELS=STATUS_LABELS,
        STATUS_TONES=STATUS_TONES,
        RECOMMENDATION_LABELS=RECOMMENDATION_LABELS,
        RECOMMENDATION_TONES=RECOMMENDATION_TONES,
        VERDICT_LABELS=VERDICT_LABELS,
        FIELD_LABELS=FIELD_LABELS,
        BEVERAGE_LABELS=BEVERAGE_LABELS,
    )
    return templates


class Renderer:
    def __init__(self, templates: Jinja2Templates):
        self.templates = templates

    def _flash_serializer(self, request: Request) -> URLSafeSerializer:
        return URLSafeSerializer(request.app.state.secret_key, salt="flash")

    def set_flash(
        self, request: Request, response: RedirectResponse, tone: str, message: str
    ) -> None:
        token = self._flash_serializer(request).dumps({"tone": tone, "message": message})
        response.set_cookie(FLASH_COOKIE, token, max_age=60, httponly=True, samesite="lax")

    def pop_flash(self, request: Request) -> dict | None:
        token = request.cookies.get(FLASH_COOKIE)
        if not token:
            return None
        try:
            return self._flash_serializer(request).loads(token)
        except BadSignature:
            return None

    def extractor_label(self, request: Request) -> str:
        extractor = request.app.state.extractor
        name = extractor.name if extractor is not None else resolve_extractor_name()
        return EXTRACTOR_LABELS.get(name, name)

    def page(self, request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
        flash = self.pop_flash(request)
        context.setdefault("user", getattr(request.state, "user", None))
        context.setdefault("flash", flash)
        context.setdefault("extractor_label", self.extractor_label(request))
        context.setdefault("demo_mode", self.extractor_label(request) == "Demo mode")
        context.setdefault("path", request.url.path)
        context.setdefault("asset_version", getattr(request.app.state, "asset_version", ""))
        if context["user"] is not None and "unread_total" not in context:
            with request.app.state.session_factory() as db:
                context["unread_total"] = services.unread_total(db, context["user"])
        response = self.templates.TemplateResponse(request, name, context, status_code=status_code)
        if flash:
            response.delete_cookie(FLASH_COOKIE)
        return response

    def partial(
        self, request: Request, name: str, status_code: int = 200, **context
    ) -> HTMLResponse:
        context.setdefault("user", getattr(request.state, "user", None))
        return self.templates.TemplateResponse(request, name, context, status_code=status_code)

    def redirect(
        self, request: Request, url: str, *, flash: tuple[str, str] | None = None
    ) -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        if flash:
            self.set_flash(request, response, *flash)
        return response
