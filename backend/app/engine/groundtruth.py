"""Ground-truth discovery for evaluation and demo cases (SPEC §14), from real chain data.

Positives: start from tier A/B exchange hot wallets, find unlabelled addresses that sweep
into them (deposit addresses, verified with the same sweep test the engine uses), then
take the wallets that funded those deposit addresses 1-2 hops upstream. The exchange is
the correct answer; the deposit address is hidden from the engine during evaluation so it
must be inferred.

Negatives: active wallets near the sample with no labelled VASP within 3 hops, verified by
a label-only check of the traced graph (inferred labels do not count), so the engine's
inference can still produce false positives on them.

Nothing here invents an address: every candidate comes from provider responses.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.btc_clustering import is_coinjoin
from app.engine.deposit_inference import infer_deposit
from app.engine.types import DataSource, LabelLookup
from app.errors import AppError
from app.models import Entity, Label
from app.providers.base import Transfer

PAGE = 1000


@dataclass
class HotWallet:
    chain: str
    address: str
    entity_id: int
    entity_name: str
    source: str
    tier: str


@dataclass
class Positive:
    id: str
    chain: str
    suspect: str
    hops_upstream: int
    expected_entity_id: int
    expected_entity_name: str
    deposit_address: str
    hot_wallet: str
    evidence_txs: list[str] = field(default_factory=list)
    method: str = "deposit_sweep"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def hot_wallets(db: Session, chain: str, limit: int, seed: int = 7) -> list[HotWallet]:
    """Tier A/B exchange hot wallets on one chain, tier A first, sampled reproducibly."""
    rows = db.execute(
        select(Label, Entity)
        .join(Entity, Entity.id == Label.entity_id)
        .where(Label.chain == chain, Label.tier.in_(["A", "B"]), Entity.entity_type == "CEX", Label.role.in_(["HOT", "UNKNOWN"]))
    ).all()
    rng = random.Random(seed)
    by_tier: dict[str, list[HotWallet]] = {"A": [], "B": []}
    for lab, ent in rows:
        by_tier[lab.tier].append(HotWallet(chain, lab.address, ent.id, ent.name, lab.source, lab.tier))
    out: list[HotWallet] = []
    seen_entities: dict[int, int] = {}
    for tier in ("A", "B"):
        pool = by_tier[tier]
        rng.shuffle(pool)
        for h in pool:
            # spread the sample across exchanges
            if seen_entities.get(h.entity_id, 0) >= 2:
                continue
            seen_entities[h.entity_id] = seen_entities.get(h.entity_id, 0) + 1
            out.append(h)
            if len(out) >= limit:
                return out
    return out


def _is_vasp_labelled(labels: LabelLookup, chain: str, address: str) -> bool:
    return any(h.is_vasp for h in labels.lookup(chain, address))


def _is_labelled(labels: LabelLookup, chain: str, address: str) -> bool:
    return bool(labels.lookup(chain, address))


async def find_deposits(ds: DataSource, labels: LabelLookup, hot: HotWallet, max_candidates: int = 8) -> list[tuple[str, Any, list[Transfer]]]:
    """Unlabelled senders into `hot` that pass the sweep test towards hot's exchange."""
    try:
        rows = await ds.get_transfers(hot.chain, hot.address, None, None, PAGE)
    except AppError:
        return []
    senders: list[str] = []
    for t in sorted(rows, key=lambda x: -x.block_time):
        if t.to_addr != hot.address or not t.in_scope or t.amount_dec <= 0 or t.from_addr in senders:
            continue
        if _is_labelled(labels, hot.chain, t.from_addr):
            continue
        senders.append(t.from_addr)
        if len(senders) >= max_candidates:
            break
    found = []
    for d in senders:
        try:
            d_rows = await ds.get_transfers(hot.chain, d, None, None, PAGE)
        except AppError:
            continue
        dep = infer_deposit(hot.chain, d, d_rows, None, labels)
        if dep is not None and dep.entity_id == hot.entity_id:
            found.append((d, dep, d_rows))
    return found


def funders_of(chain: str, address: str, rows: list[Transfer], labels: LabelLookup, exclude: set[str], limit: int) -> list[tuple[str, str]]:
    """Unlabelled wallets that sent in-scope value to `address` (most recent first)."""
    out: list[tuple[str, str]] = []
    seen = set(exclude)
    for t in sorted(rows, key=lambda x: -x.block_time):
        if t.to_addr != address or not t.in_scope or t.amount_dec <= 0 or t.from_addr in seen:
            continue
        seen.add(t.from_addr)
        if _is_labelled(labels, chain, t.from_addr):
            continue
        out.append((t.from_addr, t.tx_hash))
        if len(out) >= limit:
            break
    return out


async def positives_for_hot(
    ds: DataSource, labels: LabelLookup, hot: HotWallet, per_deposit: int = 2, two_hop: bool = True, max_candidates: int = 8
) -> tuple[list[Positive], list[str]]:
    """Positive cases from one hot wallet, plus nearby unlabelled addresses usable as negative candidates."""
    positives: list[Positive] = []
    nearby: list[str] = []
    for d, dep, d_rows in await find_deposits(ds, labels, hot, max_candidates=max_candidates):
        for f, tx in funders_of(hot.chain, d, d_rows, labels, {hot.address, d}, per_deposit):
            positives.append(
                Positive(
                    id=f"{hot.chain}-{f[-8:]}-1",
                    chain=hot.chain,
                    suspect=f,
                    hops_upstream=1,
                    expected_entity_id=hot.entity_id,
                    expected_entity_name=hot.entity_name,
                    deposit_address=d,
                    hot_wallet=hot.address,
                    evidence_txs=[tx] + dep.sweep_txs[:2],
                )
            )
            if not two_hop:
                continue
            try:
                f_rows = await ds.get_transfers(hot.chain, f, None, None, PAGE)
            except AppError:
                continue
            for t in f_rows:
                if t.from_addr == f and t.to_addr not in (d, hot.address) and t.in_scope and t.amount_dec > 0:
                    nearby.append(t.to_addr)
            for g, tx2 in funders_of(hot.chain, f, f_rows, labels, {hot.address, d, f}, 1):
                positives.append(
                    Positive(
                        id=f"{hot.chain}-{g[-8:]}-2",
                        chain=hot.chain,
                        suspect=g,
                        hops_upstream=2,
                        expected_entity_id=hot.entity_id,
                        expected_entity_name=hot.entity_name,
                        deposit_address=d,
                        hot_wallet=hot.address,
                        evidence_txs=[tx2, tx] + dep.sweep_txs[:2],
                    )
                )
    return positives, [a for a in dict.fromkeys(nearby) if not _is_labelled(labels, hot.chain, a)]


async def btc_cluster_case(ds: DataSource, labels: LabelLookup, hot: HotWallet, max_txs: int = 50) -> Positive | None:
    """A Bitcoin address co-spent with an exchange-labelled input, and a wallet that paid it."""
    try:
        txs = await ds.get_raw_txs("bitcoin", hot.address, None, None, max_txs)
    except AppError:
        return None
    for tx in txs:
        ins = [i.address for i in tx.inputs if i.address]
        if hot.address not in ins or is_coinjoin(tx):
            continue
        for d in dict.fromkeys(a for a in ins if a != hot.address):
            if _is_labelled(labels, "bitcoin", d):
                continue
            try:
                d_rows = await ds.get_transfers("bitcoin", d, None, None, 200)
            except AppError:
                continue
            for f, ftx in funders_of("bitcoin", d, d_rows, labels, {hot.address, d}, 1):
                return Positive(
                    id=f"bitcoin-{f[-8:]}-1",
                    chain="bitcoin",
                    suspect=f,
                    hops_upstream=1,
                    expected_entity_id=hot.entity_id,
                    expected_entity_name=hot.entity_name,
                    deposit_address=d,
                    hot_wallet=hot.address,
                    evidence_txs=[ftx, tx.tx_id],
                    method="common_input",
                )
    return None


def label_only_vasp_within(result: dict[str, Any], hops: int = 3) -> list[str]:
    """Names of non-inferred VASP labels reached within `hops` forward hops in an analysis graph."""
    found = []
    for n in result.get("graph", {}).get("nodes", []):
        if n.get("direction") not in ("forward", "sweep_target") or n.get("hop") is None or n["hop"] > hops:
            continue
        for lab in n.get("labels", []):
            if lab.get("is_vasp") and not str(lab.get("source", "")).startswith("inferred"):
                found.append(lab["entity_name"])
    return sorted(set(found))


def breakpoint_contracts(db: Session, chain: str = "ethereum", limit: int = 12, seed: int = 11) -> list[HotWallet]:
    """Labelled mixer and bridge contracts (for the breakpoint demo case)."""
    rows = db.execute(
        select(Label, Entity).join(Entity, Entity.id == Label.entity_id).where(Label.chain == chain, Entity.entity_type.in_(["MIXER", "BRIDGE"]))
    ).all()
    pool = [HotWallet(chain, lab.address, ent.id, ent.name, lab.source, lab.tier) for lab, ent in rows]
    random.Random(seed).shuffle(pool)
    return pool[:limit]


async def breakpoint_case(ds: DataSource, labels: LabelLookup, contract: HotWallet) -> dict[str, Any] | None:
    """An unlabelled wallet that sent in-scope value into a labelled mixer or bridge contract."""
    try:
        rows = await ds.get_transfers(contract.chain, contract.address, None, None, 200)
    except AppError:
        return None
    for t in sorted(rows, key=lambda x: -x.block_time):
        if t.to_addr == contract.address and t.in_scope and t.amount_dec > 0 and not _is_labelled(labels, contract.chain, t.from_addr):
            return {
                "chain": contract.chain,
                "suspect": t.from_addr,
                "service": contract.entity_name,
                "service_address": contract.address,
                "evidence_txs": [t.tx_hash],
            }
    return None
