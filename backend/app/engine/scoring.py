"""Attribution scoring (SPEC §6.6). Weights live in config/scoring.yaml.

For each path p from the suspect to an address labelled with candidate entity E:
    path_score(p) = R x H(hops) x sqrt(V) x T
  R: reliability of the label reached; H: hop decay; V: proportional (haircut) share of
  the suspect's outflow reaching E along p (product over hops of amount sent to the next
  hop / that node's outflow in the window); T: 1.0 if every hop is within 72 h of the
  previous one, 0.8 if within 30 days, otherwise 0.6.
Candidate confidence C(E) = 1 - prod(1 - path_score(p)), shown on a 0-100 scale.
Nearest VASP: the is_vasp candidate with the fewest hops among those with C >= 50; ties
go to the higher confidence.
"""
from __future__ import annotations

import itertools
import math
from collections import defaultdict
from decimal import Decimal
from typing import Any

import networkx as nx

from app.config import explorer_tx_url, scoring_config
from app.engine.graph_builder import FlowGraph
from app.engine.traversal import Traverser, TraversalResult
from app.engine.types import NodeInfo, node_id
from app.util import unix_to_ist, unix_to_utc


def tier_for(confidence: float) -> str:
    t = scoring_config()["confidence_tiers"]
    if confidence >= t["high"]:
        return "High"
    if confidence >= t["medium"]:
        return "Medium"
    if confidence >= t["low"]:
        return "Low"
    return "Weak"


def hop_factor(hops: int) -> float:
    h = {int(k): float(v) for k, v in scoring_config()["hop_decay"].items()}
    return h.get(hops, min(h.values()))


def timing_factor(times: list[int]) -> tuple[float, int | None]:
    cfg = scoring_config()["timing"]
    gaps = [b - a for a, b in zip(times, times[1:])]
    if not gaps:
        return float(cfg["fast_factor"]), None
    worst = max(gaps)
    if worst <= float(cfg["fast_hours"]) * 3600:
        return float(cfg["fast_factor"]), worst
    if worst <= float(cfg["medium_days"]) * 86400:
        return float(cfg["medium_factor"]), worst
    return float(cfg["slow_factor"]), worst


class Scorer:
    def __init__(self, traverser: Traverser, graph: FlowGraph, nodes: dict[str, NodeInfo], max_hops: int):
        self.t = traverser
        self.graph = graph
        self.nodes = nodes
        self.max_hops = max_hops
        self.max_paths = int(scoring_config()["traversal"].get("max_paths_per_target", 50))
        self._share_cache: dict[tuple[str, str, str], Decimal] = {}

    # ------------------------------------------------------------------ shares
    def out_share(self, u: str, v: str, res: TraversalResult) -> Decimal:
        key = ("out", u, v)
        if key in self._share_cache:
            return self._share_cache[key]
        info = self.nodes.get(u)
        if info is None or not info.transfers:
            share = Decimal(0)
        else:
            since = res.window_start if info.direction == "suspect" else info.entry_time
            outs = self.t.eligible_out(info, since, res.window_end)
            groups = self.t.group_value(outs, key=lambda x: x.to_addr)
            share = self.t.share(groups, self.graph.g.nodes[v]["address"])
        self._share_cache[key] = share
        return share

    def in_share(self, target: str, source: str, res: TraversalResult) -> Decimal:
        key = ("in", target, source)
        if key in self._share_cache:
            return self._share_cache[key]
        info = self.nodes.get(target)
        if info is None or not info.transfers:
            share = Decimal(0)
        else:
            until = res.window_end if info.direction == "suspect" else info.entry_time
            ins = self.t.eligible_in(info, None, until)
            groups = self.t.group_value(ins, key=lambda x: x.from_addr)
            share = self.t.share(groups, self.graph.g.nodes[source]["address"])
        self._share_cache[key] = share
        return share

    # ------------------------------------------------------------------ hops
    def _hop_detail(self, u: str, v: str, after: int | None, kind: str) -> dict[str, Any]:
        edges = sorted(self.graph.edges_between(u, v, (kind,)), key=lambda e: e["time"])
        chosen = next((e for e in edges if after is None or e["time"] >= after), edges[0] if edges else None)
        assets: dict[str, Decimal] = defaultdict(Decimal)
        usd = Decimal(0)
        usd_known = True
        for e in edges:
            assets[e["asset"]] += e["amount"]
            if e["usd"] is None:
                usd_known = False
            else:
                usd += e["usd"]
        chain = self.graph.g.nodes[u]["chain"]
        txs = []
        for e in edges[:10]:
            txs.append(
                {
                    "tx_hash": e["tx_hash"],
                    "asset": e["asset"],
                    "amount": format(e["amount"].normalize(), "f"),
                    "usd": format(e["usd"], "f") if e["usd"] is not None else None,
                    "time": e["time"],
                    "time_utc": unix_to_utc(e["time"]),
                    "time_ist": unix_to_ist(e["time"]),
                    "explorer_url": explorer_tx_url(chain, e["tx_hash"]),
                    "raw_ref": e["raw_ref"],
                }
            )
        return {
            "from": self.graph.g.nodes[u]["address"],
            "to": self.graph.g.nodes[v]["address"],
            "chain": chain,
            "time": chosen["time"] if chosen else None,
            "assets": {a: format(x.normalize(), "f") for a, x in assets.items()},
            "usd": format(usd.quantize(Decimal("0.01")), "f") if usd_known and edges else None,
            "transfers": txs,
            "transfer_count": len(edges),
        }

    def _score_path(self, path: list[str], res: TraversalResult, hit, backward: bool = False) -> dict[str, Any]:
        hops = len(path) - 1
        v_share = Decimal(1)
        details = []
        after = None if backward else res.window_start
        times: list[int] = []
        for u, v in zip(path, path[1:]):
            d = self._hop_detail(u, v, after, "funding" if backward else "flow")
            s = self.in_share(v, u, res) if backward else self.out_share(u, v, res)
            d["share"] = float(round(s, 4))
            v_share *= s
            details.append(d)
            if d["time"] is not None:
                times.append(d["time"])
                after = d["time"]
        if backward:
            times = sorted(times)
        T, worst_gap = timing_factor(times)
        H = hop_factor(hops)
        R = float(hit.reliability)
        V = float(v_share)
        score = R * H * math.sqrt(max(V, 0.0)) * T
        end = self.nodes.get(path[0] if backward else path[-1])
        return {
            "hops": hops,
            "nodes": [self.graph.g.nodes[n]["address"] for n in path],
            "chain": self.graph.g.nodes[path[0]]["chain"],
            "factors": {"R": round(R, 4), "H": H, "V": round(V, 6), "T": T, "worst_gap_seconds": worst_gap},
            "score": round(score, 6),
            "label": {
                "address": hit.address,
                "source": hit.source,
                "source_url": hit.source_url,
                "tier": hit.tier,
                "reliability": hit.reliability,
                "role": hit.role,
                "retrieved_at": hit.retrieved_at,
                "inherited_from": hit.inherited_from,
                "evidence": hit.evidence if hit.source.startswith("inferred") else None,
            },
            "via": (
                "inferred deposit address" if hit.source == "inferred_deposit"
                else "common-input cluster" if hit.source == "inferred_btc_cluster"
                else "labelled address"
            ),
            "terminal_reason": end.terminal_reason if end else None,
            "hops_detail": details,
        }

    # ------------------------------------------------------------------ forward candidates
    def forward_paths(self, res: TraversalResult) -> dict[int, list[dict[str, Any]]]:
        suspect = node_id(res.chain, res.suspect)
        d = self.graph.flow_digraph(("flow",))
        by_entity: dict[int, list[dict[str, Any]]] = defaultdict(list)
        hits_by_entity: dict[int, Any] = {}
        for nid, info in self.nodes.items():
            if info.chain != res.chain or info.attribution is None or info.direction in ("backward", "sweep_target"):
                continue
            hit = info.attribution
            if nid == suspect:
                paths = [[suspect]]
            elif d.has_node(suspect) and d.has_node(nid):
                paths = list(itertools.islice(nx.all_simple_paths(d, suspect, nid, cutoff=self.max_hops), self.max_paths))
            else:
                paths = []
            for p in paths:
                rec = self._score_path(p, res, hit)
                if rec["score"] > 0 or nid == suspect:
                    by_entity[hit.entity_id].append(rec)
                    hits_by_entity[hit.entity_id] = hit
        self._hits = getattr(self, "_hits", {})
        self._hits.update(hits_by_entity)
        return by_entity

    def backward_paths(self, res: TraversalResult) -> dict[int, list[dict[str, Any]]]:
        suspect = node_id(res.chain, res.suspect)
        d = self.graph.flow_digraph(("funding",))
        by_entity: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for nid, info in self.nodes.items():
            if info.chain != res.chain or info.direction != "backward" or info.attribution is None:
                continue
            if not (d.has_node(nid) and d.has_node(suspect)):
                continue
            for p in itertools.islice(nx.all_simple_paths(d, nid, suspect, cutoff=self.t.cfg.backward_hops), self.max_paths):
                rec = self._score_path(p, res, info.attribution, backward=True)
                if rec["score"] > 0:
                    by_entity[info.attribution.entity_id].append(rec)
                    self._hits = getattr(self, "_hits", {})
                    self._hits[info.attribution.entity_id] = info.attribution
        return by_entity

    def hit_for(self, entity_id: int):
        return self._hits[entity_id]


def combine(paths_by_entity: dict[int, list[dict[str, Any]]], hits: dict[int, Any], partial: bool) -> list[dict[str, Any]]:
    routable_min = float(scoring_config().get("routable_min_confidence", 50))
    out = []
    for eid, paths in paths_by_entity.items():
        paths = sorted(paths, key=lambda p: (-p["score"], p["hops"]))
        prod = 1.0
        for p in paths:
            prod *= 1.0 - min(1.0, max(0.0, p["score"]))
        conf = round((1.0 - prod) * 100, 1)
        hit = hits[eid]
        best = paths[0]
        cand = {
            "entity_id": eid,
            "entity_name": hit.entity_name,
            "entity_type": hit.entity_type,
            "is_vasp": hit.is_vasp,
            "confidence": conf,
            "tier": tier_for(conf),
            "min_hops": min(p["hops"] for p in paths),
            "path_count": len(paths),
            "routable": bool(hit.is_vasp and conf >= routable_min),
            "best_factors": best["factors"],
            "best_score": best["score"],
            "via": best["via"],
            "label_sources": sorted({p["label"]["source"] for p in paths}),
            "paths": paths[:20],
        }
        cand["raise_note"] = raise_note(cand, partial)
        out.append(cand)
    out.sort(key=lambda c: (-c["confidence"], c["min_hops"], c["entity_name"]))
    return out


def raise_note(c: dict[str, Any], partial: bool) -> str:
    f = c["best_factors"]
    notes = []
    if f["R"] < 0.8:
        notes.append(
            f"The label reached is tier {c['paths'][0]['label']['tier']} (reliability {f['R']:.2f}); "
            "an ADMIN-verified label or the exchange's confirmation of the address would raise R."
        )
    if f["V"] < 0.5:
        notes.append(
            f"Only {f['V'] * 100:.1f}% of the suspect's outflow reaches {c['entity_name']} on the strongest path; "
            "tracing unfollowed counterparties or more hops may add independent paths."
        )
    if c["min_hops"] >= 2:
        notes.append(f"The nearest evidence is {c['min_hops']} hops away (H={f['H']:.2f}); a closer deposit would score higher.")
    if f["T"] < 1.0:
        notes.append("Some hops are more than 72 h apart (T below 1.0); tighter timing would raise the score.")
    if c["path_count"] == 1:
        notes.append("Only one evidence path; independent paths to the same entity raise combined confidence.")
    if partial:
        notes.append("The analysis was partial (budget or provider limits); a narrower window or a re-run may add evidence.")
    if not notes:
        notes.append("Confidence is already high; corroborate with the VASP's KYC response.")
    return " ".join(notes)


def nearest_vasp(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    routable_min = float(scoring_config().get("routable_min_confidence", 50))
    eligible = [c for c in candidates if c["is_vasp"] and c["confidence"] >= routable_min]
    if not eligible:
        return None
    return sorted(eligible, key=lambda c: (c["min_hops"], -c["confidence"]))[0]
