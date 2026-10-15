from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record
from app.db import get_db
from app.errors import AuthError
from app.models import User
from app.security import create_token, get_current_user, login_limiter, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


def user_dict(user: User) -> dict:
    return {"id": user.id, "username": user.username, "full_name": user.full_name, "role": user.role}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else "unknown"
    login_limiter.check(body.username, ip)
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        login_limiter.fail(body.username, ip)
        record(db, body.username, "login_failed", "user", "", {"ip": ip})
        raise AuthError("Incorrect username or password.", code="invalid_credentials")
    login_limiter.success(body.username, ip)
    token, expires_in = create_token(user)
    record(db, user.username, "login", "user", user.id, {"ip": ip})
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "user": user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return user_dict(user)
