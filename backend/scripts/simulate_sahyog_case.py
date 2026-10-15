"""Sign and post a sample case to the SIMULATED SAHYOG inbound endpoint (demo helper).

    python scripts/simulate_sahyog_case.py                       # wallets from fixtures/demo_cases.json
    python scripts/simulate_sahyog_case.py --wallet TXYZ...:tron --wallet 0xabc...:ethereum
    python scripts/simulate_sahyog_case.py --api http://localhost:8000 --ref SAHYOG-DEMO-0001

The body is signed with HMAC-SHA256 using SAHYOG_SHARED_SECRET from backend/.env and sent
in the X-Signature header. Only real addresses should be used (demo cases are built by
scripts/build_ground_truth.py from recorded real data); nothing here invents wallets.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import _bootstrap  # noqa: F401
import httpx

from app.config import BACKEND_DIR, get_settings
from app.integrations.sahyog import sign

DEMO_CASES = BACKEND_DIR / "fixtures" / "demo_cases.json"


def default_wallets() -> list[dict[str, str | None]]:
    if not DEMO_CASES.exists():
        return []
    cases = json.loads(DEMO_CASES.read_text(encoding="utf-8")).get("cases", [])
    picked = [c for c in cases if c.get("use_in_sahyog_demo")] or cases[:1]
    return [{"address": c["address"], "chain_hint": c.get("chain")} for c in picked]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--ref", default=f"SAHYOG-SIM-{time.strftime('%Y%m%d-%H%M%S')}")
    p.add_argument("--agency", default="Cyber Crime Police Station (demo)")
    p.add_argument("--officer", default="Investigating Officer (demo)")
    p.add_argument("--complaint-ref", default=None, help="e.g. the NCRP acknowledgement number")
    p.add_argument("--wallet", action="append", default=[], help="address[:chain_hint]; repeatable")
    args = p.parse_args(argv)

    secret = get_settings().SAHYOG_SHARED_SECRET
    if not secret:
        print("SAHYOG_SHARED_SECRET is not set in backend/.env", file=sys.stderr)
        return 2
    wallets = []
    for w in args.wallet:
        addr, _, chain = w.partition(":")
        wallets.append({"address": addr.strip(), "chain_hint": chain.strip() or None})
    if not wallets:
        wallets = default_wallets()
    if not wallets:
        print(f"No --wallet given and {DEMO_CASES} does not exist. Run scripts/build_ground_truth.py first.", file=sys.stderr)
        return 2
    payload = {
        "sahyog_ref": args.ref,
        "agency": args.agency,
        "officer": args.officer,
        "complaint_ref": args.complaint_ref,
        "wallets": wallets,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Signature": sign(body, secret)}
    try:
        r = httpx.post(f"{args.api.rstrip('/')}/api/integrations/sahyog/cases", content=body, headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        print(f"Could not reach {args.api}: {exc}", file=sys.stderr)
        return 1
    print(f"HTTP {r.status_code} (SIMULATED SAHYOG)")
    print(json.dumps(r.json(), indent=2))
    return 0 if r.status_code < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
