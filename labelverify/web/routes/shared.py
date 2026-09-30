"""Login, logout, home redirect, label images, comment threads, health."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    current_user,
    optional_user,
    sign_session,
    verify_password,
)
from ..db import get_db
from ..models import Comment, Role, User
from ..seed import DEMO_PASSWORD, USERS
from .common import load_application, renderer

router = APIRouter()


def _home_for(user: User) -> str:
    return "/specialist" if user.role == Role.SPECIALIST else "/applicant"


@router.get("/healthz")
def healthz(request: Request) -> dict:
    return {"status": "ok", "extractor": renderer(request).extractor_label(request)}


@router.get("/me/unread")
def unread(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Applications with activity the user has not seen; polled by the sidebar badge."""
    return {"applications": services.unread_total(db, user)}


@router.get("/")
def home(request: Request, user: User | None = Depends(optional_user)):
    return RedirectResponse(_home_for(user) if user else "/login", status_code=303)


@router.get("/login")
def login_page(request: Request, next: str = "", user: User | None = Depends(optional_user)):
    if user:
        return RedirectResponse(_home_for(user), status_code=303)
    return renderer(request).page(
        request, "login.html", next=next, demo_users=USERS, demo_password=DEMO_PASSWORD, error=None
    )


@router.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not verify_password(password, user.password_hash):
        return renderer(request).page(
            request,
            "login.html",
            status_code=401,
            next=next,
            demo_users=USERS,
            demo_password=DEMO_PASSWORD,
            error="That email and password do not match.",
        )
    target = next if next.startswith("/") and not next.startswith("//") else _home_for(user)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(request, user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/applications/{app_id}/images/{image_id}")
def label_image(
    app_id: str, image_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    app = load_application(db, app_id, user)
    image = next((i for i in app.images if i.id == image_id), None)
    if image is None:
        raise HTTPException(404, "Image not found.")
    return Response(
        image.data, media_type=image.media_type, headers={"Cache-Control": "private, max-age=3600"}
    )


@router.post("/applications/{app_id}/comments")
def add_comment(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    field: str = Form("general"),
    body: str = Form(""),
):
    app = load_application(db, app_id, user)
    try:
        services.add_comment(db, app, user, field, body)
    except services.WorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return renderer(request).partial(
        request,
        "partials/thread.html",
        app=app,
        field=field,
        comments=services.comments_by_field(app).get(field, []),
    )


@router.post("/applications/{app_id}/comments/{comment_id}/resolve")
def resolve_comment(
    request: Request,
    app_id: str,
    comment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    resolved: str = Form("true"),
):
    if user.role != Role.SPECIALIST:
        raise HTTPException(403, "Only a specialist can resolve a thread.")
    app = load_application(db, app_id, user)
    comment = db.get(Comment, comment_id)
    if comment is None or comment.application_id != app.id:
        raise HTTPException(404, "Comment not found.")
    services.resolve_comment(db, comment, resolved.lower() == "true")
    db.commit()
    db.refresh(app)
    return renderer(request).partial(
        request,
        "partials/thread.html",
        app=app,
        field=comment.field,
        comments=services.comments_by_field(app).get(comment.field, []),
    )
