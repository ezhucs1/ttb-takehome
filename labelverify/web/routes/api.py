"""Stateless JSON API: verify one label against application data without logging in."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...engine.extractors import ExtractionError
from ...engine.models import ApplicationData
from ...engine.preprocess import UnreadableImageError
from ...engine.verify import run_verification
from .common import read_upload, sample_image

router = APIRouter(prefix="/api")


@router.post("/verify")
async def verify(
    request: Request,
    image: UploadFile | None = File(default=None),
    sample_id: str = Form(""),
    application: str = Form(..., description="ApplicationData as a JSON string"),
):
    try:
        upload = await read_upload(image)
        if upload is None and sample_id:
            upload = sample_image(request, sample_id)
        if upload is None:
            raise HTTPException(400, "Provide an image file or a sample_id.")
        app_data = ApplicationData.model_validate_json(application)
        result = run_verification(upload[0], app_data, extractor=request.app.state.get_extractor())
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc
    except UnreadableImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from exc
    payload = result.model_dump(mode="json")
    payload["source"] = f"upload:{upload[1]}" if not sample_id else f"sample:{sample_id}"
    return JSONResponse(payload)


@router.get("/samples/{sample_id}/image")
def sample_image_route(request: Request, sample_id: str):
    from fastapi.responses import Response

    data, _ = sample_image(request, sample_id)
    return Response(
        data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"}
    )


@router.get("/samples")
def samples(request: Request):
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "description": s["description"],
            "application": s["application"],
        }
        for s in request.app.state.samples
    ]
