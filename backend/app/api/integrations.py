"""SAHYOG integration endpoints — SIMULATED (SPEC §9). The inbound endpoint is not
Bearer-authenticated; it is verified by an HMAC-SHA256 X-Signature over the raw body."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db, iso
from app.integrations import sahyog
from app.models import Outbox, SahyogInbound, User
from app.security import get_current_user

router = APIRouter(prefix="/integrations/sahyog", tags=["integrations"])


@router.post("/cases", status_code=202)
async def inbound_case(request: Request) -> dict[str, Any]:
    import asyncio

    body = await request.body()
    return await asyncio.to_thread(sahyog.receive, body, request.headers.get("X-Signature"))


@router.get("/outbox")
def outbox(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(Outbox).order_by(Outbox.id.desc()).limit(100)).scalars()
    return {
        "simulated": True,
        "items": [
            {"id": o.id, "request_id": o.request_id, "status": o.status, "payload_sha256": o.payload_sha256, "created_at": iso(o.created_at), "payload": o.payload}
            for o in rows
        ],
    }


@router.get("/inbound")
def inbound_log(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    rows = db.execute(select(SahyogInbound).order_by(SahyogInbound.id.desc()).limit(100)).scalars()
    return {
        "simulated": True,
        "items": [
            {"id": r.id, "sahyog_ref": r.sahyog_ref, "case_id": r.case_id, "body_sha256": r.body_sha256, "received_at": iso(r.received_at), "note": r.note}
            for r in rows
        ],
    }
