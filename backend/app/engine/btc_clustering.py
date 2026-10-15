"""Bitcoin clustering (SPEC §6.5).

* Common-input-ownership: if a transaction spends inputs from D and from an address
  labelled E, D joins E's cluster (reliability 0.85).
* CoinJoin-like transactions (>= 5 equal-value outputs) are skipped and flagged as
  MIXER_EXPOSURE instead.
* Change-output detection only decides which output to follow; it never assigns labels.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from app.config import scoring_config
from app.engine.types import ClusterInference, LabelLookup
from app.providers.base import RawTx


def is_coinjoin(tx: RawTx, min_equal: int | None = None) -> bool:
    if min_equal is None:
        min_equal = int(scoring_config()["btc_clustering"].get("coinjoin_min_equal_outputs", 5))
    counts = Counter(o.value for o in tx.outputs if o.address and Decimal(o.value) > 0)
    return any(n >= min_equal for n in counts.values())


def cluster_by_common_input(address: str, txs: list[RawTx], labels: LabelLookup) -> tuple[ClusterInference | None, list[str]]:
    """Returns (cluster inference or None, coinjoin tx ids seen while spending from `address`)."""
    rel = float(scoring_config()["btc_clustering"].get("common_input_reliability", 0.85))
    coinjoins: list[str] = []
    found: dict[int, dict] = {}
    for tx in txs:
        in_addrs = [i.address for i in tx.inputs if i.address]
        if address not in in_addrs:
            continue
        if is_coinjoin(tx):
            coinjoins.append(tx.tx_id)
            continue
        for other in set(in_addrs) - {address}:
            for hit in labels.lookup("bitcoin", other):
                if not hit.is_vasp or hit.source.startswith("inferred"):
                    continue
                ev = found.setdefault(hit.entity_id, {"hit": hit, "co": set(), "txs": set()})
                ev["co"].add(other)
                ev["txs"].add(tx.tx_id)
    if not found:
        return None, coinjoins
    eid, ev = max(found.items(), key=lambda kv: (len(kv[1]["txs"]), kv[1]["hit"].reliability))
    hit = ev["hit"]
    return (
        ClusterInference(
            chain="bitcoin",
            address=address,
            entity_id=eid,
            entity_name=hit.entity_name,
            entity_type=hit.entity_type,
            reliability=rel,
            co_input_addresses=sorted(ev["co"]),
            evidence_txs=sorted(ev["txs"]),
        ),
        coinjoins,
    )


def _script_kind(addr: str | None) -> str:
    if not addr:
        return "?"
    a = addr.lower()
    if a.startswith("bc1p"):
        return "p2tr"
    if a.startswith("bc1q"):
        return "p2wpkh" if len(a) <= 42 else "p2wsh"
    if addr.startswith("3"):
        return "p2sh"
    if addr.startswith("1"):
        return "p2pkh"
    return "?"


def _is_round(value: Decimal) -> bool:
    sats = int((value * Decimal(100_000_000)).to_integral_value())
    return sats > 0 and sats % 100_000 == 0  # multiple of 0.001 BTC


def likely_change_output(tx: RawTx, spender: str) -> str | None:
    """Best-effort change detection for a 2-output spend. Only used to choose what to follow."""
    outs = [o for o in tx.outputs if o.address and o.address != spender]
    if any(o.address == spender for o in tx.outputs):
        return spender
    if len(outs) != 2:
        return None
    in_kinds = {_script_kind(i.address) for i in tx.inputs}
    a, b = outs
    ka, kb = _script_kind(a.address), _script_kind(b.address)
    if ka in in_kinds and kb not in in_kinds:
        return a.address
    if kb in in_kinds and ka not in in_kinds:
        return b.address
    ra, rb = _is_round(Decimal(a.value)), _is_round(Decimal(b.value))
    if ra and not rb:
        return b.address
    if rb and not ra:
        return a.address
    return None
