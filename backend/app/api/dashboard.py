"""Case-based analytics for the LEA dashboard (SPEC §12)."""
from __future__ import annotations

import statistics
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.analyses import analysis_brief
from app.api.watchlist import alert_dict
from app.db import get_db, utcnow
from app.models import Alert, Analysis, Case, LawfulRequest, User
from app.security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    now = utcnow()
    week = now - timedelta(days=7)
    open_cases = db.execute(select(func.count()).select_from(Case).where(Case.status != "CLOSED")).scalar_one()
    analyses_week = db.execute(select(func.count()).select_from(Analysis).where(Analysis.created_at >= week)).scalar_one()
    high = db.execute(
        select(func.count()).select_from(Analysis).where(Analysis.status == "done", Analysis.nearest_confidence >= 75)
    ).scalar_one()
    done = db.execute(
        select(Analysis.started_at, Analysis.finished_at).where(
            Analysis.status == "done", Analysis.nearest_entity_id.is_not(None), Analysis.started_at.is_not(None), Analysis.finished_at.is_not(None)
        ).order_by(Analysis.finished_at.desc()).limit(500)
    ).all()
    durations = [(f - s).total_seconds() for s, f in done if f >= s]
    median_tta = round(statistics.median(durations), 1) if durations else None
    pending = db.execute(select(func.count()).select_from(LawfulRequest).where(LawfulRequest.status == "DRAFT")).scalar_one()
    urgent = db.execute(select(func.count()).select_from(Alert).where(Alert.urgent.is_(True), Alert.acknowledged.is_(False))).scalar_one()
    top = db.execute(
        select(Analysis.nearest_entity_name, func.count().label("n"), func.avg(Analysis.nearest_confidence))
        .where(Analysis.status == "done", Analysis.nearest_entity_name.is_not(None))
        .group_by(Analysis.nearest_entity_name)
        .order_by(func.count().desc())
        .limit(8)
    ).all()
    by_status = dict(db.execute(select(Case.status, func.count()).group_by(Case.status)).all())
    by_risk = dict(db.execute(select(Analysis.risk_level, func.count()).where(Analysis.status == "done").group_by(Analysis.risk_level)).all())
    recent = db.execute(select(Analysis).order_by(Analysis.created_at.desc()).limit(8)).scalars().all()
    alerts = db.execute(select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc()).limit(8)).scalars().all()
    return {
        "kpis": {
            "open_cases": open_cases,
            "analyses_this_week": analyses_week,
            "high_confidence_attributions": high,
            "median_time_to_attribution_seconds": median_tta,
            "pending_approvals": pending,
            "urgent_alerts": urgent,
        },
        "top_vasps": [{"entity_name": n, "count": c, "avg_confidence": round(float(a or 0), 1)} for n, c, a in top],
        "cases_by_status": by_status,
        "analyses_by_risk": {k or "Unknown": v for k, v in by_risk.items()},
        "recent_analyses": [analysis_brief(a) for a in recent],
        "alerts": [alert_dict(a) for a in alerts],
    }
