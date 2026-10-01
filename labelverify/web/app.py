"""FastAPI application factory.

``create_app()`` wires the database, session cookies, templates, and routers. Tests call
it with a temporary database and an injected extractor; ``app`` at the bottom is what
uvicorn serves.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from ..engine.extractors import Extractor, get_extractor
from ..engine.extractors.demo import load_manifest
from .auth import LoginRequired, read_session, secret_key
from .db import init_db, make_engine, make_session_factory
from .models import User
from .render import Renderer, build_templates
from .routes import api, applicant, shared, specialist
from .seed import seed

HERE = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


class UserMiddleware(BaseHTTPMiddleware):
    """Resolve the session cookie to a User once per request."""

    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        user_id = read_session(request)
        if user_id:
            with request.app.state.session_factory() as db:
                request.state.user = db.get(User, user_id)
        return await call_next(request)


def create_app(
    *,
    extractor: Extractor | None = None,
    database_url: str | None = None,
    seed_data: bool = True,
    secret: str | None = None,
) -> FastAPI:
    app = FastAPI(title="LabelVerify", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", CachedStaticFiles(directory=HERE / "static"), name="static")

    engine = make_engine(database_url)
    init_db(engine)
    app.state.session_factory = make_session_factory(engine)
    app.state.secret_key = secret or secret_key(database_url=database_url or "")
    app.state.extractor = extractor
    app.state.samples = load_manifest()
    app.state.samples_by_id = {s["id"]: s for s in app.state.samples}
    app.state.render = Renderer(build_templates())
    app.state.asset_version = _asset_version()

    def resolve_extractor() -> Extractor:
        if app.state.extractor is None:
            app.state.extractor = get_extractor()
        return app.state.extractor

    app.state.get_extractor = resolve_extractor

    if seed_data:
        with app.state.session_factory() as db:
            seed(db)

    app.add_middleware(UserMiddleware)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/") or _wants_json(request):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return RedirectResponse(f"/login?next={exc.next_url}", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/") or _wants_json(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if request.headers.get("x-partial") == "1":
            return app.state.render.partial(
                request, "partials/error.html", status_code=exc.status_code, message=str(exc.detail)
            )
        return app.state.render.page(
            request,
            "error.html",
            status_code=exc.status_code,
            status=exc.status_code,
            message=str(exc.detail),
        )

    app.include_router(shared.router)
    app.include_router(applicant.router)
    app.include_router(specialist.router)
    app.include_router(api.router)
    return app


class CachedStaticFiles(StaticFiles):
    """Static files with cache headers. A URL that carries the content hash (``?v=``)
    can be cached for a year, since any change to the file changes the URL; the fonts
    and anything requested without a version get a day."""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        scope = kwargs.get("scope") or (args[2] if len(args) > 2 else {})
        versioned = b"v=" in (scope.get("query_string") or b"")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if versioned else "public, max-age=86400"
        )
        return response


def _asset_version() -> str:
    """Short hash of the stylesheet and script, appended to their URLs so a deploy never
    serves a stale cached copy from the previous version."""
    import hashlib

    digest = hashlib.sha1()
    for name in ("app.css", "app.js"):
        digest.update((HERE / "static" / name).read_bytes())
    return digest.hexdigest()[:10]


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def serve() -> FastAPI:
    """Uvicorn entry point: ``uvicorn labelverify.web.app:serve --factory``."""
    from dotenv import load_dotenv

    load_dotenv()  # reads .env in the working directory; real env vars win
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    application = create_app()
    log.info("extractor: %s", application.state.render.extractor_label(_fake_request(application)))
    return application


def _fake_request(application: FastAPI):
    """Minimal stand-in so the startup log can reuse the renderer's label logic."""
    from types import SimpleNamespace

    return SimpleNamespace(app=application)
