"""Label intelligence store (SPEC §5).

- Ingestion: replace_source() swaps one source's labels atomically and records provenance.
- Seed CSV: export_seed_csv() / load_seed_csv() so the app runs without re-ingesting.
- Lookups: LabelStore.lookup() for the engine, with cross-EVM inheritance (same key holder),
  hidden labels (evaluation) and runtime inferred labels (tier C).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import scoring_config
from app.db import iso, utcnow
from app.models import Entity, Label, LabelSourceRun
from app.models.labels import VASP_TYPES
from app.providers.context import current

EVM_LABEL_CHAINS = {
    "ethereum", "bsc", "polygon", "arbitrum", "optimism", "base", "avalanche", "worldchain", "gnosis", "celo",
}
TIER_RELIABILITY = {"A": 0.95, "B": 0.80, "D": 0.50}
SEED_COLUMNS = [
    "chain", "address", "entity_name", "entity_type", "is_vasp", "role", "source", "source_url",
    "reliability", "tier", "retrieved_at", "evidence",
]
NAME_ALIASES = {"okex": "okx"}


def name_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return NAME_ALIASES.get(key, key)


def normalize_address(chain: str, address: str) -> str:
    address = (address or "").strip()
    if chain in EVM_LABEL_CHAINS or address.lower().startswith("0x"):
        return address.lower()
    if chain == "bitcoin" and address.lower().startswith("bc1"):
        return address.lower()
    return address


def is_evm_chain(chain: str) -> bool:
    return chain in EVM_LABEL_CHAINS


@dataclass
class LabelRow:
    chain: str
    address: str
    entity_name: str
    entity_type: str
    is_vasp: bool
    role: str
    source: str
    source_url: str | None
    reliability: float
    tier: str
    retrieved_at: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_csv(self) -> dict[str, str]:
        d = asdict(self)
        d["is_vasp"] = "true" if self.is_vasp else "false"
        d["evidence"] = json.dumps(self.evidence, sort_keys=True, ensure_ascii=False)
        d["reliability"] = f"{self.reliability:.2f}"
        return {k: ("" if v is None else str(v)) for k, v in d.items()}


@dataclass
class IngestResult:
    source: str
    rows: list[LabelRow]
    source_url: str | None
    version: str | None
    content_sha256: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class LabelHit:
    label_id: int | None
    chain: str
    address: str
    entity_id: int
    entity_name: str
    entity_type: str
    is_vasp: bool
    role: str
    source: str
    source_url: str | None
    reliability: float
    tier: str
    retrieved_at: str | None
    evidence: dict[str, Any]
    inherited_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ ingestion
class EntityResolver:
    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[tuple[str, str], Entity] = {}
        for ent in db.execute(select(Entity)).scalars():
            self._cache[(ent.name_key, ent.entity_type)] = ent

    def get(self, name: str, entity_type: str, is_vasp: bool | None = None) -> Entity:
        key = (name_key(name), entity_type)
        ent = self._cache.get(key)
        if ent is None:
            ent = Entity(
                name=name.strip()[:160] or "Unnamed",
                name_key=key[0] or "unnamed",
                entity_type=entity_type,
                is_vasp=bool(is_vasp if is_vasp is not None else entity_type in VASP_TYPES),
                verification_status="unverified",
            )
            self.db.add(ent)
            self.db.flush()
            self._cache[key] = ent
        return ent

    def find_by_name(self, name: str) -> list[Entity]:
        k = name_key(name)
        return [e for (nk, _t), e in self._cache.items() if nk == k]


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return utcnow()
    v = value.rstrip("Z")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return utcnow()


def replace_source(db: Session, result: IngestResult) -> LabelSourceRun:
    """Replace all labels of one source with `result.rows` and record provenance."""
    run = LabelSourceRun(
        source=result.source,
        started_at=utcnow(),
        source_url=result.source_url,
        version=result.version,
        content_sha256=result.content_sha256,
        notes=result.notes,
    )
    db.add(run)
    db.execute(delete(Label).where(Label.source == result.source))
    resolver = EntityResolver(db)
    seen: set[tuple[str, str, int]] = set()
    entity_ids: set[int] = set()
    batch: list[dict[str, Any]] = []
    for row in result.rows:
        addr = normalize_address(row.chain, row.address)
        ent = resolver.get(row.entity_name, row.entity_type, row.is_vasp)
        key = (row.chain, addr, ent.id)
        if key in seen:
            continue
        seen.add(key)
        entity_ids.add(ent.id)
        batch.append(
            {
                "chain": row.chain,
                "address": addr,
                "entity_id": ent.id,
                "role": row.role,
                "source": row.source,
                "source_url": row.source_url,
                "reliability": float(row.reliability),
                "tier": row.tier,
                "retrieved_at": _parse_dt(row.retrieved_at),
                "evidence": row.evidence or {},
            }
        )
        if len(batch) >= 2000:
            db.execute(Label.__table__.insert(), batch)
            batch = []
    if batch:
        db.execute(Label.__table__.insert(), batch)
    run.label_count = len(seen)
    run.entity_count = len(entity_ids)
    run.finished_at = utcnow()
    db.commit()
    return run


def export_seed_csv(db: Session, path: Path, sources: Iterable[str] | None = None) -> int:
    """Write all non-inferred, non-analyst labels to the committed seed CSV."""
    q = select(Label, Entity).join(Entity, Label.entity_id == Entity.id).where(
        Label.source.notin_(["inferred_deposit", "inferred_btc_cluster", "analyst"])
    )
    if sources:
        q = q.where(Label.source.in_(list(sources)))
    q = q.order_by(Label.source, Label.chain, Label.address)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SEED_COLUMNS)
        writer.writeheader()
        for label, ent in db.execute(q):
            row = LabelRow(
                chain=label.chain,
                address=label.address,
                entity_name=ent.name,
                entity_type=ent.entity_type,
                is_vasp=ent.is_vasp,
                role=label.role,
                source=label.source,
                source_url=label.source_url,
                reliability=label.reliability,
                tier=label.tier,
                retrieved_at=iso(label.retrieved_at) or "",
                evidence=label.evidence or {},
            )
            writer.writerow(row.to_csv())
            n += 1
    return n


def read_seed_csv(path: Path) -> dict[str, list[LabelRow]]:
    by_source: dict[str, list[LabelRow]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            row = LabelRow(
                chain=rec["chain"],
                address=rec["address"],
                entity_name=rec["entity_name"],
                entity_type=rec["entity_type"],
                is_vasp=rec["is_vasp"].lower() == "true",
                role=rec["role"] or "UNKNOWN",
                source=rec["source"],
                source_url=rec["source_url"] or None,
                reliability=float(rec["reliability"]),
                tier=rec["tier"] or "B",
                retrieved_at=rec["retrieved_at"],
                evidence=json.loads(rec["evidence"]) if rec.get("evidence") else {},
            )
            by_source.setdefault(row.source, []).append(row)
    return by_source


def load_seed_csv(db: Session, path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source, rows in read_seed_csv(path).items():
        run = replace_source(
            db,
            IngestResult(
                source=source,
                rows=rows,
                source_url=rows[0].source_url if rows else None,
                version="seed-csv",
                notes={"loaded_from": path.name},
            ),
        )
        counts[source] = run.label_count
    return counts


def label_counts(db: Session) -> dict[str, int]:
    return {s: n for s, n in db.execute(select(Label.source, func.count()).group_by(Label.source))}


# ------------------------------------------------------------------ lookups
class LabelStore:
    """Read-side label lookups for the engine. One instance per analysis."""

    def __init__(self, db: Session):
        self.db = db
        self._memo: dict[tuple[str, str], list[LabelHit]] = {}
        self.cross_evm_factor = float(scoring_config().get("cross_evm_label_factor", 0.9))

    def _hits_for(self, address: str, chains: list[str] | None) -> list[LabelHit]:
        q = select(Label, Entity).join(Entity, Label.entity_id == Entity.id).where(Label.address == address)
        if chains is not None:
            q = q.where(Label.chain.in_(chains))
        hits = []
        for label, ent in self.db.execute(q):
            hits.append(
                LabelHit(
                    label_id=label.id,
                    chain=label.chain,
                    address=label.address,
                    entity_id=ent.id,
                    entity_name=ent.name,
                    entity_type=ent.entity_type,
                    is_vasp=ent.is_vasp,
                    role=label.role,
                    source=label.source,
                    source_url=label.source_url,
                    reliability=float(label.reliability),
                    tier=label.tier,
                    retrieved_at=iso(label.retrieved_at),
                    evidence=label.evidence or {},
                )
            )
        return hits

    def lookup(self, chain: str, address: str) -> list[LabelHit]:
        addr = normalize_address(chain, address)
        key = (chain, addr)
        if key in self._memo:
            return self._memo[key]
        ctx = current()
        if ctx is not None and (chain, addr) in ctx.hidden_label_addresses:
            self._memo[key] = []
            return []
        if is_evm_chain(chain):
            raw = self._hits_for(addr, sorted(EVM_LABEL_CHAINS))
            hits: list[LabelHit] = []
            for h in raw:
                if h.chain == chain:
                    hits.append(h)
                elif h.role != "CONTRACT":
                    h.inherited_from = h.chain
                    h.chain = chain
                    h.reliability = round(h.reliability * self.cross_evm_factor, 4)
                    hits.append(h)
        else:
            hits = self._hits_for(addr, [chain])
        if ctx is not None and not ctx.use_inferred_labels:
            hits = [h for h in hits if not h.source.startswith("inferred")]
        hits.sort(key=lambda h: (-h.reliability, h.entity_name))
        self._memo[key] = hits
        return hits

    def best(self, chain: str, address: str, vasp_only: bool = False) -> LabelHit | None:
        hits = self.lookup(chain, address)
        if vasp_only:
            hits = [h for h in hits if h.is_vasp]
        return hits[0] if hits else None

    def remember(self, hit: LabelHit) -> None:
        """Add a runtime (inferred or live-lookup) hit to the in-memory view."""
        key = (hit.chain, normalize_address(hit.chain, hit.address))
        hits = [h for h in self._memo.get(key, []) if h.entity_id != hit.entity_id or h.source != hit.source]
        hits.append(hit)
        hits.sort(key=lambda h: (-h.reliability, h.entity_name))
        self._memo[key] = hits

    def entity(self, entity_id: int) -> Entity | None:
        return self.db.get(Entity, entity_id)


def inferred_hit(
    chain: str,
    address: str,
    entity_id: int,
    entity_name: str,
    entity_type: str,
    role: str,
    source: str,
    reliability: float,
    evidence: dict[str, Any],
) -> LabelHit:
    """A tier-C label produced at runtime by the engine (persisted later by the pipeline)."""
    return LabelHit(
        label_id=None,
        chain=chain,
        address=normalize_address(chain, address),
        entity_id=entity_id,
        entity_name=entity_name,
        entity_type=entity_type,
        is_vasp=entity_type in VASP_TYPES,
        role=role,
        source=source,
        source_url=None,
        reliability=float(reliability),
        tier="C",
        retrieved_at=iso(utcnow()),
        evidence=evidence,
    )


def persist_runtime_label(
    db: Session,
    chain: str,
    address: str,
    entity_id: int,
    role: str,
    source: str,
    reliability: float,
    tier: str,
    evidence: dict[str, Any],
    source_url: str | None = None,
    created_by: str | None = None,
) -> Label:
    addr = normalize_address(chain, address)
    existing = db.execute(
        select(Label).where(
            Label.chain == chain, Label.address == addr, Label.entity_id == entity_id, Label.source == source
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = Label(chain=chain, address=addr, entity_id=entity_id, source=source)
        db.add(existing)
    existing.role = role
    existing.reliability = float(reliability)
    existing.tier = tier
    existing.evidence = evidence
    existing.source_url = source_url
    existing.retrieved_at = utcnow()
    existing.created_by = created_by
    db.commit()
    return existing
