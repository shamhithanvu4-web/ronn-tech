"""Hash-chained audit log (SPEC §8).

Each row stores prev_hash (the previous row's row_hash) and
row_hash = SHA-256(prev_hash + canonical JSON of the row content).
GET /api/audit/verify walks the chain and reports the first broken link.
"""
from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import iso, utcnow
from app.models import AuditLog
from app.util import canonical_json, sha256_hex

GENESIS = "0" * 64
_lock = threading.Lock()


def _content(row: AuditLog) -> dict[str, Any]:
    return {
        "ts": iso(row.ts),
        "actor": row.actor,
        "action": row.action,
        "object_type": row.object_type or "",
        "object_id": row.object_id or "",
        "details": row.details or {},
    }


def compute_hash(prev_hash: str, content: dict[str, Any]) -> str:
    return sha256_hex(prev_hash + canonical_json(content))


def record(
    db: Session,
    actor: str,
    action: str,
    object_type: str = "",
    object_id: str | int = "",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditLog:
    """Append one audit row. Serialised with a process lock to keep the chain linear."""
    with _lock:
        last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
        prev = last.row_hash if last else GENESIS
        row = AuditLog(
            ts=utcnow().replace(microsecond=0),
            actor=actor or "system",
            action=action,
            object_type=object_type or "",
            object_id=str(object_id or ""),
            details=details or {},
            prev_hash=prev,
            row_hash="",
        )
        row.row_hash = compute_hash(prev, _content(row))
        db.add(row)
        if commit:
            db.commit()
        else:
            db.flush()
        return row


def verify_chain(db: Session) -> dict[str, Any]:
    prev = GENESIS
    count = 0
    for row in db.execute(select(AuditLog).order_by(AuditLog.id.asc())).scalars():
        count += 1
        if row.prev_hash != prev:
            return {
                "valid": False,
                "rows_checked": count,
                "broken_at_id": row.id,
                "reason": "prev_hash does not match the previous row's hash",
            }
        expected = compute_hash(prev, _content(row))
        if expected != row.row_hash:
            return {
                "valid": False,
                "rows_checked": count,
                "broken_at_id": row.id,
                "reason": "row content was modified after it was written",
            }
        prev = row.row_hash
    return {"valid": True, "rows_checked": count, "head_hash": prev}
