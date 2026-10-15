"""Deposit-address inference for account chains (EVM and TRON), SPEC §6.4.

An unlabelled node D that received funds on a path is a deposit address of entity E when:
  * Sweep (required): within tau of receiving, D forwards at least (1 - alpha) of that
    asset amount to addresses labelled E. alpha = 0.01; tau = 3,200 blocks on Ethereum
    (about 10.7 h) and 11 h wall-clock elsewhere. Parameters from the Deposit Address
    Reuse heuristic of Victor (2020); alpha is applied as a relative tolerance here
    (the paper uses an absolute ETH difference), which also works for stablecoins.
  * Gas top-up (corroboration): an address labelled E sent native coin to D shortly
    before a token sweep.
  * Exclusivity (corroboration): D's outgoing counterparties are only addresses of E,
    at most 3 distinct.
Reliability: sweep only 0.70; sweep + one corroboration 0.80; all three 0.85.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from app.config import chain_config, scoring_config
from app.engine.types import DepositInference, LabelLookup
from app.providers.base import Transfer

VASP_ONLY = True


def _cfg() -> dict:
    return scoring_config()["deposit_inference"]


def _same_asset(a: Transfer, b: Transfer) -> bool:
    return a.asset == b.asset and (a.token_contract or None) == (b.token_contract or None)


def _within_tau(chain: str, t_in: Transfer, t_out: Transfer, cfg: dict) -> bool:
    if t_out.block_time < t_in.block_time:
        return False
    if chain == "ethereum" and t_in.block_number and t_out.block_number:
        return 0 <= t_out.block_number - t_in.block_number <= int(cfg.get("tau_blocks_ethereum", 3200))
    try:
        tau = int(chain_config(chain).get("tau_seconds") or cfg.get("tau_seconds_other", 39600))
    except KeyError:
        tau = int(cfg.get("tau_seconds_other", 39600))
    return t_out.block_time - t_in.block_time <= tau


def infer_deposit(
    chain: str,
    address: str,
    transfers: list[Transfer],
    incoming: Iterable[Transfer] | None,
    labels: LabelLookup,
) -> DepositInference | None:
    """Run the sweep test on D=address. `incoming` are the path transfers into D
    (None = every in-scope incoming transfer, used for the hop-0 check)."""
    cfg = _cfg()
    alpha = Decimal(str(cfg.get("alpha", 0.01)))
    outgoing = [t for t in transfers if t.from_addr == address and t.to_addr and t.to_addr != address and t.amount_dec > 0]
    if not outgoing:
        return None
    if incoming is None:
        incoming = [t for t in transfers if t.to_addr == address and t.in_scope and t.amount_dec > 0]
    incoming = [t for t in incoming if t.in_scope and t.amount_dec > 0]
    if not incoming:
        return None

    vasp_hits: dict[str, object] = {}
    for t in outgoing:
        if t.to_addr in vasp_hits:
            continue
        hits = [h for h in labels.lookup(chain, t.to_addr) if h.is_vasp and not h.source.startswith("inferred")]
        vasp_hits[t.to_addr] = hits[0] if hits else None

    # entity_id -> evidence
    sweeps: dict[int, dict] = defaultdict(lambda: {"deposit_txs": [], "sweep_txs": set(), "hot": set(), "deposited": Decimal(0), "swept": Decimal(0), "hit": None, "asset": None, "first_sweep": None})
    for t_in in sorted(incoming, key=lambda t: t.block_time):
        per_entity: dict[int, Decimal] = defaultdict(Decimal)
        per_entity_txs: dict[int, list[Transfer]] = defaultdict(list)
        for t_out in outgoing:
            if not t_out.in_scope or not _same_asset(t_in, t_out) or not _within_tau(chain, t_in, t_out, cfg):
                continue
            hit = vasp_hits.get(t_out.to_addr)
            if hit is None:
                continue
            per_entity[hit.entity_id] += t_out.amount_dec
            per_entity_txs[hit.entity_id].append(t_out)
        for eid, swept in per_entity.items():
            if swept >= (Decimal(1) - alpha) * t_in.amount_dec:
                ev = sweeps[eid]
                ev["deposit_txs"].append(t_in.tx_hash)
                ev["deposited"] += t_in.amount_dec
                for o in per_entity_txs[eid]:
                    ev["sweep_txs"].add(o.tx_hash)
                    ev["hot"].add(o.to_addr)
                    ev["first_sweep"] = o.block_time if ev["first_sweep"] is None else min(ev["first_sweep"], o.block_time)
                ev["swept"] = sum((o.amount_dec for o in outgoing if o.tx_hash in ev["sweep_txs"] and o.to_addr in ev["hot"]), Decimal(0))
                ev["hit"] = vasp_hits[per_entity_txs[eid][0].to_addr]
                ev["asset"] = t_in.asset
    if not sweeps:
        return None

    eid, ev = max(sweeps.items(), key=lambda kv: (len(kv[1]["deposit_txs"]), kv[1]["deposited"]))
    hit = ev["hit"]
    hot = set(ev["hot"])

    corroborations: list[str] = []
    gas_txs: list[str] = []
    token_sweep = any(t.token_contract for t in outgoing if t.tx_hash in ev["sweep_txs"])
    if token_sweep and ev["first_sweep"] is not None:
        window = int(float(cfg.get("gas_topup_window_hours", 24)) * 3600)
        for t in transfers:
            if t.to_addr != address or t.token_contract is not None or t.amount_dec <= 0:
                continue
            if not (ev["first_sweep"] - window <= t.block_time <= ev["first_sweep"]):
                continue
            src_hits = [h for h in labels.lookup(chain, t.from_addr) if h.entity_id == eid]
            if src_hits:
                gas_txs.append(t.tx_hash)
        if gas_txs:
            corroborations.append("gas_topup")

    out_cps = {t.to_addr for t in outgoing}
    max_cp = int(cfg.get("exclusivity_max_counterparties", 3))
    if out_cps and len(out_cps) <= max_cp:
        if all(any(h.entity_id == eid for h in labels.lookup(chain, cp)) for cp in out_cps):
            corroborations.append("exclusivity")

    rel = {
        0: float(cfg.get("reliability_sweep_only", 0.70)),
        1: float(cfg.get("reliability_one_corroboration", 0.80)),
        2: float(cfg.get("reliability_all_three", 0.85)),
    }[len(corroborations)]
    path_senders = {t.from_addr for t in incoming}
    linked = sorted({t.from_addr for t in transfers if t.to_addr == address and t.in_scope and t.amount_dec > 0} - path_senders - hot)
    return DepositInference(
        chain=chain,
        deposit_address=address,
        entity_id=eid,
        entity_name=hit.entity_name,
        entity_type=hit.entity_type,
        hot_wallets=sorted(hot),
        reliability=rel,
        corroborations=corroborations,
        deposit_txs=sorted(set(ev["deposit_txs"])),
        sweep_txs=sorted(ev["sweep_txs"]),
        gas_topup_txs=sorted(set(gas_txs)),
        swept_amount=format(ev["swept"].normalize(), "f"),
        deposited_amount=format(ev["deposited"].normalize(), "f"),
        asset=ev["asset"],
        linked_wallets=linked[:50],
    )
