from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow

REQUEST_TYPES = ("INFORMATION_DISCLOSURE", "FREEZE_HOLD")
REQUEST_STATUSES = ("DRAFT", "APPROVED", "DISPATCHED_SIMULATED", "RESPONSE_RECEIVED", "WITHDRAWN")


class LawfulRequest(Base):
    __tablename__ = "lawful_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    entity_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_type: Mapped[str] = mapped_column(String(32))
    legal_basis_id: Mapped[str] = mapped_column(String(32))
    legal_basis_label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    routing: Mapped[str] = mapped_column(String(64))
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    drafted_by: Mapped[str] = mapped_column(String(64))
    drafted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatched_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Outbox(Base):
    """Messages queued to SAHYOG. Simulated: nothing leaves this server."""

    __tablename__ = "sahyog_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("lawful_requests.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(48), default="Queued to SAHYOG (simulated)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SahyogInbound(Base):
    __tablename__ = "sahyog_inbound"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sahyog_ref: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    body_sha256: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="Simulated SAHYOG adapter")
