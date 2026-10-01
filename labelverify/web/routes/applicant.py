"""Applicant portal: dashboard, new application, detail and resubmission, batch upload."""

from __future__ import annotations

import logging
import time

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...engine.extractors import ExtractionError
from ...engine.models import ApplicationData
from ...engine.preprocess import UnreadableImageError
from ...engine.rules import all_rules
from .. import services
from ..auth import require_applicant
from ..db import get_db
from ..models import ApplicationStatus, Batch, User
from .common import (
    application_from_form,
    load_application,
    read_uploads,
    renderer,
    sample_image,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/applicant", dependencies=[Depends(require_applicant)])


# Dashboard filters, keyed by the stat card that selects them.
DASHBOARD_FILTERS: dict[str, tuple[str, tuple[ApplicationStatus, ...]]] = {
    "action": ("Needs your action", (ApplicationStatus.CORRECTION_REQUESTED,)),
    "review": (
        "In review",
        (
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.UNDER_REVIEW,
            ApplicationStatus.RESUBMITTED,
        ),
    ),
    "approved": ("Approved", (ApplicationStatus.APPROVED,)),
    "drafts": ("Drafts", (ApplicationStatus.DRAFT,)),
}


@router.get("")
def dashboard(
    request: Request,
    filter: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    apps = services.applicant_applications(db, user)
    counts = {status.value: 0 for status in ApplicationStatus}
    for app in apps:
        counts[app.status] += 1
    needs_action = [a for a in apps if a.status == ApplicationStatus.CORRECTION_REQUESTED]
    if filter not in DASHBOARD_FILTERS:
        filter = ""
    shown = apps
    if filter:
        statuses = {s.value for s in DASHBOARD_FILTERS[filter][1]}
        shown = [a for a in apps if a.status in statuses]
    return renderer(request).page(
        request,
        "applicant/dashboard.html",
        apps=shown,
        counts=counts,
        needs_action=needs_action,
        filter=filter,
        filter_label=DASHBOARD_FILTERS[filter][0] if filter else "",
    )


# --------------------------------------------------------------------------- new application


@router.get("/applications/new")
def new_application(request: Request, user: User = Depends(require_applicant)):
    return renderer(request).page(
        request, "applicant/new.html", samples=request.app.state.samples, rules=all_rules()
    )


@router.post("/applications")
async def create_application(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
    images: list[UploadFile] = File(default=[]),
    sample_id: str = Form(""),
    beverage_type: str = Form(""),
):
    """Step 1: store the label set, run extraction, and return pre-filled form values."""
    try:
        uploads = await read_uploads(images)
        if not uploads and sample_id:
            uploads = [sample_image(request, sample_id)]
        if not uploads:
            raise HTTPException(400, "Choose a label image or a sample label first.")
        app = services.create_draft(
            db,
            user,
            ApplicationData(beverage_type=beverage_type or None, brand_name="", class_type=""),
            uploads,
        )
    except UnreadableImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()

    return JSONResponse(_read_label(request, db, app))


def _read_label(request: Request, db: Session, app) -> dict:
    """Read the stored images once and report form values; a failure is reported, not
    raised, so the applicant can retry the read or fill the form by hand."""
    extractor = request.app.state.get_extractor()
    started = time.perf_counter()
    prefill: dict[str, str] = {}
    warning = None
    read_failed = False
    try:
        extraction = services.extract_for_prefill(extractor, app.current_images)
        prefill = services.prefill_fields(extraction)
        services.remember_extraction(
            app, extraction, extractor.name, int((time.perf_counter() - started) * 1000)
        )
        db.commit()
        if not extraction.image_quality.readable:
            warning = "The label is hard to read: " + "; ".join(extraction.image_quality.issues)
    except ExtractionError as exc:
        read_failed = True
        warning = f"Could not read the label automatically ({exc})"
        log.warning("read failed for %s (%d panels): %s", app.serial, len(app.current_images), exc)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    log.info("read %s: %d panel(s), %d ms, %s", app.serial, len(app.current_images), elapsed_ms, extractor.name)

    image_urls = [f"/applications/{app.id}/images/{img.id}" for img in app.current_images]
    return {
            "id": app.id,
            "serial": app.serial,
            "image_url": image_urls[0],
            "image_urls": image_urls,
            "prefill": prefill,
            "warning": warning,
            "read_failed": read_failed,
            "extractor": extractor.name,
            "ms": elapsed_ms,
    }


@router.post("/applications/{app_id}/read")
def reread(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    """Read the stored images again, after a timeout or a poor read."""
    app = load_application(db, app_id, user)
    if app.status != ApplicationStatus.DRAFT:
        raise HTTPException(409, "This application has already been submitted.")
    return JSONResponse(_read_label(request, db, app))


@router.post("/applications/{app_id}/precheck")
async def precheck(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    """Step 2: save the form and run the comparison; returns the result partial."""
    app = load_application(db, app_id, user)
    if app.status != ApplicationStatus.DRAFT:
        raise HTTPException(409, "This application has already been submitted.")
    form = await request.form()
    services.update_fields(app, application_from_form(dict(form)))
    run = services.record_run(db, app, request.app.state.get_extractor(), "precheck")
    db.commit()
    return renderer(request).partial(
        request,
        "partials/verification.html",
        app=app,
        run=run,
        result=services.result_of(run),
        comments={},
        can_comment=False,
        applicant_view=True,
    )


@router.post("/applications/{app_id}/submit")
async def submit(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    """Step 3: final save and submit to the specialist queue."""
    app = load_application(db, app_id, user)
    if app.status != ApplicationStatus.DRAFT:
        raise HTTPException(409, "This application has already been submitted.")
    form = await request.form()
    data = application_from_form(dict(form))
    if not data.brand_name or not data.class_type:
        raise HTTPException(422, "Brand name and class/type are required before submitting.")
    services.update_fields(app, data)
    if app.latest_run is None or app.latest_run.trigger != "precheck":
        services.record_run(db, app, request.app.state.get_extractor(), "submit")
    services.submit(db, app, user)
    db.commit()
    return renderer(request).redirect(
        request,
        f"/applicant/applications/{app.id}",
        flash=(
            "success",
            f"Application {app.serial} submitted. A specialist will review it shortly.",
        ),
    )


# --------------------------------------------------------------------------- detail


@router.get("/applications/{app_id}")
def detail(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    app = load_application(db, app_id, user)
    # Arriving through an inbox link consumes only that item; a direct open consumes all.
    unread = services.unread_for(db, app, user, consume=request.query_params.get("via") != "inbox")
    db.commit()
    run = app.latest_run
    return renderer(request).page(
        request,
        "applicant/detail.html",
        app=app,
        run=run,
        result=services.result_of(run),
        comments=services.comments_by_field(app),
        can_comment=not app.is_decided,
        latest_notice=app.notices[-1] if app.notices else None,
        unread=unread,
    )


@router.post("/applications/{app_id}/resubmit")
async def resubmit(
    request: Request,
    app_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
    images: list[UploadFile] = File(default=[]),
    message: str = Form(""),
):
    app = load_application(db, app_id, user)
    form = await request.form()
    data = application_from_form(dict(form))
    try:
        uploads = await read_uploads(images)
        services.resubmit(
            db,
            app,
            user,
            data,
            request.app.state.get_extractor(),
            uploads=uploads,
            message=message,
        )
    except (services.WorkflowError, UnreadableImageError) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return renderer(request).redirect(
        request,
        f"/applicant/applications/{app.id}",
        flash=("success", "Resubmitted. It is back in the review queue."),
    )


# --------------------------------------------------------------------------- batch


@router.get("/batches")
def batches(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_applicant)
):
    rows = list(
        db.scalars(
            select(Batch).where(Batch.applicant_id == user.id).order_by(Batch.created_at.desc())
        )
    )
    return renderer(request).page(
        request, "applicant/batches.html", batches=rows, errors=[], columns=services.BATCH_COLUMNS
    )


@router.get("/batches/template.csv")
def batch_template():
    return PlainTextResponse(
        services.batch_template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="labelverify-batch-template.csv"'},
    )


@router.get("/batches/sample.csv")
def sample_batch_csv(request: Request, user: User = Depends(require_applicant)):
    """A CSV covering the ten bundled samples, to try the batch flow without own data."""
    return PlainTextResponse(
        services.sample_batch_csv(request.app.state.samples),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="labelverify-sample-batch.csv"'},
    )


@router.get("/batches/sample-images.zip")
def sample_batch_zip(request: Request, user: User = Depends(require_applicant)):
    from fastapi.responses import Response

    from ...engine.extractors.demo import SAMPLES_DIR

    return Response(
        services.sample_batch_zip(request.app.state.samples, SAMPLES_DIR),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="labelverify-sample-images.zip"'},
    )


@router.post("/batches")
async def create_batch(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
    csv_file: UploadFile = File(...),
    zip_file: UploadFile = File(...),
):
    parsed = services.parse_batch(await csv_file.read(), await zip_file.read())
    if parsed.errors:
        rows = list(
            db.scalars(
                select(Batch).where(Batch.applicant_id == user.id).order_by(Batch.created_at.desc())
            )
        )
        return renderer(request).page(
            request,
            "applicant/batches.html",
            status_code=422,
            batches=rows,
            errors=parsed.errors,
            columns=services.BATCH_COLUMNS,
        )
    batch = services.create_batch(db, user, csv_file.filename or "batch.csv", parsed)
    db.commit()
    background.add_task(
        services.process_batch,
        request.app.state.session_factory,
        batch.id,
        parsed,
        request.app.state.get_extractor(),
    )
    return renderer(request).redirect(
        request,
        f"/applicant/batches/{batch.id}",
        flash=("info", f"Checking {batch.total} labels in the background."),
    )


def _load_batch(db: Session, batch_id: str, user: User) -> Batch:
    batch = db.get(Batch, batch_id)
    if batch is None or batch.applicant_id != user.id:
        raise HTTPException(404, "Batch not found.")
    return batch


@router.get("/batches/{batch_id}")
def batch_detail(
    request: Request,
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    batch = _load_batch(db, batch_id, user)
    return renderer(request).page(
        request, "applicant/batch_detail.html", batch=batch, summary=services.batch_summary(batch)
    )


@router.get("/batches/{batch_id}/rows")
def batch_rows(
    request: Request,
    batch_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_applicant),
):
    batch = _load_batch(db, batch_id, user)
    response = renderer(request).partial(
        request, "partials/batch_rows.html", batch=batch, summary=services.batch_summary(batch)
    )
    response.headers["X-Batch-Status"] = batch.status
    return response
