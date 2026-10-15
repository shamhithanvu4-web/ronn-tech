"""Analyses: start, poll, graph, and investigation-ready reports (SPEC §11)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record
from app.cases.service import attach_wallet, get_case, mark_analyzing
from app.db import get_db, iso, session_scope
from app.engine import jobs
from app.engine.graph_builder import to_cytoscape
from app.errors import Conflict, NotFound
from app.models import Analysis, Entity, User
from app.reports.service import analysis_hash_valid, analysis_json, analysis_pdf
from app.schemas import AnalysisIn
from app.security import get_current_user

router = APIRouter(prefix="/analyses", tags=["analyses"])


def entity_brief(e: Entity | None) -> dict[str, Any] | None:
    if e is None:
        return None
    return {
        "id": e.id,
        "name": e.name,
        "entity_type": e.entity_type,
        "is_vasp": e.is_vasp,
        "jurisdiction": e.jurisdiction,
        "fiu_ind_registered": e.fiu_ind_registered,
        "sahyog_onboarded": e.sahyog_onboarded,
        "le_request_channel": e.le_request_channel,
        "verification_status": e.verification_status,
        "verified_source_url": e.verified_source_url,
        "last_verified_at": iso(e.last_verified_at),
    }


def analysis_brief(a: Analysis) -> dict[str, Any]:
    runtime = None
    if a.started_at and a.finished_at:
        runtime = round((a.finished_at - a.started_at).total_seconds(), 1)
    return {
        "id": a.id,
        "address": a.address,
        "chain": a.chain,
        "chain_family": a.chain_family,
        "status": a.status,
        "stage": a.stage,
        "progress": a.progress,
        "partial": a.partial,
        "error": a.error,
        "error_code": a.error_code,
        "nearest_entity_id": a.nearest_entity_id,
        "nearest_entity_name": a.nearest_entity_name,
        "nearest_confidence": a.nearest_confidence,
        "nearest_hops": a.nearest_hops,
        "risk_level": a.risk_level,
        "case_id": a.case_id,
        "created_by": a.created_by,
        "created_at": iso(a.created_at),
        "started_at": iso(a.started_at),
        "finished_at": iso(a.finished_at),
        "runtime_seconds": runtime,
        "params": a.params or {},
    }


def _get(db: Session, analysis_id: str) -> Analysis:
    a = db.get(Analysis, analysis_id)
    if a is None:
        raise NotFound("Analysis not found.")
    return a


@router.post("", status_code=202)
def start_analysis(body: AnalysisIn, user: User = Depends(get_current_user)) -> dict[str, Any]:
    if body.case_id is not None:
        with session_scope() as db:
            case = get_case(db, body.case_id)
            if case.status == "CLOSED":
                raise Conflict("The case is closed; reopen it before adding analyses.", code="case_closed")
    aid = jobs.create_analysis(
        body.address,
        user.username,
        chain=body.chain,
        case_id=body.case_id,
        max_hops=body.max_hops,
        include_funding=body.include_funding,
        window_days=body.window_days,
    )
    if body.case_id is not None:
        with session_scope() as db:
            case = get_case(db, body.case_id)
            a = db.get(Analysis, aid)
            attach_wallet(db, case, a.address, body.chain, user.username, analysis_id=aid)
            mark_analyzing(db, case, user.username)
    jobs.submit(aid)
    return {"id": aid, "status": "queued"}


@router.get("")
def list_analyses(
    case_id: int | None = None,
    address: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    q = select(Analysis).order_by(Analysis.created_at.desc()).limit(limit)
    if case_id is not None:
        q = q.where(Analysis.case_id == case_id)
    if address:
        q = q.where(Analysis.address == address.strip())
    return {"items": [analysis_brief(a) for a in db.execute(q).scalars()]}


@router.get("/{analysis_id}")
def get_analysis(analysis_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    a = _get(db, analysis_id)
    out = analysis_brief(a)
    if a.status == "done" and a.result:
        result = {k: v for k, v in a.result.items() if k != "graph"}
        g = a.result.get("graph") or {}
        result["graph_stats"] = {"nodes": len(g.get("nodes", [])), "edges": len(g.get("edges", []))}
        out["result"] = result
        out["result_sha256"] = a.result_sha256
        out["result_hash_valid"] = analysis_hash_valid(a)
        ids = {c["entity_id"] for c in (a.result.get("candidates") or [])[:3]}
        if a.nearest_entity_id:
            ids.add(a.nearest_entity_id)
        out["entities"] = {str(i): entity_brief(db.get(Entity, i)) for i in ids}
    return out


@router.get("/{analysis_id}/graph")
def get_graph(analysis_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    a = _get(db, analysis_id)
    if a.status != "done" or not a.result:
        raise Conflict("The analysis has not finished.", code="analysis_not_done")
    graph = a.result.get("graph") or {"nodes": [], "edges": []}
    out = to_cytoscape(graph)
    out["nodes"] = {n["id"]: n for n in graph.get("nodes", [])}
    out["edges"] = {e["id"]: e for e in graph.get("edges", [])}
    return out


@router.get("/{analysis_id}/report.pdf")
def report_pdf(analysis_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    pdf, digest = analysis_pdf(db, analysis_id, user.username)
    record(db, user.username, "report_downloaded", "analysis", analysis_id, {"format": "pdf", "content_sha256": digest})
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="analysis-{analysis_id[:8]}.pdf"',
            "X-Report-SHA256": digest,
            "Access-Control-Expose-Headers": "X-Report-SHA256, Content-Disposition",
        },
    )


@router.get("/{analysis_id}/report.json")
def report_json(analysis_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    body, digest = analysis_json(db, analysis_id, user.username)
    record(db, user.username, "report_downloaded", "analysis", analysis_id, {"format": "json", "content_sha256": digest})
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="analysis-{analysis_id[:8]}.json"',
            "X-Report-SHA256": digest,
            "Access-Control-Expose-Headers": "X-Report-SHA256, Content-Disposition",
        },
    )
