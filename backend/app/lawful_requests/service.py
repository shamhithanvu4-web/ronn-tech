"""Lawful requests (SPEC §9): draft → approve (a different SUPERVISOR) → dispatch to the
simulated SAHYOG outbox → response received.

Routing: the request goes to the nearest-VASP entity when its attribution confidence is
>= 50. If that entity's sahyog_onboarded is false or unknown, the request is marked
"Manual routing required". Legal bases come only from config/legal.yaml; disclosure
defaults to its configured default, a freeze has no default.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record
from app.cases.service import add_event, attach_wallet, get_case, set_status
from app.config import PRODUCT_NAME, load_yaml, scoring_config
from app.db import iso, utcnow
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import Analysis, Case, Entity, LawfulRequest, Outbox, ReportRecord, User
from app.util import canonical_json, dt_to_ist, sha256_hex

ROUTING_SAHYOG = "SAHYOG (simulated)"
ROUTING_MANUAL = "Manual routing required"
LEGAL_NOTE = "Legal provisions to be confirmed by the investigating officer."
DISCLAIMER = (
    "Attribution is probabilistic and is an investigative lead, not proof of ownership. The address above is the likely "
    "deposit destination identified from on-chain evidence; the VASP's own records are needed to establish who controls it."
)

ITEMS = {
    "INFORMATION_DISCLOSURE": [
        "KYC records of the account holder(s) associated with the deposit / receiving address",
        "Linked bank accounts and UPI IDs used for deposits or withdrawals",
        "Deposit and withdrawal history of the deposit address and the associated account(s)",
        "Login IP addresses and device identifiers for the associated account(s)",
        "Current balance held in the associated account(s)",
    ],
    "FREEZE_HOLD": [
        "Freeze / hold the virtual assets credited from the listed deposit address and the associated account(s)",
        "Confirm the amount and asset frozen, and the time the hold took effect",
        "Preserve KYC and transaction records of the associated account(s) pending legal process",
    ],
}


def legal_options(request_type: str) -> tuple[str | None, list[dict[str, Any]]]:
    cfg = load_yaml("legal.yaml").get(request_type) or {}
    return cfg.get("default"), list(cfg.get("options") or [])


def resolve_legal_basis(request_type: str, legal_basis_id: str | None) -> dict[str, Any]:
    default, options = legal_options(request_type)
    chosen = legal_basis_id or default
    if not chosen:
        raise AppError("A legal basis must be chosen for a freeze/hold request; there is no default.", code="legal_basis_required")
    for o in options:
        if o["id"] == chosen:
            return o
    raise AppError(f"Unknown legal basis {chosen!r} for {request_type}.", code="invalid_legal_basis")


def next_request_no(db: Session) -> str:
    prefix = f"RT-REQ-{utcnow().year}-"
    last = db.execute(
        select(LawfulRequest.request_no).where(LawfulRequest.request_no.like(f"{prefix}%")).order_by(LawfulRequest.request_no.desc()).limit(1)
    ).scalar_one_or_none()
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:06d}"


def _routable_candidate(result: dict[str, Any], entity_id: int | None) -> dict[str, Any]:
    min_conf = float(scoring_config().get("routable_min_confidence", 50))
    cands = result.get("candidates") or []
    nearest = (result.get("summary") or {}).get("nearest_vasp")
    if entity_id is None:
        if nearest is None:
            raise Conflict(
                f"No VASP reached attribution confidence {min_conf:.0f}; there is no routable recipient for a request.",
                code="no_routable_vasp",
            )
        entity_id = nearest["entity_id"]
    cand = next((c for c in cands if c["entity_id"] == entity_id), None)
    if cand is None or not cand["is_vasp"] or cand["confidence"] < min_conf:
        raise Conflict(
            f"That entity is not a VASP candidate with confidence >= {min_conf:.0f} in this analysis.", code="entity_not_routable"
        )
    return cand


def draft(
    db: Session,
    case_id: int,
    analysis_id: str,
    request_type: str,
    legal_basis_id: str | None,
    notes: str,
    actor: User,
    entity_id: int | None = None,
) -> LawfulRequest:
    case = get_case(db, case_id)
    if case.status == "CLOSED":
        raise Conflict("The case is closed.", code="case_closed")
    a = db.get(Analysis, analysis_id)
    if a is None:
        raise NotFound("Analysis not found.")
    if a.status != "done" or not a.result:
        raise Conflict("The analysis has not finished.", code="analysis_not_done")
    if a.case_id not in (None, case.id):
        raise Conflict("The analysis belongs to another case.", code="analysis_other_case")
    if a.case_id is None:
        a.case_id = case.id
        attach_wallet(db, case, a.address, a.chain, actor.username, analysis_id=a.id)
    cand = _routable_candidate(a.result, entity_id)
    basis = resolve_legal_basis(request_type, legal_basis_id)
    entity = db.get(Entity, cand["entity_id"])
    if entity is None:
        raise NotFound("The attributed entity no longer exists in the directory.")
    if entity.sahyog_onboarded is True:
        routing, reason = ROUTING_SAHYOG, f"{entity.name} is recorded as onboarded on SAHYOG (source: {entity.verified_source_url})."
    else:
        state = "not onboarded" if entity.sahyog_onboarded is False else "of unknown onboarding status"
        routing, reason = ROUTING_MANUAL, f"{entity.name} is {state} on SAHYOG in the VASP directory; route through the VASP's law-enforcement channel."
    best = cand["paths"][0]
    tx_hashes: list[str] = []
    for hop in best.get("hops_detail", []):
        tx_hashes += [t["tx_hash"] for t in hop.get("transfers", [])[:3]]
    ev = (best.get("label") or {}).get("evidence") or {}
    tx_hashes += list(ev.get("deposit_txs", []))[:3] + list(ev.get("sweep_txs", []))[:3]
    req = LawfulRequest(
        request_no=next_request_no(db),
        case_id=case.id,
        analysis_id=a.id,
        entity_id=entity.id,
        entity_name=entity.name,
        confidence=cand["confidence"],
        request_type=request_type,
        legal_basis_id=basis["id"],
        legal_basis_label=basis["label"],
        status="DRAFT",
        routing=routing,
        routing_reason=reason,
        payload={
            "suspect_address": a.address,
            "target_address": best["nodes"][-1],
            "chain": best["chain"],
            "confidence": cand["confidence"],
            "tier": cand["tier"],
            "via": best["via"],
            "hops": cand["min_hops"],
            "tx_hashes": list(dict.fromkeys(tx_hashes))[:12],
            "analysis_sha256": a.result_sha256,
            "items_requested": ITEMS[request_type],
            "legal_basis_source_url": basis.get("source_url"),
            "legal_basis_verification": basis.get("verification_status", "unverified"),
        },
        notes=notes or "",
        drafted_by=actor.username,
    )
    db.add(req)
    db.flush()
    if case.status != "REQUEST_DRAFTED":
        set_status(db, case, "REQUEST_DRAFTED", actor.username, f"request {req.request_no} drafted", force=True)
    add_event(db, case, actor.username, "request_drafted", f"{request_type.replace('_', ' ').title()} request {req.request_no} drafted for {entity.name} ({routing})", {"request_id": req.id})
    record(db, actor.username, "request_drafted", "request", req.id, {"request_no": req.request_no, "entity": entity.name, "routing": routing, "type": request_type}, commit=False)
    db.commit()
    return req


def get_request(db: Session, request_id: int) -> LawfulRequest:
    r = db.get(LawfulRequest, request_id)
    if r is None:
        raise NotFound("Request not found.")
    return r


def approve(db: Session, request_id: int, actor: User, note: str = "") -> LawfulRequest:
    r = get_request(db, request_id)
    if actor.role != "SUPERVISOR":
        raise Forbidden("Only a SUPERVISOR can approve a lawful request.", code="role_required")
    if r.status != "DRAFT":
        raise Conflict(f"Only a draft can be approved (status is {r.status}).", code="invalid_transition")
    if actor.username == r.drafted_by:
        raise Forbidden("Two-person rule: the approver must be a different person from the drafter.", code="same_person")
    r.status, r.approved_by, r.approved_at = "APPROVED", actor.username, utcnow()
    case = db.get(Case, r.case_id)
    set_status(db, case, "REQUEST_APPROVED", actor.username, f"request {r.request_no} approved", force=True)
    add_event(db, case, actor.username, "request_approved", f"Request {r.request_no} approved" + (f": {note}" if note else ""), {"request_id": r.id})
    record(db, actor.username, "request_approved", "request", r.id, {"request_no": r.request_no, "note": note}, commit=False)
    db.commit()
    return r


def outbound_payload(db: Session, r: LawfulRequest) -> dict[str, Any]:
    case = db.get(Case, r.case_id)
    entity = db.get(Entity, r.entity_id) if r.entity_id else None
    p = r.payload or {}
    return {
        "schema": "ronn-tech/sahyog-outbound/v1",
        "simulated": True,
        "sender": PRODUCT_NAME,
        "request_no": r.request_no,
        "request_type": r.request_type,
        "legal_basis": {"id": r.legal_basis_id, "label": r.legal_basis_label, "note": LEGAL_NOTE},
        "case": {
            "case_no": case.case_no,
            "complaint_ref": case.complaint_ref,
            "fir_no": case.fir_no,
            "agency": case.agency,
            "sahyog_ref": case.sahyog_ref,
        },
        "vasp": {
            "entity_id": r.entity_id,
            "name": r.entity_name,
            "le_request_channel": entity.le_request_channel if entity else None,
            "sahyog_onboarded": entity.sahyog_onboarded if entity else None,
        },
        "routing": r.routing,
        "subject": {
            "suspect_address": p.get("suspect_address"),
            "deposit_address": p.get("target_address"),
            "chain": p.get("chain"),
            "attribution_confidence": p.get("confidence"),
            "tier": p.get("tier"),
            "evidence_tx_hashes": p.get("tx_hashes", []),
            "analysis_sha256": p.get("analysis_sha256"),
        },
        "items_requested": p.get("items_requested", []),
        "approved_by": r.approved_by,
        "approved_at": iso(r.approved_at),
        "drafted_by": r.drafted_by,
        "notes": r.notes,
    }


def dispatch(db: Session, request_id: int, actor: User) -> tuple[LawfulRequest, Outbox]:
    r = get_request(db, request_id)
    if r.status != "APPROVED":
        raise Conflict(f"Only an approved request can be dispatched (status is {r.status}).", code="invalid_transition")
    payload = outbound_payload(db, r)
    status = "Queued to SAHYOG (simulated)"
    if r.routing == ROUTING_MANUAL:
        status = "Queued to SAHYOG (simulated) · manual routing required"
    ob = Outbox(request_id=r.id, payload=payload, payload_sha256=sha256_hex(canonical_json(payload)), status=status)
    db.add(ob)
    r.status, r.dispatched_by, r.dispatched_at = "DISPATCHED_SIMULATED", actor.username, utcnow()
    case = db.get(Case, r.case_id)
    set_status(db, case, "DISPATCHED_SIMULATED", actor.username, f"request {r.request_no} dispatched (simulated)", force=True)
    add_event(db, case, actor.username, "request_dispatched", f"Request {r.request_no}: {status}", {"request_id": r.id})
    record(db, actor.username, "request_dispatched", "request", r.id, {"request_no": r.request_no, "payload_sha256": ob.payload_sha256, "simulated": True}, commit=False)
    db.commit()
    return r, ob


def mark_response(db: Session, request_id: int, actor: User, note: str = "") -> LawfulRequest:
    r = get_request(db, request_id)
    if r.status != "DISPATCHED_SIMULATED":
        raise Conflict("Only a dispatched request can receive a response.", code="invalid_transition")
    r.status, r.response_received_at = "RESPONSE_RECEIVED", utcnow()
    case = db.get(Case, r.case_id)
    set_status(db, case, "RESPONSE_RECEIVED", actor.username, f"response to {r.request_no} recorded", force=True)
    add_event(db, case, actor.username, "response_received", f"Response to {r.request_no} recorded" + (f": {note}" if note else ""), {"request_id": r.id})
    record(db, actor.username, "request_response", "request", r.id, {"request_no": r.request_no, "note": note}, commit=False)
    db.commit()
    return r


def request_dict(db: Session, r: LawfulRequest) -> dict[str, Any]:
    case = db.get(Case, r.case_id)
    entity = db.get(Entity, r.entity_id) if r.entity_id else None
    outbox = db.execute(select(Outbox).where(Outbox.request_id == r.id).order_by(Outbox.id.desc())).scalars().first()
    return {
        "id": r.id,
        "request_no": r.request_no,
        "case_id": r.case_id,
        "case_no": case.case_no if case else None,
        "analysis_id": r.analysis_id,
        "entity_id": r.entity_id,
        "entity_name": r.entity_name,
        "entity": {
            "sahyog_onboarded": entity.sahyog_onboarded,
            "le_request_channel": entity.le_request_channel,
            "verification_status": entity.verification_status,
            "verified_source_url": entity.verified_source_url,
        }
        if entity
        else None,
        "confidence": r.confidence,
        "request_type": r.request_type,
        "legal_basis_id": r.legal_basis_id,
        "legal_basis_label": r.legal_basis_label,
        "legal_note": LEGAL_NOTE,
        "status": r.status,
        "routing": r.routing,
        "routing_reason": r.routing_reason,
        "payload": r.payload,
        "notes": r.notes,
        "drafted_by": r.drafted_by,
        "drafted_at": iso(r.drafted_at),
        "approved_by": r.approved_by,
        "approved_at": iso(r.approved_at),
        "dispatched_by": r.dispatched_by,
        "dispatched_at": iso(r.dispatched_at),
        "response_received_at": iso(r.response_received_at),
        "outbox": {
            "id": outbox.id,
            "status": outbox.status,
            "payload_sha256": outbox.payload_sha256,
            "created_at": iso(outbox.created_at),
            "payload": outbox.payload,
            "simulated": True,
        }
        if outbox
        else None,
    }


def request_content(db: Session, r: LawfulRequest) -> dict[str, Any]:
    case = db.get(Case, r.case_id)
    p = r.payload or {}
    return {
        "report_type": "lawful_request",
        "product": PRODUCT_NAME,
        "simulated": True,
        "request": {
            "request_no": r.request_no,
            "request_type": r.request_type,
            "status": r.status,
            "entity_name": r.entity_name,
            "routing": r.routing,
            "routing_reason": r.routing_reason,
            "legal_basis_label": r.legal_basis_label,
            "drafted_by": r.drafted_by,
            "drafted_at_ist": dt_to_ist(r.drafted_at),
            "approved_by": r.approved_by,
            "approved_at_ist": dt_to_ist(r.approved_at),
            "dispatched_at_ist": dt_to_ist(r.dispatched_at),
        },
        "case": {"case_no": case.case_no, "complaint_ref": case.complaint_ref, "fir_no": case.fir_no, "agency": case.agency},
        "evidence": {
            "suspect_address": p.get("suspect_address"),
            "target_address": p.get("target_address"),
            "chain": p.get("chain"),
            "confidence": p.get("confidence"),
            "tier": p.get("tier"),
            "via": p.get("via"),
            "analysis_id": r.analysis_id,
            "analysis_sha256": p.get("analysis_sha256"),
            "tx_hashes": p.get("tx_hashes", []),
        },
        "items_requested": p.get("items_requested", []),
        "notes": r.notes,
        "legal_note": LEGAL_NOTE,
        "disclaimer": DISCLAIMER,
    }


def request_pdf(db: Session, request_id: int, actor: str) -> tuple[bytes, str]:
    from app.reports.pdf import render_request_pdf

    r = get_request(db, request_id)
    content = request_content(db, r)
    digest = sha256_hex(canonical_json(content))
    pdf = render_request_pdf(content, digest)
    db.add(
        ReportRecord(
            kind="request", object_id=str(r.id), fmt="pdf", file_sha256=sha256_hex(pdf), content_sha256=digest,
            analysis_sha256=(r.payload or {}).get("analysis_sha256"), generated_by=actor,
        )
    )
    record(db, actor, "report_generated_pdf", "request", r.id, {"content_sha256": digest}, commit=False)
    db.commit()
    return pdf, digest
