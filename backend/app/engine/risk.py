"""Wallet risk level (SPEC §6.8), always with reasons.

Severe: direct SANCTIONED exposure.
High:   direct exposure to MIXER, DARKNET_MARKET, RANSOMWARE or SCAM.
Medium: indirect exposure within 2 hops with >= 10 % value share, or 2+ typologies.
Low:    everything else.
"Direct" means the suspect itself or a counterparty one hop away (either direction).
"""
from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any

import networkx as nx

from app.config import scoring_config
from app.engine.scoring import Scorer
from app.engine.traversal import TraversalResult
from app.engine.types import NodeInfo, node_id

HIGH_TYPES = ("MIXER", "DARKNET_MARKET", "RANSOMWARE", "SCAM")
RISK_TYPES = ("SANCTIONED",) + HIGH_TYPES
ORDER = {"Low": 0, "Medium": 1, "High": 2, "Severe": 3}


def _risk_hits(info: NodeInfo) -> list:
    return [h for h in info.labels if h.entity_type in RISK_TYPES]


def assess(
    nodes: dict[str, NodeInfo], scorer: Scorer, results: list[TraversalResult], typologies: list[dict[str, Any]]
) -> dict[str, Any]:
    cfg = scoring_config()["risk"]
    min_share = Decimal(str(cfg.get("indirect_min_share", 0.10)))
    reasons: list[dict[str, Any]] = []
    level = "Low"

    def bump(new: str, reason: dict[str, Any]) -> None:
        nonlocal level
        reasons.append({"level": new, **reason})
        if ORDER[new] > ORDER[level]:
            level = new

    for res in results:
        sid = node_id(res.chain, res.suspect)
        suspect = nodes.get(sid)
        if suspect is None:
            continue
        for h in _risk_hits(suspect):
            bump(
                "Severe" if h.entity_type == "SANCTIONED" else "High",
                {"reason": f"The suspect address itself is labelled {h.entity_type} ({h.entity_name})", "address": suspect.address,
                 "chain": res.chain, "source": h.source, "source_url": h.source_url, "evidence_txs": []},
            )
        g = scorer.graph.g
        neighbours = set(g.successors(sid)) | set(g.predecessors(sid)) if g.has_node(sid) else set()
        for nid in sorted(neighbours):
            info = nodes.get(nid)
            if info is None:
                continue
            txs = [d["tx_hash"] for _u, _v, d in itertools.chain(g.edges(sid, data=True), g.in_edges(sid, data=True)) if nid in (_u, _v)]
            for h in _risk_hits(info):
                bump(
                    "Severe" if h.entity_type == "SANCTIONED" else "High",
                    {"reason": f"Direct transfer with {h.entity_type} address ({h.entity_name})", "address": info.address,
                     "chain": res.chain, "source": h.source, "source_url": h.source_url, "evidence_txs": sorted(set(txs))[:10]},
                )
        # Indirect: two hops, forward (outflow share) or backward (inflow share).
        flow = scorer.graph.flow_digraph(("flow",))
        fund = scorer.graph.flow_digraph(("funding",))
        for nid, info in sorted(nodes.items()):
            if info.chain != res.chain or nid == sid or nid in neighbours or not _risk_hits(info):
                continue
            share = Decimal(0)
            path_txs: list[str] = []
            if flow.has_node(sid) and flow.has_node(nid):
                for p in itertools.islice(nx.all_simple_paths(flow, sid, nid, cutoff=2), 20):
                    s = Decimal(1)
                    for u, v in zip(p, p[1:]):
                        s *= scorer.out_share(u, v, res)
                    share += s
                    path_txs += [t["tx_hash"] for u, v in zip(p, p[1:]) for t in flow[u][v]["transfers"]][:6]
            if fund.has_node(sid) and fund.has_node(nid):
                for p in itertools.islice(nx.all_simple_paths(fund, nid, sid, cutoff=2), 20):
                    s = Decimal(1)
                    for u, v in zip(p, p[1:]):
                        s *= scorer.in_share(v, u, res)
                    share += s
                    path_txs += [t["tx_hash"] for u, v in zip(p, p[1:]) for t in fund[u][v]["transfers"]][:6]
            if share >= min_share:
                h = _risk_hits(info)[0]
                bump(
                    "Medium",
                    {"reason": f"Indirect exposure (2 hops, {float(share) * 100:.0f}% value share) to {h.entity_type} ({h.entity_name})",
                     "address": info.address, "chain": res.chain, "source": h.source, "source_url": h.source_url,
                     "evidence_txs": sorted(set(path_txs))[:10]},
                )
    if len(typologies) >= int(cfg.get("typology_count_medium", 2)):
        bump(
            "Medium",
            {"reason": f"{len(typologies)} laundering typologies detected: " + ", ".join(t["code"] for t in typologies),
             "address": None, "chain": None, "source": "typology rules", "source_url": None,
             "evidence_txs": sorted({tx for t in typologies for tx in t["evidence_txs"]})[:10]},
        )
    if not reasons:
        reasons.append({"level": "Low", "reason": "No sanctioned, mixer, darknet, ransomware or scam exposure found within the traced paths, and fewer than two typologies.",
                        "address": None, "chain": None, "source": None, "source_url": None, "evidence_txs": []})
    reasons.sort(key=lambda r: -ORDER[r["level"]])
    return {"level": level, "reasons": reasons}
