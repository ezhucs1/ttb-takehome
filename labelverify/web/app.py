"""FastAPI application factory.

``create_app()`` wires the database, session cookies, templates, and routers. Tests call
it with a temporary database and an injected extractor; ``app`` at the bottom is what
uvicorn serves.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from ..engine.extractors import Extractor, fallback_extractor, get_extractor
from ..engine.extractors.demo import load_manifest
from . import services
from .auth import SECURE_COOKIES, LoginRequired, LoginThrottle, read_session, secret_key
from .db import init_db, make_engine, make_session_factory
from .models import User
from .render import Renderer, build_templates
from .routes import api, applicant, shared, specialist
from .seed import seed

HERE = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


# What every response carries. Scripts only from this origin (the theme script is a
# file, not inline); styles may be inline because the result table sets widths; images
# may be blobs because the file picker previews them before upload.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class HardeningMiddleware(BaseHTTPMiddleware):
    """Two things a browser-facing app needs beyond the session cookie: a request that
    changes state must come from this site (the cookie's SameSite=Lax already keeps it off
    cross-site form posts; this refuses the rest, by the Origin or Referer the browser
    sends), and every response carries the headers that keep it in its own frame and
    origin."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method in _UNSAFE_METHODS
            and not path.startswith("/api/")
            and not _same_site(request)
        ):
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not path.startswith(("/api/docs", "/api/openapi.json")):  # the docs UI loads a CDN
            headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if SECURE_COOKIES:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _same_site(request: Request) -> bool:
    """True unless the browser says the request came from another site. A request with no
    Origin and no Referer (a command-line client) is let through: it carries no cookie
    unless the caller chose to send one."""
    fetch_site = request.headers.get("sec-fetch-site", "")
    if fetch_site == "cross-site":
        return False
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlsplit(value).netloc == host
    return True


class UserMiddleware(BaseHTTPMiddleware):
    """Resolve the session cookie to a User once per request."""

    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        if request.url.path.startswith(("/static/", "/healthz")):  # no user lookup for assets
            return await call_next(request)
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
    fallback: Extractor | None | str = "auto",
) -> FastAPI:
    app = FastAPI(title="LabelVerify", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", CachedStaticFiles(directory=HERE / "static"), name="static")

    engine = make_engine(database_url)
    init_db(engine)
    app.state.session_factory = make_session_factory(engine)
    stale = services.finish_stale_batches(app.state.session_factory)
    if stale:
        log.warning("closed %d batch(es) left processing by a previous process", stale)
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

    def resolve_fallback() -> Extractor | None:
        """The reader used when the configured one fails; "auto" means the registry's."""
        if fallback == "auto":
            return fallback_extractor(resolve_extractor().name)
        return fallback  # a test's fixture, or None

    app.state.get_fallback = resolve_fallback

    if seed_data:
        with app.state.session_factory() as db:
            seed(db)

    app.add_middleware(UserMiddleware)
    app.add_middleware(HardeningMiddleware)
    app.state.login_throttle = LoginThrottle()

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/") or _wants_json(request):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        if request.headers.get("x-partial") == "1":  # a fetch from a page whose session ended
            return app.state.render.partial(
                request,
                "partials/error.html",
                status_code=401,
                message="Your session has ended. Sign in again to continue.",
            )
        return RedirectResponse(f"/login?next={exc.next_url}", status_code=303)

    @app.exception_handler(StarletteHTTPException)  # FastAPI's and the router's own 404/405
    async def _http_error(request: Request, exc: StarletteHTTPException):
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
