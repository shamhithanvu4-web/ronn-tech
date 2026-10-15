"""Etherscan-derived labels from github.com/dawsbot/eth-labels (tier B).

Only exchange and service categories are kept, plus scam/phishing/exploit and
sanctions categories, which the risk model (SPEC §6.8) needs.

Exchange identification is data-driven (no hand-typed lists):
  1. rows whose Etherscan label is "exchange" are CEX;
  2. a label slug is treated as an exchange slug when at least 60 % of its
     addresses also carry the "exchange" label somewhere in the dataset;
  3. name tags of the form "<Name> Dep: 0x..." are deposit addresses of <Name>
     when <Name> is an exchange identified by rules 1-2.
The entity name comes from the Etherscan name tag (text before ":" with
trailing numbers and wallet words removed).
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from pathlib import Path

from app.db import iso, utcnow
from app.labels.sources.common import content_hash, git_head, http_get_text
from app.labels.store import IngestResult, LabelRow, name_key

REPO = "dawsbot/eth-labels"
BRANCH = "v1"
CSV_PATH = "data/csv/accounts.csv"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{CSV_PATH}"

CHAIN_IDS = {
    "1": "ethereum",
    "56": "bsc",
    "137": "polygon",
    "42161": "arbitrum",
    "10": "optimism",
    "8453": "base",
    "43114": "avalanche",
    "480": "worldchain",
    "100": "gnosis",
    "42220": "celo",
}

CATEGORY_TYPES = {
    "exchange": "CEX",
    "otc": "OTC_P2P",
    "payments": "PAYMENT_PROCESSOR",
    "fiat-gateway": "PAYMENT_PROCESSOR",
    "mixer": "MIXER",
    "tornado-cash": "MIXER",
    "ethereum-mixer": "MIXER",
    "bridge": "BRIDGE",
    "dex": "DEX",
    "gambling": "GAMBLING",
    "phish-hack": "SCAM",
    "heist": "SCAM",
    "scam": "SCAM",
    "exploit": "SCAM",
    "ofac-sanctioned": "SANCTIONED",
    "ofac-sanctions-lists": "SANCTIONED",
}
# Generic slugs that describe a wallet kind rather than an organisation.
GENERIC_SLUGS = {
    "exchange", "cold-wallet", "hot-wallet", "derivatives", "wallet-app", "old-contract", "token-contract",
    "proposer-fee-recipient", "genesis-address", "blocked", "avs-operator", "contract-deployer", "charity",
    "nonprofit", "fiat-gateway", "payments", "otc", "dex", "bridge", "mixer", "defi", "deprecated",
}
EXCHANGE_SHARE = 0.6
EXCHANGE_MIN_ADDRESSES = 2

ROLE_PATTERNS = [
    (re.compile(r"deposit funder|funder", re.I), "OPERATOR"),
    (re.compile(r"\bdep:|\bdeposit\b", re.I), "DEPOSIT"),
    (re.compile(r"hot\s*wallet|\bhot\b", re.I), "HOT"),
    (re.compile(r"cold\s*wallet|\bcold\b", re.I), "COLD"),
    (re.compile(r"withdraw", re.I), "WITHDRAWAL"),
    (re.compile(r"router|contract|token|proxy|pool|vault|bridge|swap|settler|exchange v", re.I), "CONTRACT"),
    (re.compile(r"commerce|payment|merchant", re.I), "PAYMENT"),
]
DEP_TAG = re.compile(r"^(?P<name>.+?)\s+Dep:\s*0x[0-9a-fA-F]+", re.I)
TRAILING = re.compile(
    r"(\s+((hot|cold)\s*wallet|multi-?sig|proxy|contract|token|fees?|deposit\s+funder|funder))?(\s+\d+)?\s*$",
    re.I,
)
GENERIC_NAMES = {"exchange", "null", "none", ""}


def entity_name_from_tag(tag: str, slug: str) -> str:
    tag = (tag or "").strip()
    m = DEP_TAG.match(tag)
    if m:
        return m.group("name").strip()
    base = tag.split(":", 1)[0].strip()
    prev = None
    while prev != base:
        prev = base
        base = TRAILING.sub("", base).strip()
    if base.lower() in GENERIC_NAMES:
        if slug and slug not in GENERIC_SLUGS and slug not in CATEGORY_TYPES:
            return slug.replace("-", " ").title()
        return "Unnamed exchange (Etherscan 'exchange' label)" if slug == "exchange" else (slug.replace("-", " ").title() or "Unnamed")
    return base


def role_from_tag(tag: str) -> str:
    for pat, role in ROLE_PATTERNS:
        if pat.search(tag or ""):
            return role
    return "UNKNOWN"


def _rows(text: str, source_url: str, retrieved: str) -> tuple[list[LabelRow], dict]:
    records = list(csv.DictReader(io.StringIO(text)))
    labels_by_addr: dict[str, set[str]] = defaultdict(set)
    addrs_by_slug: dict[str, set[str]] = defaultdict(set)
    for r in records:
        a = (r.get("address") or "").lower()
        labels_by_addr[a].add(r.get("label") or "")
        addrs_by_slug[r.get("label") or ""].add(a)

    exchange_slugs: set[str] = set()
    for slug, addrs in addrs_by_slug.items():
        if slug in GENERIC_SLUGS or slug in CATEGORY_TYPES:
            continue
        co = sum(1 for a in addrs if "exchange" in labels_by_addr[a])
        if co >= EXCHANGE_MIN_ADDRESSES and co / len(addrs) >= EXCHANGE_SHARE:
            exchange_slugs.add(slug)

    exchange_names: set[str] = set()
    for r in records:
        if r.get("label") == "exchange" or r.get("label") in exchange_slugs:
            exchange_names.add(name_key(entity_name_from_tag(r.get("nameTag") or "", r.get("label") or "")))

    rows: list[LabelRow] = []
    stats: dict[str, int] = defaultdict(int)
    for r in records:
        chain = CHAIN_IDS.get(str(r.get("chainId")))
        if chain is None:
            stats["skipped_chain"] += 1
            continue
        addr = (r.get("address") or "").lower()
        if not re.fullmatch(r"0x[0-9a-f]{40}", addr):
            stats["skipped_invalid"] += 1
            continue
        slug = r.get("label") or ""
        tag = r.get("nameTag") or ""
        etype = None
        if slug in CATEGORY_TYPES:
            etype = CATEGORY_TYPES[slug]
        elif slug in exchange_slugs:
            etype = "CEX"
        elif slug.endswith("-exploit"):
            etype = "SCAM"
        dep = DEP_TAG.match(tag)
        if dep and name_key(dep.group("name")) in exchange_names:
            etype = "CEX"
        if etype is None:
            stats["skipped_category"] += 1
            continue
        name = entity_name_from_tag(tag, slug)
        if etype == "SANCTIONED":
            name = "OFAC sanctions (Etherscan label)"
        role = "DEPOSIT" if dep else role_from_tag(tag)
        if etype in ("MIXER", "BRIDGE", "DEX") and role == "UNKNOWN":
            role = "CONTRACT"
        rows.append(
            LabelRow(
                chain=chain,
                address=addr,
                entity_name=name,
                entity_type=etype,
                is_vasp=etype in ("CEX", "OTC_P2P", "PAYMENT_PROCESSOR"),
                role=role,
                source="eth_labels",
                source_url=source_url,
                reliability=0.80,
                tier="B",
                retrieved_at=retrieved,
                evidence={"etherscan_label": slug, "name_tag": tag, "chain_id": r.get("chainId")},
            )
        )
        stats[f"kept_{etype}"] += 1
    stats["exchange_slugs"] = len(exchange_slugs)
    return rows, dict(stats)


def fetch(from_dir: Path | None = None, file: Path | None = None) -> IngestResult:
    retrieved = iso(utcnow()) or ""
    if file is not None:
        text = file.read_text(encoding="utf-8")
        version = None
        source_url = RAW_URL
    elif from_dir is not None:
        text = (from_dir / CSV_PATH).read_text(encoding="utf-8")
        version = git_head(from_dir)
        source_url = f"https://github.com/{REPO}/blob/{version or BRANCH}/{CSV_PATH}"
    else:
        text = http_get_text(RAW_URL, timeout=120)
        version = BRANCH
        source_url = RAW_URL
    rows, stats = _rows(text, source_url, retrieved)
    return IngestResult(
        source="eth_labels",
        rows=rows,
        source_url=source_url,
        version=version,
        content_sha256=content_hash(text),
        notes=stats,
    )
