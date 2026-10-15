from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

ENTITY_TYPES = (
    "CEX",
    "CUSTODIAL_WALLET",
    "INSTANT_SWAP",
    "OTC_P2P",
    "PAYMENT_PROCESSOR",
    "MIXER",
    "BRIDGE",
    "DEX",
    "DARKNET_MARKET",
    "RANSOMWARE",
    "SCAM",
    "SANCTIONED",
    "GAMBLING",
    "UNKNOWN_SERVICE",
)
VASP_TYPES = ("CEX", "CUSTODIAL_WALLET", "INSTANT_SWAP", "OTC_P2P", "PAYMENT_PROCESSOR")
LABEL_ROLES = ("HOT", "COLD", "DEPOSIT", "WITHDRAWAL", "CONTRACT", "OPERATOR", "PAYMENT", "UNKNOWN")


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    name_key: Mapped[str] = mapped_column(String(160), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    is_vasp: Mapped[bool] = mapped_column(Boolean, default=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fiu_ind_registered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sahyog_onboarded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    le_request_channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")
    verified_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    labels: Mapped[list["Label"]] = relationship(back_populates="entity")

    __table_args__ = (UniqueConstraint("name_key", "entity_type", name="uq_entity_name_type"),)


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain: Mapped[str] = mapped_column(String(24))
    address: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    source: Mapped[str] = mapped_column(String(48), index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(2), default="B")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    entity: Mapped[Entity] = relationship(back_populates="labels")

    __table_args__ = (
        Index("ix_labels_chain_address", "chain", "address"),
        Index("ix_labels_address", "address"),
        UniqueConstraint("chain", "address", "entity_id", "source", name="uq_label"),
    )


class LabelSourceRun(Base):
    """Provenance of each ingestion run (SPEC §5)."""

    __tablename__ = "label_source_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(48), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    label_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[dict] = mapped_column(JSON, default=dict)
