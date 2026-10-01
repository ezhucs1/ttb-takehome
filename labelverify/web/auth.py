"""Password hashing, signed session cookies, and role guards.

A middleware in ``app.py`` resolves the session cookie to a ``User`` once per request and
stores it on ``request.state.user``; the dependencies below read from there.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .models import Role, User

log = logging.getLogger(__name__)

# Set LABELVERIFY_SECURE_COOKIES=true behind HTTPS (the deployment does) so the session
# cookie is never sent in clear; off by default so local http://127.0.0.1 still works.
SECURE_COOKIES = os.environ.get("LABELVERIFY_SECURE_COOKIES", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
SESSION_COOKIE = "lv_session"
SESSION_MAX_AGE = 60 * 60 * 12
_PBKDF2_ROUNDS = 120_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def secret_key(data_dir: str | os.PathLike | None = None, *, database_url: str = "") -> str:
    """The cookie-signing key.

    ``SECRET_KEY`` in the environment wins (set it in any shared deployment). Otherwise a
    key is generated once and kept next to the database, so sessions survive restarts
    without anyone committing a secret. If that file cannot be written, the key is
    per-process and sessions end when the server stops.
    """
    configured = os.environ.get("SECRET_KEY", "").strip()
    if configured:
        return configured
    directory = Path(data_dir) if data_dir is not None else _default_data_dir(database_url)
    path = directory / ".secret_key"
    try:
        existing = path.read_text().strip()
        if len(existing) >= 32:
            return existing
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        log.warning("could not store a secret key at %s (%s); sessions end on restart", path, exc)
    return key


def _default_data_dir(url: str = "") -> Path:
    """Beside the SQLite file when there is one, else ./data."""
    url = url or os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        file = url.removeprefix("sqlite:///")
        if file and file != ":memory:":
            return Path(file).resolve().parent
    return Path("data")


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.secret_key, salt="session")


def sign_session(request: Request, user_id: str) -> str:
    return _serializer(request).dumps({"uid": user_id})


def read_session(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = _serializer(request).loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    return payload.get("uid")


class LoginRequired(HTTPException):
    """Raised for anonymous page requests; the app handler redirects to /login."""

    def __init__(self, next_url: str):
        super().__init__(status_code=401, detail="Login required")
        self.next_url = next_url


def optional_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def current_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        raise LoginRequired(
            request.url.path + (f"?{request.url.query}" if request.url.query else "")
        )
    return user


def require_role(role: Role) -> Callable[..., User]:
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role != role:
            raise HTTPException(status_code=403, detail="This page belongs to the other role.")
        return user

    return dependency


require_applicant = require_role(Role.APPLICANT)
require_specialist = require_role(Role.SPECIALIST)
