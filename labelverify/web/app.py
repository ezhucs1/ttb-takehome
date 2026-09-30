"""FastAPI application: the single-label verification screen and JSON API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..engine.extractors import ExtractionError, Extractor, get_extractor, resolve_extractor_name
from ..engine.extractors.demo import SAMPLES_DIR, load_manifest
from ..engine.models import ApplicationData, BeverageType
from ..engine.preprocess import UnreadableImageError
from ..engine.verify import run_verification

HERE = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

EXTRACTOR_LABELS = {
    "claude": "Claude vision model",
    "tesseract": "Local OCR (Tesseract)",
    "demo": "Demo mode (bundled samples only)",
    "fixture": "Test fixture",
}


def create_app(extractor: Extractor | None = None) -> FastAPI:
    app = FastAPI(title="LabelVerify", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    app.state.extractor = extractor
    app.state.samples = load_manifest()
    app.state.samples_by_id = {s["id"]: s for s in app.state.samples}

    def current_extractor() -> Extractor:
        if app.state.extractor is None:
            app.state.extractor = get_extractor()
        return app.state.extractor

    def extractor_label() -> str:
        name = app.state.extractor.name if app.state.extractor else resolve_extractor_name()
        return EXTRACTOR_LABELS.get(name, name)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "extractor": extractor_label()}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "samples": app.state.samples,
                "samples_json": json.dumps(
                    [{k: s[k] for k in ("id", "title", "application")} for s in app.state.samples]
                ),
                "beverage_types": [(b.value, _beverage_label(b)) for b in BeverageType],
                "extractor_label": extractor_label(),
                "demo_mode": (
                    app.state.extractor.name if app.state.extractor else resolve_extractor_name()
                )
                == "demo",
            },
        )

    @app.get("/samples/{sample_id}/image")
    def sample_image(sample_id: str):
        sample = app.state.samples_by_id.get(sample_id)
        if sample is None:
            raise HTTPException(404, "Unknown sample")
        return FileResponse(SAMPLES_DIR / sample["file"], media_type="image/png")

    async def _read_image(image: UploadFile | None, sample_id: str | None) -> tuple[bytes, str]:
        if image is not None and image.filename:
            data = await image.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    413, "Image is larger than 10 MB. Please resize it and try again."
                )
            if not data:
                raise HTTPException(400, "The uploaded file is empty.")
            return data, f"upload:{image.filename}"
        if sample_id:
            sample = app.state.samples_by_id.get(sample_id)
            if sample is None:
                raise HTTPException(400, "Unknown sample label.")
            return (SAMPLES_DIR / sample["file"]).read_bytes(), f"sample:{sample_id}"
        raise HTTPException(400, "Please upload a label image or pick a sample label.")

    def _application_from_form(**fields) -> ApplicationData:
        try:
            return ApplicationData(**fields)
        except ValidationError as exc:
            raise HTTPException(
                422, f"Application data is invalid: {exc.errors()[0]['msg']}"
            ) from exc

    @app.post("/verify", response_class=HTMLResponse)
    async def verify_html(
        request: Request,
        image: UploadFile | None = File(default=None),
        sample_id: str | None = Form(default=None),
        beverage_type: str = Form(...),
        brand_name: str = Form(""),
        class_type: str = Form(""),
        alcohol_content: str = Form(""),
        net_contents: str = Form(""),
        producer_name: str = Form(""),
        producer_address: str = Form(""),
        is_import: bool = Form(False),
        country_of_origin: str = Form(""),
    ):
        try:
            data, source = await _read_image(image, sample_id)
            application = _application_from_form(
                beverage_type=beverage_type,
                brand_name=brand_name,
                class_type=class_type,
                alcohol_content=alcohol_content,
                net_contents=net_contents,
                producer_name=producer_name,
                producer_address=producer_address,
                is_import=is_import,
                country_of_origin=country_of_origin,
            )
            result = run_verification(data, application, extractor=current_extractor())
        except HTTPException as exc:
            return _error_partial(templates, request, exc.status_code, str(exc.detail))
        except UnreadableImageError as exc:
            return _error_partial(templates, request, 400, str(exc))
        except ExtractionError as exc:
            return _error_partial(templates, request, 502, f"Could not read the label: {exc}")

        return templates.TemplateResponse(
            request,
            "partials/result.html",
            {"result": result, "application": application, "source": source},
        )

    @app.post("/api/verify")
    async def verify_json(
        image: UploadFile | None = File(default=None),
        sample_id: str | None = Form(default=None),
        application: str = Form(..., description="ApplicationData as a JSON string"),
    ):
        data, source = await _read_image(image, sample_id)
        try:
            app_data = ApplicationData.model_validate_json(application)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()) from exc
        try:
            result = run_verification(data, app_data, extractor=current_extractor())
        except UnreadableImageError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ExtractionError as exc:
            raise HTTPException(502, str(exc)) from exc
        payload = result.model_dump(mode="json")
        payload["source"] = source
        return JSONResponse(payload)

    return app


def _error_partial(templates: Jinja2Templates, request: Request, status: int, message: str):
    return templates.TemplateResponse(
        request, "partials/error.html", {"message": message}, status_code=status
    )


def _beverage_label(b: BeverageType) -> str:
    return {
        BeverageType.DISTILLED_SPIRITS: "Distilled spirits",
        BeverageType.WINE: "Wine",
        BeverageType.MALT_BEVERAGE: "Malt beverage (beer)",
    }[b]


app = create_app()
