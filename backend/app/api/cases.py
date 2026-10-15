"""Cases, wallets, CSV batch import and request drafting (SPEC §7, §9, §11)."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.analyses import analysis_brief
from app.audit import record
from app.cases.service import add_event, attach_wallet, can_transition, case_summary, create_case, get_case, mark_analyzing, set_status
from app.db import get_db, iso, session_scope
from app.engine import jobs
from app.errors import AppError, Conflict
from app.lawful_requests import service as requests_service
from app.models import Analysis, Case, CaseEvent, CaseWallet, LawfulRequest, User
from app.models.cases import CRIME_TYPES
from app.schemas import CaseIn, CasePatch, CaseWalletIn, RequestIn
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/cases", tags=["cases"])
MAX_IMPORT_ROWS = 500
MAX_IMPORT_BYTES = 2 * 1024 * 1024


@router.get("")
def list_cases(
    status: str | None = None,
    open: bool | None = None,  # noqa: A002 - query parameter name
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    stmt = select(Case).order_by(Case.updated_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Case.status == status.upper())
    if open:
        stmt = stmt.where(Case.status != "CLOSED")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Case.case_no.ilike(like), Case.title.ilike(like), Case.complaint_ref.ilike(like), Case.fir_no.ilike(like)))
    return {"items": [case_summary(db, c) for c in db.execute(stmt).scalars()], "crime_types": list(CRIME_TYPES)}


@router.post("", status_code=201)
def new_case(body: CaseIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    case = create_case(db, user.username, source="manual", **body.model_dump())
    db.commit()
    return case_summary(db, case)


@router.get("/{case_id}")
def case_detail(case_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    case = get_case(db, case_id)
    analyses = db.execute(select(Analysis).where(Analysis.case_id == case.id).order_by(Analysis.created_at.desc())).scalars().all()
    by_id = {a.id: a for a in analyses}
    reqs = db.execute(select(LawfulRequest).where(LawfulRequest.case_id == case.id).order_by(LawfulRequest.id.desc())).scalars().all()
    events = db.execute(select(CaseEvent).where(CaseEvent.case_id == case.id).order_by(CaseEvent.ts.desc(), CaseEvent.id.desc())).scalars().all()
    out = case_summary(db, case)
    out["wallets"] = [
        {
            "id": w.id,
            "address": w.address,
            "chain_hint": w.chain_hint,
            "complaint_ref": w.complaint_ref,
            "analysis_id": w.analysis_id,
            "analysis": analysis_brief(by_id[w.analysis_id]) if w.analysis_id in by_id else None,
            "added_by": w.added_by,
            "added_at": iso(w.added_at),
        }
        for w in case.wallets
    ]
    out["analyses"] = [analysis_brief(a) for a in analyses]
    out["requests"] = [
        {
            "id": r.id,
            "request_no": r.request_no,
            "request_type": r.request_type,
            "status": r.status,
            "entity_name": r.entity_name,
            "confidence": r.confidence,
            "routing": r.routing,
            "drafted_by": r.drafted_by,
            "drafted_at": iso(r.drafted_at),
        }
        for r in reqs
    ]
    out["timeline"] = [{"id": e.id, "ts": iso(e.ts), "actor": e.actor, "kind": e.kind, "message": e.message, "data": e.data} for e in events]
    out["allowed_status"] = [s for s in ("NEW", "ANALYZING", "REVIEW", "REQUEST_DRAFTED", "REQUEST_APPROVED", "DISPATCHED_SIMULATED", "RESPONSE_RECEIVED", "CLOSED") if s != case.status and can_transition(case.status, s)]
    return out


@router.patch("/{case_id}")
def update_case(case_id: int, body: CasePatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    case = get_case(db, case_id)
    fields = body.model_dump(exclude_unset=True)
    status = fields.pop("status", None)
    note = fields.pop("note", None)
    changed = {}
    for k, v in fields.items():
        if getattr(case, k) != v:
            changed[k] = {"from": getattr(case, k), "to": v}
            setattr(case, k, v)
    if changed:
        add_event(db, case, user.username, "updated", "Updated " + ", ".join(changed), {"changes": changed})
        record(db, user.username, "case_updated", "case", case.id, {"changes": list(changed)}, commit=False)
    if status and status != case.status:
        set_status(db, case, status, user.username, note or "")
    elif note:
        add_event(db, case, user.username, "note", note)
    db.commit()
    return case_summary(db, case)


@router.post("/{case_id}/wallets", status_code=201)
def add_wallet(case_id: int, body: CaseWalletIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    from app.engine.validate import validate_address

    case = get_case(db, case_id)
    if case.status == "CLOSED":
        raise Conflict("The case is closed; reopen it before adding wallets.", code="case_closed")
    hint = (body.chain_hint or "").strip().lower() or None
    info = validate_address(body.address, hint)
    analysis_id = body.analysis_id
    if analysis_id:
        a = db.get(Analysis, analysis_id)
        if a is None:
            raise AppError("Analysis not found.", code="not_found", status_code=404)
        if a.address != info.address:
            raise AppError("The analysis is for a different address.", code="address_mismatch")
        if a.case_id is None:
            a.case_id = case.id
        add_event(db, case, user.username, "analysis_linked", f"Analysis of {a.address} linked", {"analysis_id": a.id})
    w = attach_wallet(db, case, info.address, hint, user.username, complaint_ref=body.complaint_ref, analysis_id=analysis_id)
    record(db, user.username, "case_wallet_added", "case", case.id, {"address": info.address, "analysis_id": analysis_id}, commit=False)
    db.commit()
    started = None
    if body.analyse and not analysis_id:
        started = jobs.create_analysis(info.address, user.username, chain=hint, case_id=case.id)
        with session_scope() as s2:
            c2 = s2.get(Case, case.id)
            w2 = s2.get(CaseWallet, w.id)
            w2.analysis_id = started
            mark_analyzing(s2, c2, user.username)
        jobs.submit(started)
    return {"id": w.id, "address": info.address, "chain_hint": hint, "analysis_id": started or analysis_id}


@router.post("/import", status_code=201)
async def import_csv(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    crime_type: str = Form(default="other"),
    agency: str = Form(default=""),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """CSV with columns address, chain_hint, complaint_ref → one case, one analysis per wallet.
    Analyses run on the background queue with concurrency ANALYSIS_WORKERS (default 2)."""
    from app.engine.validate import validate_address
    from app.errors import InvalidAddress

    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise AppError("The CSV is larger than 2 MB.", code="upload_too_large", status_code=413)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("The CSV must be UTF-8 encoded.", code="invalid_csv") from exc
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip().lower() for c in (reader.fieldnames or [])}
    if "address" not in cols:
        raise AppError("The CSV needs a header row with columns: address, chain_hint, complaint_ref.", code="invalid_csv")
    crime = (crime_type or "other").strip().lower()
    if crime not in CRIME_TYPES:
        raise AppError(f"crime_type must be one of: {', '.join(CRIME_TYPES)}", code="validation_error", status_code=422)
    rows, rejected, seen = [], [], set()
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        addr = row.get("address", "")
        if not addr:
            continue
        hint = row.get("chain_hint", "").lower() or None
        try:
            info = validate_address(addr, hint)
        except InvalidAddress as exc:
            rejected.append({"line": i, "address": addr, "reason": exc.detail})
            continue
        if (info.address, hint) in seen:
            rejected.append({"line": i, "address": addr, "reason": "duplicate row"})
            continue
        seen.add((info.address, hint))
        rows.append((info.address, hint, row.get("complaint_ref") or None))
        if len(rows) > MAX_IMPORT_ROWS:
            raise AppError(f"At most {MAX_IMPORT_ROWS} wallets per import.", code="too_many_rows")
    if not rows:
        raise AppError("No valid wallet rows in the CSV.", code="no_valid_rows")
    refs = sorted({r[2] for r in rows if r[2]})
    with session_scope() as db:
        case = create_case(
            db,
            user.username,
            title=title.strip() or f"Batch import ({len(rows)} wallets)",
            complaint_ref=refs[0] if len(refs) == 1 else None,
            agency=agency.strip() or None,
            crime_type=crime,
            source="batch_import",
        )
        case_id, case_no = case.id, case.case_no
        add_event(db, case, user.username, "batch_import", f"Imported {len(rows)} wallet(s) from {file.filename or 'CSV'}; {len(rejected)} row(s) rejected", {"rejected": rejected[:50]})
        record(db, user.username, "case_import", "case", case_id, {"rows": len(rows), "rejected": len(rejected)}, commit=False)
    ids = []
    for address, hint, ref in rows:
        aid = jobs.create_analysis(address, user.username, chain=hint, case_id=case_id)
        with session_scope() as db:
            c = db.get(Case, case_id)
            attach_wallet(db, c, address, hint, user.username, complaint_ref=ref, analysis_id=aid)
            mark_analyzing(db, c, user.username)
        ids.append(aid)
    for aid in ids:
        jobs.submit(aid)
    return {"case_id": case_id, "case_no": case_no, "analyses": ids, "rejected": rejected}


@router.post("/{case_id}/requests", status_code=201)
def draft_request(
    case_id: int, body: RequestIn, db: Session = Depends(get_db), user: User = Depends(require_roles("ANALYST", "ADMIN"))
) -> dict[str, Any]:
    r = requests_service.draft(db, case_id, body.analysis_id, body.request_type, body.legal_basis_id, body.notes, user, entity_id=body.entity_id)
    return requests_service.request_dict(db, r)
