"""Lawful requests: view, approve (two-person rule), dispatch (simulated), PDF (SPEC §9)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record
from app.db import get_db
from app.lawful_requests import service
from app.models import LawfulRequest, User
from app.schemas import RequestAction
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("")
def list_requests(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    stmt = select(LawfulRequest).order_by(LawfulRequest.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(LawfulRequest.status == status.upper())
    return {"items": [service.request_dict(db, r) for r in db.execute(stmt).scalars()]}


@router.get("/{request_id}")
def get_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    return service.request_dict(db, service.get_request(db, request_id))


@router.post("/{request_id}/approve")
def approve(
    request_id: int, body: RequestAction | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("SUPERVISOR"))
) -> dict[str, Any]:
    r = service.approve(db, request_id, user, (body.note if body else "") or "")
    return service.request_dict(db, r)


@router.post("/{request_id}/dispatch")
def dispatch(
    request_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("SUPERVISOR", "ANALYST", "ADMIN"))
) -> dict[str, Any]:
    r, _ob = service.dispatch(db, request_id, user)
    return service.request_dict(db, r)


@router.post("/{request_id}/response")
def response_received(
    request_id: int, body: RequestAction | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict[str, Any]:
    r = service.mark_response(db, request_id, user, (body.note if body else "") or "")
    return service.request_dict(db, r)


@router.get("/{request_id}/pdf")
def request_pdf(request_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    pdf, digest = service.request_pdf(db, request_id, user.username)
    record(db, user.username, "report_downloaded", "request", request_id, {"format": "pdf", "content_sha256": digest})
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="request-{request_id}.pdf"',
            "X-Report-SHA256": digest,
            "Access-Control-Expose-Headers": "X-Report-SHA256, Content-Disposition",
        },
    )
