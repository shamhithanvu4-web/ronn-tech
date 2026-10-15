from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class ProviderCache(Base):
    """Response cache keyed by sha256 of the canonical (key-free) request."""

    __tablename__ = "provider_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    chain: Mapped[str] = mapped_column(String(24), index=True)
    url: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    body: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RawResponse(Base):
    """Every raw provider response used by an analysis (evidence integrity)."""

    __tablename__ = "raw_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    chain: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    path: Mapped[str] = mapped_column(Text)
    from_cache: Mapped[str] = mapped_column(String(8), default="network")
