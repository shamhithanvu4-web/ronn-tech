"""Analysis pipeline: validate -> fetch -> build graph -> infer deposits -> score -> done.

CLI:  python -m app.engine.pipeline <address> [--chain C] [--max-hops N] [--no-funding] [--json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from decimal import Decimal
from typing import Any, Callable

from app.config import METHODOLOGY_VERSION, PRODUCT_NAME, config_sha256, get_settings, scoring_config
from app.db import iso, utcnow
from app.engine.graph_builder import FlowGraph, serialise
from app.engine.risk import assess
from app.engine.scoring import Scorer, combine, nearest_vasp, tier_for
from app.engine.traversal import TraversalConfig, Traverser, family_of
from app.engine.typologies import detect
from app.engine.types import ChainCheck, DataSource, LabelLookup, NodeInfo, node_id
from app.engine.validate import validate_address
from app.errors import AppError, KeyMissing, PlanRequired, ProviderError, ReplayMiss
from app.providers import chain_enabled
from app.providers.context import BudgetExceeded, current
from app.util import short_addr

SCHEMA = "ronn-tech/analysis/v1"
ProgressFn = Callable[[str, int], None]


async def _suspect_transfers(ds: DataSource, chain: str, address: str, limit: int) -> tuple[ChainCheck, list]:
    try:
        rows = await ds.get_transfers(chain, address, None, None, limit)
    except PlanRequired as exc:
        return ChainCheck(chain, "requires_paid_plan", exc.detail), []
    except KeyMissing as exc:
        return ChainCheck(chain, "key_missing", exc.detail), []
    except BudgetExceeded as exc:
        return ChainCheck(chain, "error", exc.detail), []
    except ReplayMiss:
        return ChainCheck(chain, "not_recorded", "no recorded data for this chain (replay mode)"), []
    except AppError as exc:
        return ChainCheck(chain, "error", f"{exc.code}: {exc.detail}"), []
    in_scope = [t for t in rows if t.in_scope and t.amount_dec > 0]
    if not in_scope:
        return ChainCheck(chain, "inactive", "no in-scope transfers", transfers=len(rows)), rows
    times = [t.block_time for t in in_scope]
    return ChainCheck(chain, "active", None, transfers=len(rows), first_seen=min(times), last_seen=max(times)), rows


def _breakpoints(nodes: dict[str, NodeInfo], graph: FlowGraph) -> list[dict[str, Any]]:
    out = []
    for nid, info in sorted(nodes.items()):
        bridges = [h for h in info.labels if h.entity_type == "BRIDGE"]
        if not bridges or info.direction not in ("forward",):
            continue
        edges = [d for _u, _v, d in graph.g.in_edges(nid, data=True) if d["kind"] == "flow"]
        if not edges:
            continue
        amounts: dict[str, Decimal] = {}
        for d in edges:
            amounts[d["asset"]] = amounts.get(d["asset"], Decimal(0)) + d["amount"]
        out.append(
            {
                "bridge": bridges[0].entity_name,
                "chain": info.chain,
                "address": info.address,
                "hop": info.hop,
                "amounts": {a: format(v.normalize(), "f") for a, v in amounts.items()},
                "first_time": min(d["time"] for d in edges),
                "tx_hashes": sorted({d["tx_hash"] for d in edges})[:20],
                "label_source": bridges[0].source,
                "recommendation": "Decentralised bridge: identify the destination chain and address from the bridge transaction and continue tracing there.",
            }
        )
    return out


def _possible_services(nodes: dict[str, NodeInfo], scorer: Scorer, results) -> list[dict[str, Any]]:
    out = []
    for res in results:
        sid = node_id(res.chain, res.suspect)
        d = scorer.graph.flow_digraph(("flow",))
        for nid, info in sorted(nodes.items()):
            if info.chain != res.chain or not info.high_activity or info.attribution is not None or nid == sid:
                continue
            best = Decimal(0)
            if d.has_node(sid) and d.has_node(nid):
                import itertools

                import networkx as nx

                for p in itertools.islice(nx.all_simple_paths(d, sid, nid, cutoff=scorer.max_hops), 20):
                    s = Decimal(1)
                    for u, v in zip(p, p[1:]):
                        s *= scorer.out_share(u, v, res)
                    best = max(best, s)
            out.append(
                {
                    "chain": info.chain,
                    "address": info.address,
                    "hop": info.hop,
                    "activity": info.activity,
                    "value_share": float(round(best, 4)),
                    "note": "Unlabelled high-activity service (possible VASP). Not routable until identified.",
                }
            )
    return out


def _recommended_action(nearest: dict | None, candidates: list[dict], breakpoints: list, any_activity: bool) -> str:
    if nearest is not None:
        via = nearest["paths"][0]
        target = via["nodes"][-1]
        return (
            f"Draft an information-disclosure request to {nearest['entity_name']} for {short_addr(target, 8, 6)} "
            "(KYC, linked bank/UPI accounts, deposit and withdrawal history, login IPs and devices, current balance); "
            "consider a freeze/hold request if funds may remain. Attribution is an investigative lead, not proof of ownership."
        )
    if breakpoints:
        return "Funds reached a decentralised bridge. Identify the destination chain from the bridge transaction and continue tracing there."
    if candidates:
        return "No VASP reached the routable threshold (confidence 50). Review the evidence paths, extend hops or the window, or watch the wallet."
    if not any_activity:
        return "No in-scope activity found on the supported chains. Check the address and chain, or add it to the watchlist."
    return "No VASP found within the traced hops. Add the wallet to the watchlist to be alerted when funds move."


async def analyse(
    address: str,
    ds: DataSource,
    labels: LabelLookup,
    chain_hint: str | None = None,
    max_hops: int | None = None,
    include_funding: bool = True,
    window_days: int | None = None,
    window_start: int | None = None,
    progress: ProgressFn | None = None,
    analysis_id: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    progress = progress or (lambda _s, _p: None)
    progress("validating", 5)
    info = validate_address(address, chain_hint)
    cfg = TraversalConfig.from_config(
        max_hops=max_hops, include_funding=include_funding, window_days=window_days, window_start=window_start
    )
    chains = [c for c in info.chains if chain_enabled(c)]
    skipped = [ChainCheck(c, "disabled", "provider key not configured") for c in info.chains if c not in chains]

    progress("fetching", 12)
    checks_rows = await asyncio.gather(*(_suspect_transfers(ds, c, info.address, cfg.page_limit) for c in chains))
    checks = [c for c, _ in checks_rows] + skipped
    active = [(c, rows) for c, rows in checks_rows if c.status == "active"]
    if not active and checks and all(c.status in ("requires_paid_plan", "key_missing", "error", "disabled", "not_recorded") for c in checks):
        statuses = {c.status for c in checks}
        detail = "; ".join(f"{c.chain}: {c.detail}" for c in checks if c.detail)
        if statuses <= {"not_recorded", "disabled"} and "not_recorded" in statuses:
            raise ReplayMiss(f"DATA_MODE=replay and no recorded data exists for this address. {detail}")
        if statuses == {"requires_paid_plan"}:
            raise PlanRequired(detail)
        if statuses & {"key_missing", "disabled"} and statuses <= {"key_missing", "disabled", "requires_paid_plan"}:
            raise KeyMissing(detail)
        raise ProviderError(f"No chain could be queried: {detail}")

    graph = FlowGraph()
    nodes: dict[str, NodeInfo] = {}
    traverser = Traverser(ds, labels, cfg, graph, nodes, progress)
    results = []
    for check, rows in active:
        progress("building_graph", 25)
        results.append(await traverser.run(check.chain, info.address, rows))

    # Sanctions screening of the suspect (only when the Chainalysis key is configured).
    screen = getattr(labels, "sanctions_screen", None)
    if screen is not None:
        for res in results:
            hit = await screen(res.chain, info.address)
            if hit is not None:
                sn = nodes[node_id(res.chain, info.address)]
                sn.labels = [hit] + sn.labels

    progress("inferring_deposits", 70)
    scorer = Scorer(traverser, graph, nodes, cfg.max_hops)
    fwd: dict[int, list] = {}
    back: dict[int, list] = {}
    for res in results:
        for eid, paths in scorer.forward_paths(res).items():
            fwd.setdefault(eid, []).extend(paths)
        if cfg.include_funding:
            for eid, paths in scorer.backward_paths(res).items():
                back.setdefault(eid, []).extend(paths)
    partial = any(r.partial for r in results)
    hits = getattr(scorer, "_hits", {})

    progress("scoring", 85)
    candidates = combine(fwd, hits, partial)
    funding = combine(back, hits, partial)
    nearest = nearest_vasp(candidates)
    typologies = detect(nodes, graph, results, traverser)
    risk = assess(nodes, scorer, results, typologies)
    breakpoints = _breakpoints(nodes, graph)
    services = _possible_services(nodes, scorer, results)

    deposits = [n.deposit.to_dict() | {"hop": n.hop} for n in nodes.values() if n.deposit is not None]
    clusters = [n.cluster.to_dict() | {"hop": n.hop} for n in nodes.values() if n.cluster is not None]
    linked = sorted({w for d in deposits for w in d["linked_wallets"]})
    suspect_nodes = [nodes[node_id(r.chain, info.address)] for r in results]
    hop0 = {
        "labels": [h.to_dict() for n in suspect_nodes for h in n.labels],
        "deposit_inference": [n.deposit.to_dict() for n in suspect_nodes if n.deposit],
        "cluster_inference": [n.cluster.to_dict() for n in suspect_nodes if n.cluster],
    }
    active_chains = [c.chain for c in checks if c.status == "active"]
    ctx = current()
    evidence = sorted((ctx.evidence if ctx else []), key=lambda e: (e["chain"], e["url"]))
    label_sources: dict[str, dict[str, Any]] = {}
    for n in nodes.values():
        for h in n.labels:
            key = f"{h.source}|{h.source_url}"
            if key not in label_sources:
                label_sources[key] = {"source": h.source, "source_url": h.source_url, "tier": h.tier, "retrieved_at": h.retrieved_at}
    partial_reasons = sorted({r for res in results for r in res.partial_reasons})
    nearest_summary = None
    if nearest is not None:
        best = nearest["paths"][0]
        nearest_summary = {
            "entity_id": nearest["entity_id"],
            "entity_name": nearest["entity_name"],
            "entity_type": nearest["entity_type"],
            "confidence": nearest["confidence"],
            "tier": nearest["tier"],
            "hops": nearest["min_hops"],
            "via": best["via"],
            "target_address": best["nodes"][-1],
            "chain": best["chain"],
        }
    result = {
        "schema": SCHEMA,
        "product": PRODUCT_NAME,
        "analysis_id": analysis_id,
        "methodology_version": METHODOLOGY_VERSION,
        "scoring_config_sha256": config_sha256("scoring.yaml"),
        "data_mode": get_settings().data_mode,
        "generated_at": iso(utcnow()),
        "suspect": {
            "address": info.address,
            "family": info.family,
            "kind": info.kind,
            "chains_checked": [c.to_dict() for c in checks],
            "active_chains": active_chains,
        },
        "params": {
            "max_hops": cfg.max_hops,
            "backward_hops": cfg.backward_hops,
            "include_funding": cfg.include_funding,
            "window_days": cfg.window_days,
            "window_start": results[0].window_start if results else None,
            "window_end": results[0].window_end if results else None,
            "dust_usd": format(cfg.dust_usd, "f"),
            "top_counterparties": cfg.top_k,
            "min_outflow_share": format(cfg.min_share, "f"),
            "node_budget": cfg.node_budget,
            "api_call_budget": ctx.api_call_budget if ctx else None,
        },
        "summary": {
            "nearest_vasp": nearest_summary,
            "risk_level": risk["level"],
            "partial": partial,
            "partial_reasons": partial_reasons,
            "recommended_action": _recommended_action(nearest, candidates, breakpoints, bool(active_chains)),
            "candidate_count": len(candidates),
            "node_count": len(nodes),
            "api_calls": ctx.api_calls if ctx else None,
            "runtime_seconds": round(time.monotonic() - started, 2),
        },
        "hop0": hop0,
        "candidates": candidates,
        "funding_sources": funding,
        "possible_unlabelled_services": services,
        "deposit_inferences": deposits,
        "cluster_inferences": clusters,
        "linked_wallets": linked,
        "typologies": typologies,
        "risk": risk,
        "cross_chain": {
            "evm_mapping": [c.to_dict() for c in checks] if info.family == "evm" else [],
            "same_key_holder_note": (
                "The same EVM address is active on several chains; it is controlled by one key holder."
                if info.family == "evm" and len(active_chains) > 1
                else None
            ),
            "breakpoints": breakpoints,
        },
        "address_poisoning_candidates": [p for r in results for p in r.poisoning][:100],
        "unfollowed": {k: v for r in results for k, v in r.unfollowed.items()},
        "errors": [e for r in results for e in r.errors][:50],
        "graph": serialise(graph, nodes),
        "evidence": {
            "raw_responses": evidence,
            "label_sources": sorted(label_sources.values(), key=lambda x: (x["source"], x["source_url"] or "")),
        },
    }
    progress("done", 100)
    return result


def candidate_table(result: dict[str, Any]) -> str:
    lines = []
    s = result["summary"]
    nv = s["nearest_vasp"]
    lines.append(f"Suspect {result['suspect']['address']} ({result['suspect']['family']}); active chains: {', '.join(result['suspect']['active_chains']) or 'none'}")
    for c in result["suspect"]["chains_checked"]:
        lines.append(f"  chain {c['chain']:9s} {c['status']:20s} {c['detail'] or ''}")
    if nv:
        lines.append(f"Nearest VASP: {nv['entity_name']} · Confidence {nv['confidence']:.0f} · {nv['tier']} · {nv['hops']} hop(s) · via {nv['via']} {nv['target_address']}")
    else:
        lines.append("Nearest VASP: none at confidence >= 50")
    lines.append(f"Risk: {s['risk_level']}   partial={s['partial']} {s['partial_reasons']}   nodes={s['node_count']} api_calls={s['api_calls']}")
    lines.append("")
    lines.append(f"{'Candidate':32s} {'Type':12s} {'Conf':>5s} {'Tier':7s} {'Hops':>4s} {'Paths':>5s}  R     H     V        T")
    for c in result["candidates"][:10]:
        f = c["best_factors"]
        lines.append(
            f"{c['entity_name'][:32]:32s} {c['entity_type'][:12]:12s} {c['confidence']:5.1f} {c['tier']:7s} {c['min_hops']:4d} {c['path_count']:5d}  "
            f"{f['R']:.2f}  {f['H']:.2f}  {f['V']:.4f}  {f['T']:.1f}"
        )
        lines.append(f"    what would raise this: {c['raise_note']}")
    if result["funding_sources"]:
        lines.append("Funding-source VASPs:")
        for c in result["funding_sources"][:5]:
            lines.append(f"  {c['entity_name']} {c['confidence']:.1f} ({c['tier']}) {c['min_hops']} hop(s)")
    if result["typologies"]:
        lines.append("Typologies: " + ", ".join(t["code"] for t in result["typologies"]))
    lines.append(f"Recommended action: {s['recommended_action']}")
    return "\n".join(lines)


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one attribution analysis and print candidates.")
    parser.add_argument("address")
    parser.add_argument("--chain")
    parser.add_argument("--max-hops", type=int)
    parser.add_argument("--no-funding", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the full result JSON")
    args = parser.parse_args(argv)

    from app.db import init_db, session_scope
    from app.engine.datasource import ProductionLabels, ProviderDataSource
    from app.providers.context import AnalysisContext, use_context

    init_db()
    budget = int(scoring_config()["traversal"].get("api_call_budget", 600))
    with session_scope() as db:
        ctx = AnalysisContext(analysis_id=None, api_call_budget=budget)
        with use_context(ctx):
            try:
                result = asyncio.run(
                    analyse(
                        args.address, ProviderDataSource(), ProductionLabels(db), chain_hint=args.chain,
                        max_hops=args.max_hops, include_funding=not args.no_funding,
                        progress=lambda s, p: print(f"[{p:3d}%] {s}", file=sys.stderr),
                    )
                )
            except AppError as exc:
                print(f"error ({exc.code}): {exc.detail}", file=sys.stderr)
                return 2
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(candidate_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = ["analyse", "candidate_table", "tier_for", "family_of"]
