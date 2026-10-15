from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

CASE_STATUSES = (
    "NEW",
    "ANALYZING",
    "REVIEW",
    "REQUEST_DRAFTED",
    "REQUEST_APPROVED",
    "DISPATCHED_SIMULATED",
    "RESPONSE_RECEIVED",
    "CLOSED",
)
CRIME_TYPES = (
    "investment fraud",
    "task scam",
    "sextortion",
    "digital arrest",
    "ransomware",
    "darknet",
    "terror financing",
    "other",
)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    complaint_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fir_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agency: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assigned_officer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    crime_type: Mapped[str] = mapped_column(String(32), default="other")
    status: Mapped[str] = mapped_column(String(32), default="NEW", index=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | sahyog_simulated | batch_import
    sahyog_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    wallets: Mapped[list["CaseWallet"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CaseEvent.ts"
    )


class CaseWallet(Base):
    __tablename__ = "case_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    address: Mapped[str] = mapped_column(String(128))
    chain_hint: Mapped[str | None] = mapped_column(String(24), nullable=True)
    complaint_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    added_by: Mapped[str] = mapped_column(String(64), default="system")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="wallets")


class CaseEvent(Base):
    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

    case: Mapped[Case] = relationship(back_populates="events")
