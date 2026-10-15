"""Watchlist and alerts (SPEC §7, §11)."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record
from app.config import explorer_tx_url
from app.db import get_db, iso
from app.engine.validate import validate_address
from app.errors import AppError, NotFound
from app.models import Alert, Analysis, WatchItem, User
from app.schemas import WatchIn
from app.security import get_current_user
from app.util import now_unix

router = APIRouter(tags=["watchlist"])


def watch_dict(w: WatchItem, alerts: int = 0) -> dict[str, Any]:
    return {
        "id": w.id,
        "chain": w.chain,
        "address": w.address,
        "case_id": w.case_id,
        "analysis_id": w.analysis_id,
        "note": w.note,
        "created_by": w.created_by,
        "created_at": iso(w.created_at),
        "active": w.active,
        "baseline_time": w.baseline_time,
        "last_checked_at": iso(w.last_checked_at),
        "last_error": w.last_error,
        "alert_count": alerts,
    }


def alert_dict(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "watch_id": a.watch_id,
        "chain": a.chain,
        "address": a.address,
        "tx_hash": a.tx_hash,
        "explorer_url": explorer_tx_url(a.chain, a.tx_hash),
        "to_addr": a.to_addr,
        "asset": a.asset,
        "amount": a.amount,
        "block_time": a.block_time,
        "urgent": a.urgent,
        "vasp_entity_id": a.vasp_entity_id,
        "vasp_name": a.vasp_name,
        "message": a.message,
        "evidence": a.evidence,
        "acknowledged": a.acknowledged,
        "created_at": iso(a.created_at),
    }


@router.get("/watchlist")
def list_watch(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    counts = dict(db.execute(select(Alert.watch_id, func.count()).group_by(Alert.watch_id)).all())
    items = db.execute(select(WatchItem).where(WatchItem.active.is_(True)).order_by(WatchItem.created_at.desc())).scalars()
    return {"items": [watch_dict(w, counts.get(w.id, 0)) for w in items]}


@router.post("/watchlist", status_code=201)
def add_watch(body: WatchIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    chain = (body.chain or "").strip().lower() or None
    info = validate_address(body.address, chain)
    if chain is None:
        if len(info.chains) == 1:
            chain = info.chains[0]
        elif body.analysis_id:
            a = db.get(Analysis, body.analysis_id)
            active = ((a.result or {}).get("suspect") or {}).get("active_chains") if a else None
            chain = active[0] if active else None
        if chain is None:
            raise AppError("This address is valid on several chains; choose the chain to watch.", code="chain_required")
    existing = db.execute(
        select(WatchItem).where(WatchItem.chain == chain, WatchItem.address == info.address, WatchItem.active.is_(True))
    ).scalars().first()
    if existing is not None:
        return watch_dict(existing)
    baseline = body.since if body.since is not None else now_unix()
    w = WatchItem(
        chain=chain, address=info.address, case_id=body.case_id, analysis_id=body.analysis_id, note=body.note or "",
        created_by=user.username, baseline_time=baseline, seen_tx=[],
    )
    db.add(w)
    db.flush()
    record(db, user.username, "watch_added", "watch", w.id, {"chain": chain, "address": info.address, "baseline_time": baseline}, commit=False)
    db.commit()
    return watch_dict(w)


@router.delete("/watchlist/{watch_id}")
def remove_watch(watch_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    w = db.get(WatchItem, watch_id)
    if w is None or not w.active:
        raise NotFound("Watch item not found.")
    w.active = False
    record(db, user.username, "watch_removed", "watch", w.id, {"address": w.address}, commit=False)
    db.commit()
    return {"id": w.id, "active": False}


@router.post("/watchlist/{watch_id}/check")
async def check_now(watch_id: int, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Poll one watched wallet immediately instead of waiting for the 5-minute cycle."""
    from app.alerts.watcher import check_item
    from app.db import session_scope

    with session_scope() as db:
        w = db.get(WatchItem, watch_id)
        if w is None or not w.active:
            raise NotFound("Watch item not found.")
    created = await asyncio.to_thread(lambda: asyncio.run(check_item(watch_id)))
    with session_scope() as db:
        w = db.get(WatchItem, watch_id)
        return {"created_alerts": created, "watch": watch_dict(w), "error": w.last_error}


@router.get("/alerts")
def list_alerts(
    urgent: bool | None = None,
    unacknowledged: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    stmt = select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit)
    if urgent is not None:
        stmt = stmt.where(Alert.urgent.is_(urgent))
    if unacknowledged:
        stmt = stmt.where(Alert.acknowledged.is_(False))
    unack_urgent = db.execute(select(func.count()).select_from(Alert).where(Alert.urgent.is_(True), Alert.acknowledged.is_(False))).scalar_one()
    return {"items": [alert_dict(a) for a in db.execute(stmt).scalars()], "unacknowledged_urgent": unack_urgent}


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    a = db.get(Alert, alert_id)
    if a is None:
        raise NotFound("Alert not found.")
    a.acknowledged = True
    record(db, user.username, "alert_acknowledged", "alert", a.id, {}, commit=False)
    db.commit()
    return alert_dict(a)
