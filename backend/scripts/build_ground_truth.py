"""Build evaluation ground truth and the five demo cases from REAL chain data (SPEC §14).

    cd backend
    python scripts/build_ground_truth.py                     # defaults: ethereum, tron, bitcoin
    python scripts/build_ground_truth.py --chains ethereum --hot-per-chain 10 --negatives 30

Needs network access and API keys in backend/.env (ETHERSCAN_API_KEY for EVM chains,
TRONGRID_API_KEY recommended for TRON). Run it with DATA_MODE=live or DATA_MODE=record.

Writes:
  eval/ground_truth.json      positives (suspect -> expected exchange, hidden deposit address)
                              and verified negative controls
  fixtures/demo_cases.json    the five demo cases (addresses only; record them next with
                              scripts/record_fixtures.py)

Every address comes from provider responses and the committed label seed; nothing is typed
from memory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.config import BACKEND_DIR, get_settings
from app.db import init_db, iso, session_scope, utcnow
from app.engine import groundtruth as gt
from app.engine.datasource import ProductionLabels, ProviderDataSource
from app.engine.pipeline import analyse
from app.errors import AppError
from app.labels.store import label_counts, load_seed_csv
from app.config import SEED_CSV
from app.providers.context import AnalysisContext, use_context

GT_PATH = BACKEND_DIR / "eval" / "ground_truth.json"
DEMO_PATH = BACKEND_DIR / "fixtures" / "demo_cases.json"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def build(args: argparse.Namespace) -> dict:
    chains = [c.strip() for c in args.chains.split(",") if c.strip()]
    positives: list[gt.Positive] = []
    nearby: dict[str, list[str]] = {}
    btc_case = None
    breakpoint = None
    with session_scope() as db:
        labels = ProductionLabels(db, enable_live=False)
        ds = ProviderDataSource()
        for chain in chains:
            hots = gt.hot_wallets(db, chain, args.hot_per_chain, seed=args.seed)
            log(f"[{chain}] {len(hots)} hot wallets sampled")
            for hot in hots:
                try:
                    if chain == "bitcoin":
                        if btc_case is None:
                            btc_case = await gt.btc_cluster_case(ds, labels, hot)
                            if btc_case:
                                positives.append(btc_case)
                                log(f"  bitcoin cluster case via {hot.entity_name}")
                        continue
                    pos, near = await gt.positives_for_hot(ds, labels, hot, per_deposit=args.per_deposit, two_hop=True)
                except AppError as exc:
                    log(f"  {hot.entity_name} {hot.address}: {exc.code}: {exc.detail[:120]}")
                    continue
                positives.extend(pos)
                nearby.setdefault(chain, []).extend(near)
                log(f"  {hot.entity_name}: {len(pos)} positive(s)")
                if len([p for p in positives if p.chain == chain]) >= args.max_positives_per_chain:
                    break
        if "ethereum" in chains:
            for contract in gt.breakpoint_contracts(db):
                try:
                    breakpoint = await gt.breakpoint_case(ds, labels, contract)
                except AppError:
                    breakpoint = None
                if breakpoint:
                    log(f"[ethereum] breakpoint case via {contract.entity_name}")
                    break

    # Negative controls: nearby unlabelled wallets whose traced 3-hop graph reaches no
    # labelled VASP (label-only check; inferred labels do not count).
    negatives: list[dict] = []
    for chain, cands in nearby.items():
        for addr in list(dict.fromkeys(cands))[: args.negatives * 3]:
            if len([n for n in negatives if n["chain"] == chain]) >= args.negatives:
                break
            with session_scope() as db:
                try:
                    r = await analyse(addr, ProviderDataSource(), ProductionLabels(db, enable_live=False), chain_hint=chain, max_hops=3, include_funding=False)
                except AppError:
                    continue
            if not r["suspect"]["active_chains"] or r["summary"]["partial"]:
                continue
            if gt.label_only_vasp_within(r, 3):
                continue
            negatives.append({"id": f"{chain}-neg-{addr[-8:]}", "chain": chain, "address": addr, "verified": "no labelled VASP within 3 hops (label-only check)"})
        log(f"[{chain}] {len([n for n in negatives if n['chain'] == chain])} negative control(s)")

    return {"positives": positives, "negatives": negatives, "btc_case": btc_case, "breakpoint": breakpoint}


def demo_cases(found: dict) -> list[dict]:
    pos: list[gt.Positive] = found["positives"]
    cases = []
    eth1 = next((p for p in pos if p.chain == "ethereum" and p.hops_upstream == 1 and p.method == "deposit_sweep"), None)
    if eth1:
        cases.append({
            "id": "demo-1-eth-direct-deposit", "title": "Direct deposit to an inferred exchange deposit address (Ethereum)",
            "chain": "ethereum", "address": eth1.suspect, "use_in_sahyog_demo": True,
            "expectation": f"Nearest VASP {eth1.expected_entity_name}, 1 hop, via inferred deposit address {eth1.deposit_address}",
            "ground_truth": eth1.to_dict(),
        })
    tron = next((p for p in pos if p.chain == "tron" and p.hops_upstream == 2), None) or next((p for p in pos if p.chain == "tron"), None)
    if tron:
        cases.append({
            "id": "demo-2-tron-usdt-multihop", "title": "Multi-hop USDT-TRC20 path to a labelled exchange (TRON)",
            "chain": "tron", "address": tron.suspect, "use_in_sahyog_demo": True,
            "expectation": f"Nearest VASP {tron.expected_entity_name} at {tron.hops_upstream + 1} hop(s) or fewer, reaching hot wallet {tron.hot_wallet}",
            "ground_truth": tron.to_dict(),
        })
    if found["btc_case"]:
        b = found["btc_case"]
        cases.append({
            "id": "demo-3-btc-common-input", "title": "Bitcoin case resolved by common-input clustering",
            "chain": "bitcoin", "address": b.suspect, "use_in_sahyog_demo": False,
            "expectation": f"Nearest VASP {b.expected_entity_name} via common-input cluster address {b.deposit_address}",
            "ground_truth": b.to_dict(),
        })
    if found["breakpoint"]:
        k = found["breakpoint"]
        cases.append({
            "id": "demo-4-mixer-or-bridge", "title": f"Path ending at a mixer or bridge breakpoint ({k['service']})",
            "chain": k["chain"], "address": k["suspect"], "use_in_sahyog_demo": False,
            "expectation": f"Breakpoint at {k['service']} ({k['service_address']}); MIXER_EXPOSURE or CROSS_CHAIN_BREAKPOINT typology",
            "ground_truth": k,
        })
    if found["negatives"]:
        n = found["negatives"][0]
        cases.append({
            "id": "demo-5-honest-no-match", "title": "Honest no-match",
            "chain": n["chain"], "address": n["address"], "use_in_sahyog_demo": False,
            "expectation": "No VASP at confidence >= 50; the report says so plainly",
            "ground_truth": n,
        })
    return cases


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chains", default="ethereum,tron,bitcoin")
    p.add_argument("--hot-per-chain", type=int, default=8)
    p.add_argument("--per-deposit", type=int, default=2)
    p.add_argument("--max-positives-per-chain", type=int, default=40)
    p.add_argument("--negatives", type=int, default=15, help="negative controls per chain")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--budget", type=int, default=20000, help="API call budget for the whole run")
    args = p.parse_args(argv)

    settings = get_settings()
    if settings.data_mode == "replay":
        log("Ground truth must come from live data: set DATA_MODE=live or DATA_MODE=record.")
        return 2
    if any(c in args.chains for c in ("ethereum", "polygon", "arbitrum", "bsc")) and not settings.ETHERSCAN_API_KEY:
        log("ETHERSCAN_API_KEY is missing in backend/.env (https://etherscan.io/myapikey).")
        return 2
    init_db()
    with session_scope() as db:
        if not label_counts(db):
            load_seed_csv(db, SEED_CSV)
    ctx = AnalysisContext(analysis_id=None, api_call_budget=args.budget)
    with use_context(ctx):
        found = asyncio.run(build(args))
    now = iso(utcnow())
    GT_PATH.parent.mkdir(parents=True, exist_ok=True)
    GT_PATH.write_text(json.dumps({
        "generated_at": now,
        "data_mode": settings.data_mode,
        "method": "tier A/B exchange hot wallets -> sweep-verified deposit addresses -> funders 1-2 hops upstream; negatives verified label-only",
        "params": vars(args),
        "positives": [x.to_dict() for x in found["positives"]],
        "negatives": found["negatives"],
    }, indent=2) + "\n", encoding="utf-8")
    cases = demo_cases(found)
    DEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEMO_PATH.write_text(json.dumps({"generated_at": now, "cases": cases}, indent=2) + "\n", encoding="utf-8")
    print(f"positives: {len(found['positives'])}  negatives: {len(found['negatives'])}  demo cases: {len(cases)}/5  api calls: {ctx.api_calls}")
    print(f"wrote {GT_PATH.relative_to(BACKEND_DIR)} and {DEMO_PATH.relative_to(BACKEND_DIR)}")
    missing = 5 - len(cases)
    if missing:
        print(f"{missing} demo case(s) not found in this sample; re-run with a larger --hot-per-chain or another --seed.")
    print("Next: python scripts/record_fixtures.py  (records the demo cases for offline replay)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
