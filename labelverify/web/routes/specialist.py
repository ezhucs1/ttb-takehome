"""Specialist workspace: queue, review, decisions, notices."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .. import services
from ..auth import require_specialist
from ..db import get_db
from ..models import Batch, User
from .common import load_application, renderer

router = APIRouter(prefix="/specialist", dependencies=[Depends(require_specialist)])


@router.get("")
def queue(
    request: Request,
    tab: str = "open",
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
):
    if tab not in services.QUEUE_TABS:
        tab = "open"
    apps = services.queue(db, tab)
    return renderer(request).page(
        request,
        "specialist/queue.html",
        tab=tab,
        tabs=services.QUEUE_TABS,
        apps=apps,
        batches=services.batch_rollups(db, tab),
        stats=services.queue_stats(db),
    )


def _load_batch(db: Session, batch_id: str) -> Batch:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "Batch not found.")
    return batch


@router.get("/batches/{batch_id}")
def batch_page(
    request: Request,
    batch_id: str,
    tab: str = "open",
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
):
    """A batch as one bundle: its rows under the queue's tabs, decided one by one."""
    if tab not in services.QUEUE_TABS and tab != "all":
        tab = "open"
    batch = _load_batch(db, batch_id)
    services.open_batch_items(db, user, batch.id)  # opening the bundle reads its rows' news
    db.commit()
    return renderer(request).page(
        request,
        "specialist/batch.html",
        batch=batch,
        tab=tab,
        tabs={"all": "All rows", **services.QUEUE_TABS},
        rollup=services.batch_rollup(db, batch, tab),
        items=services.batch_items_for(batch, tab),
    )


@router.post("/batches/{batch_id}/bulk-approve")
async def batch_bulk_approve(
    request: Request,
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
):
    batch = _load_batch(db, batch_id)
    form = await request.form()
    allowed = {item.application_id for item in batch.items if item.application_id}
    ids = [i for i in form.getlist("ids") if i in allowed]
    approved = services.bulk_approve(db, user, ids)
    db.commit()
    return renderer(request).redirect(
        request,
        f"/specialist/batches/{batch.id}?tab=ready",
        flash=("success", f"Approved {approved} row{'s' if approved != 1 else ''} of the batch."),
    )


@router.post("/bulk-approve")
async def bulk_approve(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_specialist)
):
    form = await request.form()
    ids = form.getlist("ids")
    approved = services.bulk_approve(db, user, ids)
    db.commit()
    return renderer(request).redirect(
        request,
        "/specialist?tab=ready",
        flash=("success", f"Approved {approved} application{'s' if approved != 1 else ''}."),
    )


@router.get("/applications/{app_id}")
def review(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
):
    app = load_application(db, app_id, user)
    services.claim_for_review(db, app, user)
    # Arriving through an inbox link consumes only that item; a direct open consumes all.
    unread = services.unread_for(db, app, user, consume=request.query_params.get("via") != "inbox")
    db.commit()
    run = app.latest_run
    result = services.result_of(run)
    return renderer(request).page(
        request,
        "specialist/review.html",
        app=app,
        batch=db.get(Batch, app.batch_id) if app.batch_id else None,
        run=run,
        result=result,
        comments=services.comments_by_field(app),
        can_comment=not app.is_decided,
        latest_notice=app.notices[-1] if app.notices else None,
        flagged=services.flagged_fields(result) if result else [],
        unread=unread,
    )


@router.post("/applications/{app_id}/notice")
def draft_notice(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
):
    app = load_application(db, app_id, user)
    try:
        draft = services.draft_correction(app)
    except services.WorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    return renderer(request).partial(request, "partials/notice_editor.html", app=app, draft=draft)


@router.post("/applications/{app_id}/rerun")
def rerun(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
):
    app = load_application(db, app_id, user)
    if app.is_decided:
        raise HTTPException(409, "This application has already been decided.")
    services.record_run(
        db,
        app,
        request.app.state.get_extractor(),
        "rerun",
        fresh=True,
        fallback=request.app.state.get_fallback(),
    )
    db.commit()
    return renderer(request).redirect(
        request, f"/specialist/applications/{app.id}", flash=("info", "Label re-checked.")
    )


@router.post("/applications/{app_id}/decision")
def decision(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_specialist),
    action: str = Form(...),
    notice_body: str = Form(""),
    notice_source: str = Form("template"),
    note: str = Form(""),
):
    app = load_application(db, app_id, user)
    try:
        services.decide(
            db, app, user, action, notice_body=notice_body, notice_source=notice_source, note=note
        )
    except services.WorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    messages = {
        "approve": ("success", f"{app.serial} approved."),
        "reject": ("info", f"{app.serial} rejected."),
        "request_correction": ("success", f"Correction request sent to {app.organization}."),
    }
    back = f"/specialist/batches/{app.batch_id}" if app.batch_id else "/specialist"
    return renderer(request).redirect(request, back, flash=messages[action])
