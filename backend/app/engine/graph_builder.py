"""Fund-flow graph (NetworkX MultiDiGraph): nodes are addresses (chain-qualified),
edges are transfers carrying tx hash, asset, amount, USD estimate and time."""
from __future__ import annotations

import math
from collections import defaultdict
from decimal import Decimal
from typing import Any

import networkx as nx

from app.config import explorer_address_url, explorer_tx_url
from app.engine.types import NodeInfo, node_id
from app.providers.base import Transfer

EDGE_KINDS = ("flow", "funding", "sweep", "gas", "poisoning")


class FlowGraph:
    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()
        self._edge_seen: set[tuple] = set()

    def add_node(self, info: NodeInfo) -> None:
        if not self.g.has_node(info.id):
            self.g.add_node(info.id, chain=info.chain, address=info.address)

    def add_transfer(self, t: Transfer, kind: str = "flow") -> bool:
        u, v = node_id(t.chain, t.from_addr), node_id(t.chain, t.to_addr)
        sig = (u, v, t.tx_hash, t.asset, t.amount, kind)
        if sig in self._edge_seen:
            return False
        self._edge_seen.add(sig)
        for n, a in ((u, t.from_addr), (v, t.to_addr)):
            if not self.g.has_node(n):
                self.g.add_node(n, chain=t.chain, address=a)
        self.g.add_edge(
            u,
            v,
            key=f"{t.tx_hash}:{len(self._edge_seen)}",
            tx_hash=t.tx_hash,
            asset=t.asset,
            token_contract=t.token_contract,
            amount=Decimal(t.amount),
            usd=t.usd_dec,
            time=t.block_time,
            block=t.block_number,
            kind=kind,
            chain=t.chain,
            raw_ref=t.raw_ref,
        )
        return True

    def flow_digraph(self, kinds: tuple[str, ...] = ("flow",)) -> nx.DiGraph:
        """Collapse parallel edges of the given kinds into a simple DiGraph."""
        d = nx.DiGraph()
        for u, v, data in self.g.edges(data=True):
            if data["kind"] not in kinds:
                continue
            if d.has_edge(u, v):
                d[u][v]["transfers"].append(data)
            else:
                d.add_edge(u, v, transfers=[data])
        return d

    def edges_between(self, u: str, v: str, kinds: tuple[str, ...] = ("flow",)) -> list[dict[str, Any]]:
        if not self.g.has_edge(u, v):
            return []
        return [d for d in self.g.get_edge_data(u, v).values() if d["kind"] in kinds]


def _node_role(info: NodeInfo | None) -> str:
    if info is None:
        return "unknown"
    if info.direction == "suspect":
        return "suspect"
    if info.deposit is not None:
        return "deposit_inferred"
    if info.cluster is not None:
        return "cluster_inferred"
    hit = info.attribution or (info.labels[0] if info.labels else None)
    if hit is not None:
        if hit.is_vasp:
            return "vasp"
        return {
            "MIXER": "mixer",
            "BRIDGE": "bridge",
            "SANCTIONED": "sanctioned",
            "DEX": "dex",
        }.get(hit.entity_type, "risk" if hit.entity_type in ("SCAM", "RANSOMWARE", "DARKNET_MARKET", "GAMBLING") else "labelled")
    if info.high_activity:
        return "high_activity"
    return "unknown"


def serialise(graph: FlowGraph, nodes: dict[str, NodeInfo]) -> dict[str, Any]:
    out_nodes = []
    for nid, data in sorted(graph.g.nodes(data=True)):
        info = nodes.get(nid)
        hit = None
        if info is not None:
            hit = info.attribution or (info.labels[0] if info.labels else None)
        out_nodes.append(
            {
                "id": nid,
                "chain": data["chain"],
                "address": data["address"],
                "hop": info.hop if info else None,
                "direction": info.direction if info else None,
                "role": _node_role(info),
                "expanded": bool(info and info.expanded),
                "terminal_reason": info.terminal_reason if info else None,
                "inferred": (
                    "deposit" if info is not None and info.deposit is not None
                    else "cluster" if info is not None and info.cluster is not None
                    else None
                ),
                "label": (
                    {
                        "entity_id": hit.entity_id,
                        "entity_name": hit.entity_name,
                        "entity_type": hit.entity_type,
                        "is_vasp": hit.is_vasp,
                        "role": hit.role,
                        "source": hit.source,
                        "source_url": hit.source_url,
                        "reliability": hit.reliability,
                        "tier": hit.tier,
                        "retrieved_at": hit.retrieved_at,
                        "inherited_from": hit.inherited_from,
                    }
                    if hit
                    else None
                ),
                "labels": [h.to_dict() for h in (info.labels if info else [])][:6],
                "activity": info.activity if info else {},
                "explorer_url": explorer_address_url(data["chain"], data["address"]),
            }
        )
    agg: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for u, v, d in graph.g.edges(data=True):
        k = (u, v, d["kind"], d["asset"])
        e = agg.setdefault(
            k,
            {
                "id": f"e{len(agg)}",
                "source": u,
                "target": v,
                "kind": d["kind"],
                "chain": d["chain"],
                "asset": d["asset"],
                "amount": Decimal(0),
                "usd": Decimal(0),
                "usd_known": True,
                "tx_hashes": [],
                "first_time": d["time"],
                "last_time": d["time"],
            },
        )
        e["amount"] += d["amount"]
        if d["usd"] is None:
            e["usd_known"] = False
        else:
            e["usd"] += d["usd"]
        e["tx_hashes"].append(d["tx_hash"])
        e["first_time"] = min(e["first_time"], d["time"])
        e["last_time"] = max(e["last_time"], d["time"])
    out_edges = []
    for e in sorted(agg.values(), key=lambda x: (x["source"], x["target"], x["kind"], x["asset"])):
        txs = sorted(set(e["tx_hashes"]))
        out_edges.append(
            {
                "id": e["id"],
                "source": e["source"],
                "target": e["target"],
                "kind": e["kind"],
                "chain": e["chain"],
                "asset": e["asset"],
                "amount": format(e["amount"].normalize(), "f"),
                "usd": format(e["usd"].quantize(Decimal("0.01")), "f") if e["usd_known"] else None,
                "tx_count": len(txs),
                "tx_hashes": txs[:25],
                "tx_links": [{"tx_hash": h, "explorer_url": explorer_tx_url(e["chain"], h)} for h in txs[:25]],
                "first_time": e["first_time"],
                "last_time": e["last_time"],
                "explorer_url": explorer_tx_url(e["chain"], txs[0]) if txs else None,
            }
        )
    return {"nodes": out_nodes, "edges": out_edges}


def to_cytoscape(graph_json: dict[str, Any]) -> dict[str, Any]:
    """Cytoscape.js elements from the serialised analysis graph."""
    elements = []
    for n in graph_json.get("nodes", []):
        label = n["label"]["entity_name"] if n.get("label") else ""
        elements.append(
            {
                "group": "nodes",
                "data": {
                    "id": n["id"],
                    "address": n["address"],
                    "chain": n["chain"],
                    "role": n["role"],
                    "hop": n["hop"],
                    "display": label or f"{n['address'][:6]}…{n['address'][-4:]}",
                    "entity": label,
                    "inferred": n.get("inferred"),
                },
                "classes": " ".join([n["role"]] + ([f"inferred inferred_{n['inferred']}"] if n.get("inferred") else [])),
            }
        )
    for e in graph_json.get("edges", []):
        usd = float(e["usd"]) if e.get("usd") else None
        width = 1.0 + (math.log10(usd + 1) if usd else 0.5)
        elements.append(
            {
                "group": "edges",
                "data": {
                    "id": e["id"],
                    "source": e["source"],
                    "target": e["target"],
                    "kind": e["kind"],
                    "label": f"{_short_amount(e['amount'])} {e['asset']}",
                    "width": round(min(width, 9.0), 2),
                    "usd": e.get("usd"),
                    "tx_count": e["tx_count"],
                },
                "classes": e["kind"],
            }
        )
    return {"elements": elements}


def _short_amount(amount: str) -> str:
    try:
        d = Decimal(amount)
    except Exception:  # noqa: BLE001
        return amount
    if d >= 1000:
        return f"{d:,.0f}"
    if d >= 1:
        return f"{d:,.2f}"
    return f"{d:.6f}".rstrip("0").rstrip(".")


def outflow_totals(transfers: list[Transfer], address: str, since: int | None, until: int | None) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for t in transfers:
        if t.from_addr == address and t.in_scope and (since is None or t.block_time >= since) and (until is None or t.block_time <= until):
            totals[t.asset] += t.amount_dec
    return dict(totals)
