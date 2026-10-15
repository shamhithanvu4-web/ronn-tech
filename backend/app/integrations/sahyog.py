"""SAHYOG adapter — SIMULATED (SPEC §9).

SAHYOG has no public API. This module is a documented stand-in so the end-to-end flow
can be demonstrated:

* Inbound: POST /api/integrations/sahyog/cases with a JSON body
      {sahyog_ref, agency, officer, complaint_ref, wallets: [{address, chain_hint}]}
  authenticated by header  X-Signature: <hex HMAC-SHA256(raw body, SAHYOG_SHARED_SECRET)>
  (an optional "sha256=" prefix is accepted). It creates a case and starts one
  analysis per valid wallet automatically.
* Outbound: an approved lawful request is written to the sahyog_outbox table as
  "Queued to SAHYOG (simulated)". Nothing leaves this server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.audit import record
from app.cases.service import add_event, attach_wallet, create_case, mark_analyzing
from app.config import get_settings
from app.db import session_scope
from app.errors import AppError, AuthError
from app.models import Case, SahyogInbound
from app.util import sha256_hex

ACTOR = "sahyog-simulated"
SIMULATED_NOTE = "Simulated SAHYOG adapter: SAHYOG has no public API."


class InboundWallet(BaseModel):
    address: str = Field(min_length=1, max_length=128)
    chain_hint: str | None = Field(default=None, max_length=24)


class InboundCase(BaseModel):
    sahyog_ref: str = Field(min_length=1, max_length=64)
    agency: str = Field(min_length=1, max_length=128)
    officer: str = Field(min_length=1, max_length=128)
    complaint_ref: str | None = Field(default=None, max_length=64)
    wallets: list[InboundWallet] = Field(min_length=1, max_length=100)


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, header: str | None) -> None:
    secret = get_settings().SAHYOG_SHARED_SECRET
    if not secret:
        raise AppError("SAHYOG_SHARED_SECRET is not configured on this server.", code="sahyog_not_configured", status_code=503)
    if not header:
        raise AuthError("Missing X-Signature header.", code="signature_missing")
    given = header.strip()
    if given.lower().startswith("sha256="):
        given = given[7:]
    expected = sign(body, secret)
    if not hmac.compare_digest(given.lower(), expected):
        raise AuthError("X-Signature does not match the request body.", code="signature_invalid")


def parse(body: bytes) -> InboundCase:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError("Body is not valid JSON.", code="invalid_json") from exc
    try:
        return InboundCase.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", []))
        raise AppError(f"{loc}: {first.get('msg')}", code="validation_error", status_code=422) from exc


def receive(body: bytes, signature: str | None) -> dict[str, Any]:
    """Verify, store and act on one inbound SAHYOG case. Idempotent on sahyog_ref."""
    from app.engine import jobs
    from app.engine.validate import validate_address
    from app.errors import InvalidAddress

    verify_signature(body, signature)
    msg = parse(body)
    body_hash = sha256_hex(body)
    with session_scope() as db:
        existing = db.execute(select(Case).where(Case.sahyog_ref == msg.sahyog_ref)).scalars().first()
        if existing is not None:
            return {"status": "duplicate", "case_id": existing.id, "case_no": existing.case_no, "analyses": [], "rejected": [], "simulated": True}

    accepted: list[tuple[str, str | None]] = []
    rejected: list[dict[str, str]] = []
    for w in msg.wallets:
        hint = (w.chain_hint or "").strip().lower() or None
        try:
            info = validate_address(w.address, hint)
            accepted.append((info.address, hint))
        except InvalidAddress as exc:
            rejected.append({"address": w.address, "chain_hint": hint or "", "reason": exc.detail})

    with session_scope() as db:
        case = create_case(
            db,
            ACTOR,
            title=f"SAHYOG {msg.sahyog_ref} · {msg.agency}",
            complaint_ref=msg.complaint_ref,
            agency=msg.agency,
            assigned_officer=msg.officer,
            crime_type="other",
            source="sahyog_simulated",
            sahyog_ref=msg.sahyog_ref,
        )
        case_id, case_no = case.id, case.case_no
        db.add(SahyogInbound(sahyog_ref=msg.sahyog_ref, payload=json.loads(body), body_sha256=body_hash, case_id=case_id, note=SIMULATED_NOTE))
        add_event(db, case, ACTOR, "sahyog_inbound", f"Case received from SAHYOG (simulated), ref {msg.sahyog_ref}, {len(msg.wallets)} wallet(s)", {"body_sha256": body_hash})
        for r in rejected:
            add_event(db, case, ACTOR, "wallet_rejected", f"Wallet {r['address']} rejected: {r['reason']}", r)
        record(db, ACTOR, "sahyog_inbound", "case", case_id, {"sahyog_ref": msg.sahyog_ref, "body_sha256": body_hash, "wallets": len(msg.wallets), "rejected": len(rejected), "simulated": True}, commit=False)

    analyses = []
    for address, hint in accepted:
        aid = jobs.create_analysis(address, ACTOR, chain=hint, case_id=case_id)
        with session_scope() as db:
            case = db.get(Case, case_id)
            attach_wallet(db, case, address, hint, ACTOR, complaint_ref=msg.complaint_ref, analysis_id=aid)
            mark_analyzing(db, case, ACTOR)
        jobs.submit(aid)
        analyses.append({"analysis_id": aid, "address": address, "chain_hint": hint})
    return {"status": "accepted", "case_id": case_id, "case_no": case_no, "analyses": analyses, "rejected": rejected, "simulated": True}
