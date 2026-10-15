"""SQLite-backed response cache (10-minute TTL in live/record mode)."""
from __future__ import annotations

from datetime import timedelta

from app.db import SessionLocal, utcnow
from app.models import ProviderCache
from app.providers.recorder import RecordedResponse

LIVE_TTL = timedelta(minutes=10)


def get(key: str) -> RecordedResponse | None:
    db = SessionLocal()
    try:
        row = db.get(ProviderCache, key)
        if row is None:
            return None
        if row.expires_at is not None and row.expires_at < utcnow():
            return None
        return RecordedResponse(
            url=row.url,
            method="GET",
            request_body=None,
            status=row.status_code,
            body=row.body,
            content_sha256=row.content_sha256,
            retrieved_at=row.retrieved_at.isoformat(timespec="seconds") + "Z",
        )
    finally:
        db.close()


def put(key: str, chain: str, rec: RecordedResponse, permanent: bool = False) -> None:
    db = SessionLocal()
    try:
        row = db.get(ProviderCache, key)
        expires = None if permanent else utcnow() + LIVE_TTL
        if row is None:
            row = ProviderCache(key=key, chain=chain)
            db.add(row)
        row.url = rec.url
        row.status_code = rec.status
        row.body = rec.body
        row.content_sha256 = rec.content_sha256
        row.retrieved_at = utcnow()
        row.expires_at = expires
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def clear() -> int:
    db = SessionLocal()
    try:
        n = db.query(ProviderCache).delete()
        db.commit()
        return n
    finally:
        db.close()
