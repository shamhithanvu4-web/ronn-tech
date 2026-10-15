"""Background analysis jobs: a small thread pool (concurrency ANALYSIS_WORKERS, default 2).

Each job runs the async pipeline in its own event loop, records progress on the
analysis row, then stores the result, its SHA-256, the raw-response evidence index
and any tier-C inferred labels, and writes an audit entry.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from sqlalchemy import select

from app.audit import record
from app.config import get_settings, scoring_config
from app.db import session_scope, utcnow
from app.engine.datasource import ProductionLabels, ProviderDataSource
from app.engine.pipeline import analyse
from app.errors import AppError
from app.labels.store import persist_runtime_label
from app.models import Analysis, Case, CaseEvent, RawResponse
from app.providers import recorder
from app.providers.context import AnalysisContext, use_context
from app.util import canonical_json, sha256_hex

log = logging.getLogger("ronntech.jobs")
STAGE_PROGRESS = {"queued": 0, "validating": 5, "fetching": 15, "building_graph": 35, "inferring_deposits": 70, "scoring": 85, "done": 100}

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max(1, int(get_settings().ANALYSIS_WORKERS)), thread_name_prefix="analysis")
        return _executor


def shutdown() -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


def create_analysis(
    address: str,
    created_by: str,
    chain: str | None = None,
    case_id: int | None = None,
    max_hops: int | None = None,
    include_funding: bool = True,
    window_days: int | None = None,
    window_start: int | None = None,
) -> str:
    from app.engine.validate import validate_address

    info = validate_address(address, chain)  # raises InvalidAddress (HTTP 400)
    analysis_id = str(uuid.uuid4())
    with session_scope() as db:
        db.add(
            Analysis(
                id=analysis_id,
                address=info.address,
                chain=chain,
                chain_family=info.family,
                status="queued",
                stage="queued",
                progress=0,
                params={
                    "max_hops": max_hops,
                    "include_funding": include_funding,
                    "window_days": window_days,
                    "window_start": window_start,
                    "chain": chain,
                },
                case_id=case_id,
                created_by=created_by,
            )
        )
        record(db, created_by, "analysis_requested", "analysis", analysis_id, {"address": info.address, "chain": chain, "case_id": case_id}, commit=False)
    return analysis_id


def submit(analysis_id: str) -> Future:
    return executor().submit(run_analysis, analysis_id)


class _Progress:
    def __init__(self, analysis_id: str):
        self.analysis_id = analysis_id
        self.last = 0.0
        self.stage = None

    def __call__(self, stage: str, pct: int) -> None:
        now = time.monotonic()
        if stage == self.stage and now - self.last < 0.5:
            return
        self.stage, self.last = stage, now
        with session_scope() as db:
            a = db.get(Analysis, self.analysis_id)
            if a is not None:
                a.stage = stage
                a.progress = max(a.progress or 0, int(pct))


def run_analysis(analysis_id: str, hidden: set[tuple[str, str]] | None = None, persist_inferred: bool = True, enable_live: bool = True) -> dict[str, Any] | None:
    with session_scope() as db:
        a = db.get(Analysis, analysis_id)
        if a is None:
            return None
        a.status, a.stage, a.started_at = "running", "validating", utcnow()
        params = dict(a.params or {})
        address = a.address
        actor = a.created_by
        case_id = a.case_id
        window_days = params.get("window_days")
        if case_id and not window_days:
            case = db.get(Case, case_id)
            window_days = case.window_days if case else None
    budget = int(scoring_config()["traversal"].get("api_call_budget", 600))
    ctx = AnalysisContext(
        analysis_id=analysis_id,
        api_call_budget=budget,
        evidence_dir=recorder.EVIDENCE_DIR / analysis_id,
        hidden_label_addresses=set(hidden or set()),
        use_inferred_labels=persist_inferred,
    )
    progress = _Progress(analysis_id)
    try:
        with session_scope() as ldb, use_context(ctx):
            labels = ProductionLabels(ldb, enable_live=enable_live)
            result = asyncio.run(
                analyse(
                    address,
                    ProviderDataSource(),
                    labels,
                    chain_hint=params.get("chain"),
                    max_hops=params.get("max_hops"),
                    include_funding=params.get("include_funding", True),
                    window_days=window_days,
                    window_start=params.get("window_start"),
                    progress=progress,
                    analysis_id=analysis_id,
                )
            )
    except AppError as exc:
        _fail(analysis_id, actor, exc.detail, exc.code)
        return None
    except Exception as exc:  # noqa: BLE001 - never leave an analysis stuck in "running"
        log.exception("analysis %s crashed", analysis_id)
        _fail(analysis_id, actor, f"Internal error: {type(exc).__name__}", "internal_error")
        return None
    _store(analysis_id, actor, result, persist_inferred)
    return result


def _fail(analysis_id: str, actor: str, detail: str, code: str) -> None:
    with session_scope() as db:
        a = db.get(Analysis, analysis_id)
        if a is None:
            return
        a.status, a.stage, a.error, a.error_code, a.finished_at = "failed", "failed", detail, code, utcnow()
        record(db, actor, "analysis_failed", "analysis", analysis_id, {"code": code, "detail": detail[:300]}, commit=False)
        if a.case_id:
            db.add(CaseEvent(case_id=a.case_id, actor="system", kind="analysis_failed", message=f"Analysis of {a.address} failed: {detail[:200]}", data={"analysis_id": analysis_id}))
    _update_case_status(analysis_id)


def _store(analysis_id: str, actor: str, result: dict[str, Any], persist_inferred: bool) -> None:
    digest = sha256_hex(canonical_json(result))
    with session_scope() as db:
        a = db.get(Analysis, analysis_id)
        if a is None:
            return
        nv = result["summary"]["nearest_vasp"]
        a.result = result
        a.result_sha256 = digest
        a.partial = bool(result["summary"]["partial"])
        a.nearest_entity_id = nv["entity_id"] if nv else None
        a.nearest_entity_name = nv["entity_name"] if nv else None
        a.nearest_confidence = nv["confidence"] if nv else None
        a.nearest_hops = nv["hops"] if nv else None
        a.risk_level = result["risk"]["level"]
        a.status, a.stage, a.progress, a.finished_at = "done", "done", 100, utcnow()
        for e in result["evidence"]["raw_responses"]:
            db.add(
                RawResponse(
                    analysis_id=analysis_id,
                    chain=e["chain"],
                    provider=e["provider"],
                    url=e["url"],
                    content_sha256=e["content_sha256"],
                    path=e["path"] or "",
                    from_cache=e["origin"],
                )
            )
        if persist_inferred:
            for dep in result["deposit_inferences"]:
                persist_runtime_label(
                    db, dep["chain"], dep["deposit_address"], dep["entity_id"], "DEPOSIT", "inferred_deposit",
                    dep["reliability"], "C", dep["evidence"] | {"corroborations": dep["corroborations"], "analysis_id": analysis_id},
                    created_by=actor,
                )
            for cl in result["cluster_inferences"]:
                persist_runtime_label(
                    db, cl["chain"], cl["address"], cl["entity_id"], "UNKNOWN", "inferred_btc_cluster",
                    cl["reliability"], "C", cl["evidence"] | {"analysis_id": analysis_id}, created_by=actor,
                )
        record(
            db, actor, "analysis_completed", "analysis", analysis_id,
            {"result_sha256": digest, "nearest": nv["entity_name"] if nv else None, "confidence": nv["confidence"] if nv else None,
             "risk": result["risk"]["level"], "partial": result["summary"]["partial"]},
            commit=False,
        )
        if a.case_id:
            msg = (
                f"Analysis of {a.address}: likely deposit destination {nv['entity_name']} (confidence {nv['confidence']:.0f}, {nv['tier']})"
                if nv else f"Analysis of {a.address}: no VASP at confidence >= 50"
            )
            db.add(CaseEvent(case_id=a.case_id, actor="system", kind="analysis_completed", message=msg, data={"analysis_id": analysis_id}))
    _update_case_status(analysis_id)


def _update_case_status(analysis_id: str) -> None:
    with session_scope() as db:
        a = db.get(Analysis, analysis_id)
        if a is None or not a.case_id:
            return
        case = db.get(Case, a.case_id)
        if case is None or case.status not in ("NEW", "ANALYZING"):
            return
        pending = db.execute(
            select(Analysis).where(Analysis.case_id == case.id, Analysis.status.in_(["queued", "running"]))
        ).scalars().first()
        if pending is None:
            case.status = "REVIEW"
            db.add(CaseEvent(case_id=case.id, actor="system", kind="status", message="All analyses finished; case ready for review", data={"status": "REVIEW"}))
