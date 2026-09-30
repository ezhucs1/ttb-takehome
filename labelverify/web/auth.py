"""Password hashing, signed session cookies, and role guards.

A middleware in ``app.py`` resolves the session cookie to a ``User`` once per request and
stores it on ``request.state.user``; the dependencies below read from there.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .models import Role, User

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


def secret_key() -> str:
    """Configured key, or a per-process key (sessions then reset on restart; see docs)."""
    return os.environ.get("SECRET_KEY") or secrets.token_hex(32)


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
        raise LoginRequired(str(request.url.path))
    return user


def require_role(role: Role) -> Callable[..., User]:
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role != role:
            raise HTTPException(status_code=403, detail="This page belongs to the other role.")
        return user

    return dependency


require_applicant = require_role(Role.APPLICANT)
require_specialist = require_role(Role.SPECIALIST)
