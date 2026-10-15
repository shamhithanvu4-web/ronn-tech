"""Authentication, roles and login rate limiting (SPEC §8)."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import bcrypt
import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AppError, AuthError, Forbidden
from app.models import User

JWT_ALG = "HS256"
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError("Password is longer than bcrypt's 72-byte limit")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except ValueError:
        return False


def create_token(user: User) -> tuple[str, int]:
    settings = get_settings()
    ttl = int(settings.JWT_TTL_MINUTES)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "role": user.role,
        "uid": user.id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG), ttl * 60


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired. Please sign in again.", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid authentication token.", code="token_invalid") from exc


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise AuthError("Missing bearer token.", code="token_missing")
    claims = decode_token(header.split(" ", 1)[1].strip())
    user = db.execute(select(User).where(User.username == claims.get("sub"))).scalar_one_or_none()
    if user is None or not user.active:
        raise AuthError("User is not active.", code="user_inactive")
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise Forbidden(f"This action requires role {' or '.join(roles)}.", code="role_required")
        return user

    return _dep


class LoginRateLimiter:
    """At most `max_attempts` failed logins per (username, ip) per `window` seconds."""

    def __init__(self, max_attempts: int = 5, window: int = 300):
        self.max_attempts = max_attempts
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _key(self, username: str, ip: str) -> str:
        return f"{username.lower()}|{ip}"

    def check(self, username: str, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = [t for t in self._failures.get(self._key(username, ip), []) if now - t < self.window]
            self._failures[self._key(username, ip)] = attempts
            if len(attempts) >= self.max_attempts:
                retry = int(self.window - (now - attempts[0])) + 1
                raise AppError(
                    f"Too many failed sign-in attempts. Try again in {retry} seconds.",
                    code="login_rate_limited",
                    status_code=429,
                )

    def fail(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.setdefault(self._key(username, ip), []).append(time.monotonic())

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username, ip), None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()
