"""Evaluation metrics (SPEC §14), shared by eval/run_eval.py and its tests."""
from __future__ import annotations

import time

from app.db import session_scope
from app.engine import jobs
from app.labels.store import name_key
from app.models import Analysis


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def run_one(address: str, chain: str, hidden: set[tuple[str, str]]) -> tuple[dict | None, float, str | None]:
    aid = jobs.create_analysis(address, "eval", chain=chain)
    t0 = time.monotonic()
    result = jobs.run_analysis(aid, hidden=hidden, persist_inferred=False, enable_live=False)
    dt = time.monotonic() - t0
    err = None
    if result is None:
        with session_scope() as db:
            a = db.get(Analysis, aid)
            err = f"{a.error_code}: {a.error}" if a else "unknown"
    return result, dt, err


def evaluate(gt: dict, limit: int | None) -> dict:
    rows = []
    positives = gt.get("positives", [])[: limit or None]
    negatives = gt.get("negatives", [])[: limit or None]
    for i, p in enumerate(positives, 1):
        hidden = {(p["chain"], p["deposit_address"])}
        if p["chain"] in ("ethereum", "polygon", "arbitrum", "bsc"):
            hidden.add((p["chain"], p["deposit_address"].lower()))
        result, dt, err = run_one(p["suspect"], p["chain"], hidden)
        cands = [c for c in (result or {}).get("candidates", []) if c["is_vasp"]]
        names = [name_key(c["entity_name"]) for c in cands]
        want = name_key(p["expected_entity_name"])
        nv = (result or {}).get("summary", {}).get("nearest_vasp")
        rows.append({
            "kind": "positive", "id": p["id"], "chain": p["chain"], "hops_upstream": p["hops_upstream"], "method": p.get("method"),
            "expected": p["expected_entity_name"], "top": cands[0]["entity_name"] if cands else None,
            "top1": bool(names[:1] and names[0] == want), "top3": want in names[:3],
            "nearest": nv["entity_name"] if nv else None, "nearest_correct": bool(nv and name_key(nv["entity_name"]) == want),
            "tier": nv["tier"] if nv else None, "confidence": nv["confidence"] if nv else None,
            "partial": (result or {}).get("summary", {}).get("partial"), "runtime": round(dt, 2), "error": err,
        })
        print(f"[{i}/{len(positives)}] {p['id']}: expected {p['expected_entity_name']} -> {rows[-1]['nearest'] or rows[-1]['top'] or 'none'}"
              f"{' (error ' + err + ')' if err else ''}", flush=True)
    for i, n in enumerate(negatives, 1):
        result, dt, err = run_one(n["address"], n["chain"], set())
        nv = (result or {}).get("summary", {}).get("nearest_vasp")
        rows.append({
            "kind": "negative", "id": n["id"], "chain": n["chain"], "nearest": nv["entity_name"] if nv else None,
            "tier": nv["tier"] if nv else None, "confidence": nv["confidence"] if nv else None, "false_positive": nv is not None,
            "runtime": round(dt, 2), "error": err,
        })
        print(f"[neg {i}/{len(negatives)}] {n['id']}: {'FALSE POSITIVE ' + nv['entity_name'] if nv else 'no VASP >= 50'}", flush=True)

    pos = [r for r in rows if r["kind"] == "positive" and not r["error"]]
    neg = [r for r in rows if r["kind"] == "negative" and not r["error"]]
    high = [r for r in rows if r["tier"] == "High" and not r["error"]]
    high_ok = [r for r in high if r["kind"] == "positive" and r["nearest_correct"]]
    runtimes = [r["runtime"] for r in rows if not r["error"]]

    def rate(xs, pred):
        return round(sum(1 for x in xs if pred(x)) / len(xs), 4) if xs else None

    by_chain = {}
    for chain in sorted({r["chain"] for r in pos}):
        sub = [r for r in pos if r["chain"] == chain]
        by_chain[chain] = {"positives": len(sub), "top1": rate(sub, lambda r: r["top1"]), "coverage": rate(sub, lambda r: r["nearest"] is not None)}
    return {
        "metrics": {
            "positives": len(pos),
            "negatives": len(neg),
            "errors": sum(1 for r in rows if r["error"]),
            "top1_accuracy": rate(pos, lambda r: r["top1"]),
            "top3_accuracy": rate(pos, lambda r: r["top3"]),
            "precision_at_high": round(len(high_ok) / len(high), 4) if high else None,
            "high_count": len(high),
            "coverage": rate(pos, lambda r: r["nearest"] is not None),
            "false_positive_rate": rate(neg, lambda r: r["false_positive"]),
            "runtime_p50": percentile(runtimes, 0.5),
            "runtime_p95": percentile(runtimes, 0.95),
        },
        "by_chain": by_chain,
        "rows": rows,
    }


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def markdown(out: dict) -> str:
    m = out["metrics"]
    lines = [
        f"Run `{out['label']}` at {out['generated_at']} · data mode `{out['data_mode']}` · scoring.yaml SHA-256 `{out['scoring_config_sha256'][:16]}…`",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Positive cases (errors excluded) | {m['positives']} |",
        f"| Negative controls | {m['negatives']} |",
        f"| Runs that errored | {m['errors']} |",
        f"| Top-1 accuracy | {pct(m['top1_accuracy'])} |",
        f"| Top-3 accuracy | {pct(m['top3_accuracy'])} |",
        f"| Precision at High tier | {pct(m['precision_at_high'])} (n = {m['high_count']}) |",
        f"| Coverage (candidate ≥ 50) | {pct(m['coverage'])} |",
        f"| False-positive rate on negatives | {pct(m['false_positive_rate'])} |",
        f"| Runtime p50 / p95 | {m['runtime_p50']} s / {m['runtime_p95']} s |",
        "",
        "| Chain | Positives | Top-1 | Coverage |",
        "|---|---|---|---|",
    ]
    for c, v in out["by_chain"].items():
        lines.append(f"| {c} | {v['positives']} | {pct(v['top1'])} | {pct(v['coverage'])} |")
    lines += ["", "Numbers are reported exactly as measured by `eval/run_eval.py`."]
    return "\n".join(lines)
