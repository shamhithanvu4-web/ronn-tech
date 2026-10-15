from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class WatchItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(24))
    address: Mapped[str] = mapped_column(String(128), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    baseline_time: Mapped[int | None] = mapped_column(Integer, nullable=True)  # unix seconds
    seen_tx: Mapped[list] = mapped_column(JSON, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int | None] = mapped_column(ForeignKey("watchlist.id"), nullable=True, index=True)
    chain: Mapped[str] = mapped_column(String(24))
    address: Mapped[str] = mapped_column(String(128))
    tx_hash: Mapped[str] = mapped_column(String(128))
    to_addr: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset: Mapped[str | None] = mapped_column(String(24), nullable=True)
    amount: Mapped[str | None] = mapped_column(String(64), nullable=True)
    block_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    vasp_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vasp_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
