from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow

ANALYSIS_STATUSES = ("queued", "running", "done", "failed")
ANALYSIS_STAGES = ("queued", "validating", "fetching", "building_graph", "inferring_deposits", "scoring", "done")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    chain: Mapped[str | None] = mapped_column(String(24), nullable=True)
    chain_family: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nearest_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nearest_entity_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    nearest_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_hops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReportRecord(Base):
    """Hashes of every generated report so an uploaded copy can be verified."""

    __tablename__ = "report_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # analysis | request
    object_id: Mapped[str] = mapped_column(String(36), index=True)
    fmt: Mapped[str] = mapped_column(String(8))  # pdf | json
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    analysis_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_by: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")
