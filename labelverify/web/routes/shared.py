"""Login, logout, home redirect, label images, comment threads, health."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..auth import (
    SECURE_COOKIES,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    current_user,
    optional_user,
    sign_session,
    verify_password,
)
from ..db import get_db
from ..models import Comment, LabelImage, Role, User
from ..seed import DEMO_PASSWORD, USERS
from .common import load_application, renderer

router = APIRouter()


def _login_context() -> dict:
    """What the landing page shows besides the form. Demo accounts are listed unless
    LABELVERIFY_DEMO_ACCOUNTS=false, which a public deployment should set; reviewers then
    take the credentials from the README instead of the page."""
    show_demo = os.environ.get("LABELVERIFY_DEMO_ACCOUNTS", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    return {
        "demo_users": USERS if show_demo else [],
        "demo_password": DEMO_PASSWORD if show_demo else "",
        "repo_url": os.environ.get(
            "LABELVERIFY_REPO_URL", "https://github.com/ezhucs1/ttb-takehome"
        ),
    }


def _home_for(user: User) -> str:
    return "/specialist" if user.role == Role.SPECIALIST else "/applicant"


def _safe_next(user: User, next_url: str) -> str:
    """Where to go after login: the remembered page, unless it is off-site or belongs to
    the other role (a session that expired as an applicant must not send a specialist
    to an applicant page)."""
    if not next_url.startswith("/") or next_url.startswith("//"):
        return _home_for(user)
    other = "/applicant" if user.role == Role.SPECIALIST else "/specialist"
    if next_url == other or next_url.startswith(other + "/") or next_url.startswith(other + "?"):
        return _home_for(user)
    return next_url


@router.get("/healthz")
def healthz(request: Request) -> dict:
    return {"status": "ok", "extractor": renderer(request).extractor_label(request)}


@router.get("/rules")
def rules_page(request: Request, user: User = Depends(current_user)):
    from ...engine.rules import all_rules

    return renderer(request).page(request, "rules.html", rules=all_rules())


@router.get("/inbox")
def inbox(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    items = services.activity_feed(db, user)
    return renderer(request).page(
        request,
        "inbox.html",
        items=items,
        unread_items=[i for i in items if i.unread],
        read_items=[i for i in items if not i.unread],
    )


@router.get("/inbox/open/{kind}/{item_id}")
def inbox_open(
    request: Request,
    kind: str,
    item_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Mark one item read, then go to its application without consuming the others."""
    try:
        app, anchor = services.open_item(db, user, kind, item_id)
    except services.WorkflowError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    base = (
        "/specialist/applications/" if user.role == Role.SPECIALIST else "/applicant/applications/"
    )
    return RedirectResponse(f"{base}{app.id}?via=inbox#{anchor}", status_code=303)


@router.post("/inbox/read-all")
def inbox_read_all(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    cleared = services.mark_all_seen(db, user)
    db.commit()
    message = f"Marked {cleared} item{'s' if cleared != 1 else ''} as read."
    return renderer(request).redirect(request, "/inbox", flash=("info", message))


@router.get("/me/unread")
def unread(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Inbox items the user has not read; polled by the sidebar badge."""
    return {"count": services.unread_total(db, user)}


@router.get("/")
def home(request: Request, user: User | None = Depends(optional_user)):
    return RedirectResponse(_home_for(user) if user else "/login", status_code=303)


@router.get("/login")
def login_page(request: Request, next: str = "", user: User | None = Depends(optional_user)):
    if user:
        return RedirectResponse(_home_for(user), status_code=303)
    return renderer(request).page(request, "login.html", next=next, error=None, **_login_context())


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
            error="That email and password do not match.",
            **_login_context(),
        )
    response = RedirectResponse(_safe_next(user, next), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(request, user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
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
    image = db.get(LabelImage, image_id)  # one row, not every version's bytes
    if image is None or image.application_id != app.id:
        raise HTTPException(404, "Image not found.")
    return Response(  # an image id never changes content, so the browser may keep it
        image.data,
        media_type=image.media_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
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
    return renderer(request).partial(
        request,
        "partials/thread.html",
        app=app,
        field=comment.field,
        comments=services.comments_by_field(app).get(comment.field, []),
    )
