"""Case management (SPEC §7): numbering, status transitions, timeline, wallets."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record
from app.db import iso, utcnow
from app.errors import AppError, Conflict, NotFound
from app.models import Analysis, Case, CaseEvent, CaseWallet, LawfulRequest
from app.models.cases import CASE_STATUSES

CASE_PREFIX = "RT"

# Allowed manual/automatic transitions. Forward steps follow the SPEC §7 order; a case
# may also go back to ANALYZING (new wallet added) or REVIEW (request withdrawn), and
# any open case may be CLOSED.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "NEW": ("ANALYZING", "REVIEW", "CLOSED"),
    "ANALYZING": ("REVIEW", "CLOSED"),
    "REVIEW": ("ANALYZING", "REQUEST_DRAFTED", "CLOSED"),
    "REQUEST_DRAFTED": ("ANALYZING", "REVIEW", "REQUEST_APPROVED", "CLOSED"),
    "REQUEST_APPROVED": ("REQUEST_DRAFTED", "DISPATCHED_SIMULATED", "CLOSED"),
    "DISPATCHED_SIMULATED": ("REQUEST_DRAFTED", "RESPONSE_RECEIVED", "CLOSED"),
    "RESPONSE_RECEIVED": ("ANALYZING", "REQUEST_DRAFTED", "CLOSED"),
    "CLOSED": ("REVIEW",),
}


def next_case_no(db: Session, when: datetime | None = None) -> str:
    """RT-YYYY-NNNNNN, sequential within the year."""
    year = (when or utcnow()).year
    prefix = f"{CASE_PREFIX}-{year}-"
    last = db.execute(
        select(Case.case_no).where(Case.case_no.like(f"{prefix}%")).order_by(Case.case_no.desc()).limit(1)
    ).scalar_one_or_none()
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:06d}"


def add_event(db: Session, case: Case, actor: str, kind: str, message: str, data: dict[str, Any] | None = None) -> CaseEvent:
    ev = CaseEvent(case_id=case.id, actor=actor, kind=kind, message=message, data=data or {})
    db.add(ev)
    return ev


def can_transition(old: str, new: str) -> bool:
    return old == new or new in TRANSITIONS.get(old, ())


def set_status(db: Session, case: Case, new: str, actor: str, reason: str = "", force: bool = False) -> None:
    if new not in CASE_STATUSES:
        raise AppError(f"Unknown case status {new!r}.", code="invalid_status")
    old = case.status
    if old == new:
        return
    if not force and not can_transition(old, new):
        raise Conflict(f"A case cannot move from {old} to {new}.", code="invalid_transition")
    case.status = new
    case.updated_at = utcnow()
    add_event(db, case, actor, "status", f"Status {old} → {new}" + (f": {reason}" if reason else ""), {"from": old, "to": new})
    record(db, actor, "case_status", "case", case.id, {"from": old, "to": new, "reason": reason}, commit=False)


def create_case(db: Session, actor: str, **fields: Any) -> Case:
    case = Case(case_no=next_case_no(db), created_by=actor, status="NEW", **fields)
    db.add(case)
    db.flush()
    add_event(db, case, actor, "created", f"Case {case.case_no} created" + (f" ({case.source})" if case.source != "manual" else ""))
    record(db, actor, "case_created", "case", case.id, {"case_no": case.case_no, "source": case.source}, commit=False)
    return case


def get_case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise NotFound("Case not found.")
    return case


def attach_wallet(
    db: Session, case: Case, address: str, chain_hint: str | None, actor: str, complaint_ref: str | None = None, analysis_id: str | None = None
) -> CaseWallet:
    existing = db.execute(
        select(CaseWallet).where(CaseWallet.case_id == case.id, CaseWallet.address == address)
    ).scalars().first()
    if existing is not None:
        if analysis_id:
            existing.analysis_id = analysis_id
        return existing
    w = CaseWallet(case_id=case.id, address=address, chain_hint=chain_hint, complaint_ref=complaint_ref, analysis_id=analysis_id, added_by=actor)
    db.add(w)
    add_event(db, case, actor, "wallet_added", f"Wallet {address} added" + (f" ({chain_hint})" if chain_hint else ""), {"address": address})
    return w


def mark_analyzing(db: Session, case: Case, actor: str) -> None:
    if case.status != "ANALYZING" and can_transition(case.status, "ANALYZING"):
        set_status(db, case, "ANALYZING", actor, "analysis started")


def case_summary(db: Session, case: Case) -> dict[str, Any]:
    wallets = len(case.wallets)
    analyses = db.execute(select(func.count()).select_from(Analysis).where(Analysis.case_id == case.id)).scalar_one()
    requests = db.execute(select(func.count()).select_from(LawfulRequest).where(LawfulRequest.case_id == case.id)).scalar_one()
    return {
        "id": case.id,
        "case_no": case.case_no,
        "title": case.title,
        "complaint_ref": case.complaint_ref,
        "fir_no": case.fir_no,
        "agency": case.agency,
        "assigned_officer": case.assigned_officer,
        "crime_type": case.crime_type,
        "status": case.status,
        "source": case.source,
        "sahyog_ref": case.sahyog_ref,
        "window_days": case.window_days,
        "created_by": case.created_by,
        "created_at": iso(case.created_at),
        "updated_at": iso(case.updated_at),
        "wallet_count": wallets,
        "analysis_count": analyses,
        "request_count": requests,
    }
