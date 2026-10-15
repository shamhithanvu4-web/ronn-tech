"""VASP directory and labels (SPEC §5, §11). ADMIN verifies entities with a source URL;
any analyst may add a tier-D label (reliability 0.50) until it is verified."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit import record
from app.config import explorer_address_url, scoring_config
from app.db import get_db, iso, utcnow
from app.engine.validate import CHAIN_TO_FAMILY, validate_address
from app.errors import AppError, NotFound
from app.labels.store import EntityResolver, LabelStore, normalize_address, persist_runtime_label
from app.models import Entity, Label, User
from app.models.labels import VASP_TYPES
from app.schemas import EntityPatch, LabelIn
from app.security import get_current_user, require_roles

router = APIRouter(tags=["entities"])
TRI_FIELDS = ("fiu_ind_registered", "sahyog_onboarded", "le_request_channel")


def entity_dict(e: Entity, label_count: int | None = None) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "entity_type": e.entity_type,
        "is_vasp": e.is_vasp,
        "jurisdiction": e.jurisdiction,
        "fiu_ind_registered": e.fiu_ind_registered,
        "sahyog_onboarded": e.sahyog_onboarded,
        "le_request_channel": e.le_request_channel,
        "verification_status": e.verification_status,
        "verified_source_url": e.verified_source_url,
        "last_verified_at": iso(e.last_verified_at),
        "verified_by": e.verified_by,
        "label_count": label_count,
    }


@router.get("/entities")
def list_entities(
    q: str | None = None,
    entity_type: str | None = None,
    vasp_only: bool = True,
    verification_status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    counts = select(Label.entity_id, func.count().label("n")).group_by(Label.entity_id).subquery()
    stmt = select(Entity, func.coalesce(counts.c.n, 0)).outerjoin(counts, counts.c.entity_id == Entity.id)
    if vasp_only:
        stmt = stmt.where(Entity.is_vasp.is_(True))
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type.upper())
    if verification_status:
        stmt = stmt.where(Entity.verification_status == verification_status)
    if q:
        stmt = stmt.where(or_(Entity.name.ilike(f"%{q.strip()}%"), Entity.name_key.ilike(f"%{q.strip().lower()}%")))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = stmt.order_by(func.coalesce(counts.c.n, 0).desc(), Entity.name).limit(limit).offset(offset)
    return {"total": total, "items": [entity_dict(e, n) for e, n in db.execute(stmt).all()]}


@router.get("/entities/{entity_id}")
def get_entity(entity_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    e = db.get(Entity, entity_id)
    if e is None:
        raise NotFound("Entity not found.")
    labels = db.execute(select(Label).where(Label.entity_id == e.id).order_by(Label.tier, Label.chain).limit(50)).scalars().all()
    n = db.execute(select(func.count()).select_from(Label).where(Label.entity_id == e.id)).scalar_one()
    out = entity_dict(e, n)
    out["labels"] = [
        {"chain": l.chain, "address": l.address, "role": l.role, "source": l.source, "source_url": l.source_url, "tier": l.tier,
         "reliability": l.reliability, "retrieved_at": iso(l.retrieved_at), "explorer_url": explorer_address_url(l.chain, l.address)}
        for l in labels
    ]
    return out


@router.patch("/entities/{entity_id}")
def patch_entity(entity_id: int, body: EntityPatch, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMIN"))) -> dict[str, Any]:
    e = db.get(Entity, entity_id)
    if e is None:
        raise NotFound("Entity not found.")
    fields = body.model_dump(exclude_unset=True)
    clear = fields.pop("clear", []) or []
    url = fields.get("verified_source_url", e.verified_source_url)
    sets_fact = any(fields.get(k) is not None for k in TRI_FIELDS + ("jurisdiction",)) or fields.get("verification_status") == "verified"
    if sets_fact and not url:
        raise AppError(
            "A verified_source_url is required when recording FIU-IND registration, SAHYOG onboarding, a request channel, "
            "a jurisdiction or a verified status. Never enter these from memory.",
            code="source_url_required",
        )
    changes: dict[str, Any] = {}
    for k, v in fields.items():
        if getattr(e, k) != v:
            changes[k] = {"from": getattr(e, k), "to": v}
            setattr(e, k, v)
    for k in clear:
        if getattr(e, k) is not None:
            changes[k] = {"from": getattr(e, k), "to": None}
            setattr(e, k, None)
    if changes:
        e.last_verified_at = utcnow()
        e.verified_by = user.username
        if "verification_status" not in fields and sets_fact and e.verification_status == "unverified":
            e.verification_status = "verified"
            changes["verification_status"] = {"from": "unverified", "to": "verified"}
        record(db, user.username, "entity_updated", "entity", e.id, {"changes": changes}, commit=False)
    db.commit()
    return entity_dict(e)


@router.get("/labels")
def get_labels(
    address: str,
    chain: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    info = validate_address(address, chain)
    store = LabelStore(db)
    chains = [chain] if chain else info.chains
    hits = []
    for c in chains:
        for h in store.lookup(c, info.address):
            d = h.to_dict()
            d["lookup_chain"] = c
            hits.append(d)
    return {"address": info.address, "chains": chains, "labels": hits}


@router.post("/labels", status_code=201)
def add_label(body: LabelIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    chain = body.chain.strip().lower()
    if chain not in CHAIN_TO_FAMILY:
        raise AppError(f"Unknown chain {chain!r}.", code="invalid_chain")
    info = validate_address(body.address, chain)
    ent = EntityResolver(db).get(body.entity_name.strip(), body.entity_type, is_vasp=body.entity_type in VASP_TYPES)
    rel = float(scoring_config()["reliability_tiers"]["D"])
    label = persist_runtime_label(
        db, chain, info.address, ent.id, body.role, "analyst", rel, "D",
        {"note": body.note, "added_by": user.username, "status": "unverified analyst label"},
        source_url=body.source_url, created_by=user.username,
    )
    record(db, user.username, "label_added", "label", label.id, {"chain": chain, "address": normalize_address(chain, info.address), "entity": ent.name, "tier": "D"})
    return {"id": label.id, "chain": chain, "address": label.address, "entity_id": ent.id, "entity_name": ent.name, "tier": "D", "reliability": rel}
