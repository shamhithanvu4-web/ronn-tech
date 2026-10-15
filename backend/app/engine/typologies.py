"""Rule-based laundering typologies (SPEC §6.7), each with evidence transaction hashes."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.config import scoring_config
from app.engine.btc_clustering import is_coinjoin
from app.engine.graph_builder import FlowGraph
from app.engine.traversal import Traverser, TraversalResult
from app.engine.types import NodeInfo, node_id
from app.providers.base import Transfer

TITLES = {
    "PEEL_CHAIN": "Peel chain",
    "FAN_OUT": "Fan-out",
    "FAN_IN": "Fan-in",
    "RAPID_PASS_THROUGH": "Rapid pass-through",
    "MIXER_EXPOSURE": "Mixer exposure",
    "CROSS_CHAIN_BREAKPOINT": "Cross-chain breakpoint",
    "STRUCTURING": "Structuring",
    "ADDRESS_POISONING": "Address poisoning",
}


def _cfg() -> dict[str, Any]:
    return scoring_config()["typologies"]


def _finding(code: str, description: str, nodes: list[str], txs: list[str], **details: Any) -> dict[str, Any]:
    return {
        "code": code,
        "title": TITLES[code],
        "description": description,
        "nodes": nodes,
        "evidence_txs": sorted(set(txs))[:50],
        "details": details,
    }


def _window_hits(times_parties: list[tuple[int, str, str]], window: int, threshold: int) -> tuple[list[str], list[str]] | None:
    """Sliding window: >= threshold distinct parties within `window` seconds."""
    ev = sorted(times_parties)
    j = 0
    for i in range(len(ev)):
        while ev[i][0] - ev[j][0] > window:
            j += 1
        parties = {p for _t, p, _tx in ev[j : i + 1]}
        if len(parties) >= threshold:
            return sorted(parties), [tx for _t, _p, tx in ev[j : i + 1]]
    return None


def fan_out_in(info: NodeInfo, t: Traverser, res: TraversalResult) -> list[dict[str, Any]]:
    cfg = _cfg()
    window = int(float(cfg["fan_window_hours"]) * 3600)
    threshold = int(cfg["fan_threshold"])
    since = res.window_start if info.direction == "suspect" else info.entry_time
    out: list[dict[str, Any]] = []
    outs = t.eligible_out(info, since, res.window_end)
    hit = _window_hits([(x.block_time, x.to_addr, x.tx_hash) for x in outs], window, threshold)
    if hit:
        out.append(_finding("FAN_OUT", f"{len(hit[0])} recipients within {cfg['fan_window_hours']} h", [info.address], hit[1], recipients=len(hit[0])))
    ins = t.eligible_in(info, since, res.window_end)
    hit = _window_hits([(x.block_time, x.from_addr, x.tx_hash) for x in ins], window, threshold)
    if hit:
        out.append(_finding("FAN_IN", f"{len(hit[0])} senders within {cfg['fan_window_hours']} h", [info.address], hit[1], senders=len(hit[0])))
    return out


def _val(x: Transfer) -> Decimal:
    return x.usd_dec if x.usd_dec is not None else x.amount_dec


def rapid_pass_through(info: NodeInfo, t: Traverser, res: TraversalResult) -> dict[str, Any] | None:
    cfg = _cfg()
    window = int(float(cfg["rapid_pass_through_hours"]) * 3600)
    since = res.window_start if info.direction == "suspect" else info.entry_time
    ins = t.eligible_in(info, since, res.window_end)
    outs = t.eligible_out(info, since, res.window_end)
    if not ins or not outs:
        return None
    total_in = sum((_val(x) for x in ins), Decimal(0))
    if total_in <= 0:
        return None
    quick: list[Transfer] = []
    in_times = sorted(x.block_time for x in ins)
    for o in outs:
        if any(0 <= o.block_time - ti <= window for ti in in_times):
            quick.append(o)
    ratio = min(Decimal(1), sum((_val(x) for x in quick), Decimal(0)) / total_in)
    if ratio >= Decimal(str(cfg["rapid_pass_through_share"])):
        return _finding(
            "RAPID_PASS_THROUGH",
            f"{float(ratio) * 100:.0f}% of received value left within {cfg['rapid_pass_through_hours']} h",
            [info.address],
            [x.tx_hash for x in ins[:10]] + [x.tx_hash for x in quick[:10]],
            share=float(round(ratio, 4)),
        )
    return None


def _is_round(x: Transfer, unit: Decimal) -> bool:
    v = _val(x)
    return v >= unit and (v % unit) == 0


def structuring(info: NodeInfo, t: Traverser, res: TraversalResult) -> dict[str, Any] | None:
    cfg = _cfg()
    unit = Decimal(str(cfg["structuring_round_unit"]))
    tol = Decimal(str(cfg["structuring_tolerance"]))
    window = int(float(cfg["structuring_window_hours"]) * 3600)
    need = int(cfg["structuring_min_count"])
    since = res.window_start if info.direction == "suspect" else info.entry_time
    for direction, rows in (("outgoing", t.eligible_out(info, since, res.window_end)), ("incoming", t.eligible_in(info, since, res.window_end))):
        rounds = sorted((x for x in rows if _is_round(x, unit)), key=lambda x: x.block_time)
        for i in range(len(rounds)):
            group = [x for x in rounds[i:] if x.block_time - rounds[i].block_time <= window]
            if len(group) < need:
                continue
            vals = sorted(_val(x) for x in group)
            median = vals[len(vals) // 2]
            near = [x for x in group if abs(_val(x) - median) <= median * tol]
            if len(near) >= need:
                return _finding(
                    "STRUCTURING",
                    f"{len(near)} near-identical round {direction} amounts (~{median.normalize():f}) within {cfg['structuring_window_hours']} h",
                    [info.address],
                    [x.tx_hash for x in near],
                    count=len(near),
                    amount=format(median.normalize(), "f"),
                    direction=direction,
                )
    return None


def _strip(addr: str) -> str:
    a = addr[2:] if addr.lower().startswith("0x") else addr
    return a.lower() if addr.lower().startswith("0x") else a


def address_poisoning(info: NodeInfo, poison: list[dict[str, Any]], t: Traverser) -> dict[str, Any] | None:
    cfg = _cfg()
    pre, suf = int(cfg["poisoning_prefix"]), int(cfg["poisoning_suffix"])
    real_cps = set()
    for x in info.transfers:
        if x.in_scope and x.amount_dec > 0 and not t.is_dust(x):
            real_cps.add(x.to_addr if x.from_addr == info.address else x.from_addr)
    candidates = [p for p in poison if p["node"] == info.id]
    for x in info.transfers:  # dust from other addresses also counts
        if x.in_scope and x.amount_dec > 0 and t.is_dust(x) and x.to_addr == info.address:
            candidates.append({"tx_hash": x.tx_hash, "from": x.from_addr, "to": x.to_addr, "reason": "dust transfer"})
    hits = []
    for p in candidates:
        other = p["from"] if p["to"] == info.address else p["to"]
        if not other or other in real_cps:
            continue
        so = _strip(other)
        for real in real_cps:
            sr = _strip(real)
            if real != other and len(so) > pre + suf and so[:pre] == sr[:pre] and so[-suf:] == sr[-suf:]:
                hits.append((p, other, real))
                break
    if not hits:
        return None
    return _finding(
        "ADDRESS_POISONING",
        f"{len(hits)} zero-value or dust transfer(s) from look-alike addresses sharing the first {pre} and last {suf} characters with a real counterparty",
        [info.address],
        [h[0]["tx_hash"] for h in hits],
        lookalikes=[{"lookalike": h[1], "imitates": h[2], "tx_hash": h[0]["tx_hash"], "reason": h[0].get("reason")} for h in hits[:20]],
    )


def peel_chain(nodes: dict[str, NodeInfo], graph: FlowGraph, chain: str, t: Traverser, res: TraversalResult) -> dict[str, Any] | None:
    cfg = _cfg()
    small = Decimal(str(cfg["peel_small_output_share"]))
    need = int(cfg["peel_chain_min_steps"])
    # Map address -> its peel step (tx id, small output, continuation address).
    steps: dict[str, tuple[str, str]] = {}
    for info in nodes.values():
        if info.chain != chain:
            continue
        if chain == "bitcoin":
            for tx in info.raw_txs:
                if not any(i.address == info.address for i in tx.inputs) or is_coinjoin(tx):
                    continue
                outs = [o for o in tx.outputs if o.address]
                if len(outs) != 2:
                    continue
                total = sum((Decimal(i.value) for i in tx.inputs), Decimal(0))
                a, b = sorted(outs, key=lambda o: Decimal(o.value))
                if total > 0 and Decimal(a.value) < small * total and b.address:
                    steps.setdefault(info.address, (tx.tx_id, b.address))
        else:
            since = res.window_start if info.direction == "suspect" else info.entry_time
            outs = t.eligible_out(info, since, res.window_end)
            if len(outs) != 2:
                continue
            ins = t.eligible_in(info, since, res.window_end)
            total = sum((_val(x) for x in ins), Decimal(0)) or sum((_val(x) for x in outs), Decimal(0))
            a, b = sorted(outs, key=_val)
            if total > 0 and _val(a) < small * total:
                steps.setdefault(info.address, (b.tx_hash, b.to_addr))
    best: list[tuple[str, str]] = []
    for start in steps:
        chain_steps, cur, seen = [], start, set()
        while cur in steps and cur not in seen:
            seen.add(cur)
            chain_steps.append((cur, steps[cur][0]))
            cur = steps[cur][1]
        if len(chain_steps) > len(best):
            best = chain_steps
    if len(best) >= need:
        return _finding(
            "PEEL_CHAIN",
            f"{len(best)} consecutive transactions each peeling off < {float(small) * 100:.0f}% and passing the rest on",
            [a for a, _ in best],
            [tx for _, tx in best],
            steps=len(best),
        )
    return None


def detect(nodes: dict[str, NodeInfo], graph: FlowGraph, results: list[TraversalResult], t: Traverser) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for res in results:
        chain_nodes = {k: v for k, v in nodes.items() if v.chain == res.chain}
        examined = [n for n in chain_nodes.values() if n.fetched and n.direction in ("suspect", "forward") and not (n.attribution and n.attribution.is_vasp)]
        for info in examined:
            findings.extend(fan_out_in(info, t, res))
            for fn in (rapid_pass_through, structuring):
                f = fn(info, t, res)
                if f:
                    findings.append(f)
            f = address_poisoning(info, res.poisoning, t)
            if f:
                findings.append(f)
        f = peel_chain(chain_nodes, graph, res.chain, t, res)
        if f:
            findings.append(f)

        mixer_nodes = [n for n in chain_nodes.values() if any(h.entity_type == "MIXER" for h in n.labels)]
        if mixer_nodes or res.coinjoins:
            txs = list(res.coinjoins)
            for n in mixer_nodes:
                txs.extend(n.entry_txs)
            findings.append(
                _finding(
                    "MIXER_EXPOSURE",
                    "Path touches a labelled mixer" + (" and CoinJoin-like transactions" if res.coinjoins else "")
                    if mixer_nodes
                    else "CoinJoin-like transactions (>= 5 equal-value outputs) on the path",
                    [n.address for n in mixer_nodes],
                    txs,
                    mixers=sorted({h.entity_name for n in mixer_nodes for h in n.labels if h.entity_type == "MIXER"}),
                    coinjoin_txs=res.coinjoins[:20],
                )
            )
        bridge_nodes = [n for n in chain_nodes.values() if any(h.entity_type in ("BRIDGE", "INSTANT_SWAP") for h in n.labels)]
        if bridge_nodes:
            findings.append(
                _finding(
                    "CROSS_CHAIN_BREAKPOINT",
                    "Path reaches a bridge or instant-swap service; follow up on the destination chain",
                    [n.address for n in bridge_nodes],
                    [tx for n in bridge_nodes for tx in n.entry_txs],
                    services=sorted({h.entity_name for n in bridge_nodes for h in n.labels if h.entity_type in ("BRIDGE", "INSTANT_SWAP")}),
                )
            )
    # Merge duplicates (same code) across nodes, keeping per-node detail.
    merged: dict[str, dict[str, Any]] = {}
    for f in findings:
        m = merged.get(f["code"])
        if m is None:
            merged[f["code"]] = {**f, "instances": [dict(f)]}
        else:
            m["nodes"] = sorted(set(m["nodes"]) | set(f["nodes"]))
            m["evidence_txs"] = sorted(set(m["evidence_txs"]) | set(f["evidence_txs"]))[:50]
            m["instances"].append(dict(f))
            m["description"] = f"{TITLES[f['code']]} at {len(m['instances'])} addresses; first: {m['instances'][0]['description']}"
    return sorted(merged.values(), key=lambda f: f["code"])


def suspect_node_id(res: TraversalResult) -> str:
    return node_id(res.chain, res.suspect)


_ = defaultdict  # keep import for type users
