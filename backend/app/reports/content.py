"""Report content: one canonical dict rendered both as PDF and JSON (SPEC §10).

The JSON export is {"report": <content>, "integrity": {...}}. content_sha256 is the
SHA-256 of canonical JSON (sorted keys, compact separators, UTF-8) of "report".
"""
from __future__ import annotations

from typing import Any

from app.config import METHODOLOGY_VERSION, PRODUCT_NAME, load_yaml
from app.db import iso, utcnow
from app.util import canonical_json, dt_to_ist, sha256_hex, unix_to_ist, unix_to_utc

DISCLAIMER = (
    "Attribution is probabilistic and is an investigative lead, not proof of ownership. "
    "It identifies a likely deposit destination with an attribution confidence derived from on-chain evidence "
    "and third-party labels; it does not establish who controls any wallet. Confirm with the VASP's records "
    "(KYC, account history) obtained through lawful process before taking action against any person."
)

METHODOLOGY = [
    "Validation: Bitcoin base58check and bech32/bech32m (BIP-173/BIP-350); EVM 0x + 40 hex; TRON base58check (version 0x41); Solana base58 (32 bytes).",
    "Traversal: breadth-first from the suspect to the configured hop limit within a 90-day window from its first in-scope transfer; per address the top 5 counterparties by value plus any receiving >= 10 % of its outflow; dust (< USD 10) and zero-value token transfers are not followed.",
    "Labels: OFAC SDN addresses and exchange proof-of-reserve wallets (tier A, 0.95); Etherscan-derived labels, Tronscan tags and Ransomwhere (tier B, 0.80); runtime inferences (tier C, 0.60-0.85); WalletExplorer and analyst labels (tier D, 0.50).",
    "Deposit-address inference (EVM/TRON): an address that forwards at least 99 % of a received amount to an exchange-labelled address within 3,200 blocks (Ethereum) or 11 hours (other chains) is treated as that exchange's deposit address (Victor 2020, relative tolerance); exchange gas top-ups and exclusive forwarding corroborate.",
    "Bitcoin: common-input-ownership clustering with exchange-labelled inputs (0.85); CoinJoin-like transactions (>= 5 equal outputs) are excluded and flagged.",
    "Scoring: path score = R x H(hops) x sqrt(V) x T; candidate confidence = 1 - product(1 - path score); tiers High >= 75, Medium 50-74, Low 25-49, Weak < 25. The nearest VASP is the VASP with the fewest hops among candidates with confidence >= 50.",
]
LIMITATIONS = [
    "Labels come from public datasets and may be incomplete, stale or wrong; unverified entities are marked as such.",
    "Inferred deposit addresses and clusters are heuristics; exchanges can share or rotate addresses.",
    "USD values are approximate and use the current price, not the price at the time of transfer.",
    "Traversal is bounded by hop, node and API-call budgets; results marked partial may miss paths.",
    "Decentralised bridges and swaps break the trail on the source chain; follow-up on the destination chain is required.",
    "Provider data (explorer APIs) is trusted as returned; raw responses and their SHA-256 hashes are preserved for verification.",
]


def certificate_template() -> dict[str, Any]:
    legal = load_yaml("legal.yaml").get("electronic_record_certificate", {})
    return {
        "title": legal.get("label", "Electronic-record certificate (template)"),
        "status": "Template for the investigating officer to complete and verify against the statutory form.",
        "fields": [
            "Name and designation of the certifying officer: ______________________",
            "Description of the electronic record (this report, its JSON export and the raw provider responses listed in the Integrity section): ______________________",
            "Computer system / device used to produce the record and its lawful control: ______________________",
            "Statement that the system was operating properly during the relevant period, or details of any malfunction: ______________________",
            "Hash values of the record (SHA-256, as listed in this report): ______________________",
            "Place: ____________   Date: ____________   Signature: ____________",
        ],
        "note": "Legal provisions to be confirmed by the investigating officer.",
    }


def _hop_rows(path: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for hop in path["hops_detail"]:
        for t in hop["transfers"]:
            rows.append(
                {
                    "from": hop["from"],
                    "to": hop["to"],
                    "chain": hop["chain"],
                    "asset": t["asset"],
                    "amount": t["amount"],
                    "usd": t["usd"],
                    "time_utc": t["time_utc"],
                    "time_ist": t["time_ist"],
                    "tx_hash": t["tx_hash"],
                    "explorer_url": t["explorer_url"],
                }
            )
    return rows


def analysis_report(analysis: Any, case: Any | None, generated_by: str) -> dict[str, Any]:
    r = analysis.result or {}
    s = r.get("summary", {})
    candidates = []
    for c in (r.get("candidates") or [])[:3]:
        candidates.append(
            {
                "entity_name": c["entity_name"],
                "entity_type": c["entity_type"],
                "is_vasp": c["is_vasp"],
                "attribution_confidence": c["confidence"],
                "tier": c["tier"],
                "min_hops": c["min_hops"],
                "path_count": c["path_count"],
                "routable": c["routable"],
                "factors": c["best_factors"],
                "via": c["via"],
                "label_sources": c["label_sources"],
                "what_would_raise_this": c["raise_note"],
                "evidence_paths": [
                    {
                        "score": p["score"],
                        "hops": p["hops"],
                        "factors": p["factors"],
                        "via": p["via"],
                        "label": p["label"],
                        "nodes": p["nodes"],
                        "transfers": _hop_rows(p),
                    }
                    for p in c["paths"][:5]
                ],
            }
        )
    now = utcnow()
    params = r.get("params", {})
    return {
        "report_type": "analysis",
        "product": PRODUCT_NAME,
        "methodology_version": METHODOLOGY_VERSION,
        "cover": {
            "case_no": case.case_no if case else None,
            "case_title": case.title if case else None,
            "complaint_ref": case.complaint_ref if case else None,
            "fir_no": case.fir_no if case else None,
            "agency": case.agency if case else None,
            "analysis_id": analysis.id,
            "generated_at_utc": iso(now),
            "generated_at_ist": dt_to_ist(now),
            "analyst": generated_by,
            "data_mode": r.get("data_mode"),
        },
        "subject": {
            "address": r.get("suspect", {}).get("address", analysis.address),
            "family": r.get("suspect", {}).get("family"),
            "chains_checked": r.get("suspect", {}).get("chains_checked", []),
            "window_start_utc": unix_to_utc(params.get("window_start")),
            "window_end_utc": unix_to_utc(params.get("window_end")),
            "window_start_ist": unix_to_ist(params.get("window_start")),
            "window_end_ist": unix_to_ist(params.get("window_end")),
            "max_hops": params.get("max_hops"),
        },
        "summary": {
            "likely_deposit_destination": s.get("nearest_vasp"),
            "risk_level": s.get("risk_level"),
            "recommended_action": s.get("recommended_action"),
            "partial": s.get("partial"),
            "partial_reasons": s.get("partial_reasons", []),
        },
        "candidates": candidates,
        "funding_sources": [
            {"entity_name": c["entity_name"], "attribution_confidence": c["confidence"], "tier": c["tier"], "min_hops": c["min_hops"]}
            for c in (r.get("funding_sources") or [])[:5]
        ],
        "deposit_inferences": r.get("deposit_inferences", []),
        "cluster_inferences": r.get("cluster_inferences", []),
        "linked_wallets": r.get("linked_wallets", []),
        "possible_unlabelled_services": r.get("possible_unlabelled_services", []),
        "cross_chain": r.get("cross_chain", {}),
        "typologies": [
            {"code": t["code"], "title": t["title"], "description": t["description"], "evidence_txs": t["evidence_txs"]}
            for t in r.get("typologies", [])
        ],
        "risk": r.get("risk", {}),
        "methodology": METHODOLOGY,
        "limitations": LIMITATIONS,
        "data_sources": {
            "labels": r.get("evidence", {}).get("label_sources", []),
            "providers": sorted({(e["provider"], e["chain"]) for e in r.get("evidence", {}).get("raw_responses", [])}),
            "usd_note": "USD values are approximate, current price.",
        },
        "certificate": certificate_template(),
        "disclaimer": DISCLAIMER,
    }


def wrap_with_integrity(content: dict[str, Any], analysis_sha256: str | None, raw_responses: list[dict[str, Any]]) -> dict[str, Any]:
    digest = sha256_hex(canonical_json(content))
    return {
        "report": content,
        "integrity": {
            "algorithm": "SHA-256 over canonical JSON of 'report' (sorted keys, separators ',' ':', UTF-8)",
            "content_sha256": digest,
            "analysis_sha256": analysis_sha256,
            "raw_responses": [
                {"url": e["url"], "sha256": e["content_sha256"], "retrieved_at": e["retrieved_at"], "origin": e.get("origin")}
                for e in raw_responses
            ],
        },
    }


def content_hash(content: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(content))
