"""OFAC SDN digital-currency addresses (tier A, SANCTIONED only).

Source: JSON lists on the "lists" branch of
https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses (all assets),
generated nightly from the US Treasury SDN list.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.db import iso, utcnow
from app.engine.validate import validate_address
from app.errors import InvalidAddress
from app.labels.sources.common import content_hash, git_head, http_get_json, http_get_text
from app.labels.store import IngestResult, LabelRow

REPO = "0xB10C/ofac-sanctioned-digital-currency-addresses"
BRANCH = "lists"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{{name}}"
API_LIST = f"https://api.github.com/repos/{REPO}/contents?ref={BRANCH}"
ENTITY = "OFAC SDN-listed address"

# Asset code -> chain. Stablecoin lists (USDT/USDC) are mapped by address format.
ASSET_CHAIN = {
    "XBT": "bitcoin",
    "ETH": "ethereum",
    "ARB": "arbitrum",
    "BSC": "bsc",
    "TRX": "tron",
    "SOL": "solana",
    "ETC": "ethereum-classic",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "BSV": "bitcoin-sv",
    "BTG": "bitcoin-gold",
    "DASH": "dash",
    "XMR": "monero",
    "ZEC": "zcash",
    "XRP": "ripple",
    "XVG": "verge",
}
VALIDATED_CHAINS = {"bitcoin", "ethereum", "arbitrum", "bsc", "tron", "solana"}


def _chain_for(asset: str, address: str) -> str:
    if asset in ASSET_CHAIN:
        return ASSET_CHAIN[asset]
    if address.lower().startswith("0x"):
        return "ethereum"
    if address.startswith("T") and len(address) == 34:
        return "tron"
    try:
        if validate_address(address).family == "bitcoin":
            return "bitcoin"  # Omni-layer stablecoin addresses
    except InvalidAddress:
        pass
    return asset.lower()


def _rows_for(asset: str, addresses: list[str], source_url: str, retrieved: str) -> tuple[list[LabelRow], int]:
    rows: list[LabelRow] = []
    rejected = 0
    for addr in addresses:
        addr = str(addr).strip()
        if not addr:
            continue
        chain = _chain_for(asset, addr)
        evidence = {"asset_list": asset, "list": f"sanctioned_addresses_{asset}.json"}
        if chain in VALIDATED_CHAINS:
            try:
                info = validate_address(addr, "evm" if addr.lower().startswith("0x") else None)
                addr = info.address
            except InvalidAddress:
                rejected += 1
                continue
            family_chain = {"bitcoin": "bitcoin", "tron": "tron", "solana": "solana"}.get(info.family)
            if info.family == "evm" and chain not in ("ethereum", "arbitrum", "bsc"):
                family_chain = "ethereum"
            if family_chain and family_chain != chain:
                # The upstream list file and the address format disagree; label the
                # chain the address actually belongs to and keep a note.
                evidence["list_format_mismatch"] = f"listed under {asset}, address format is {info.family}"
                chain = family_chain
        rows.append(
            LabelRow(
                chain=chain,
                address=addr,
                entity_name=ENTITY,
                entity_type="SANCTIONED",
                is_vasp=False,
                role="UNKNOWN",
                source="ofac",
                source_url=source_url,
                reliability=0.95,
                tier="A",
                retrieved_at=retrieved,
                evidence=evidence,
            )
        )
    return rows, rejected


def fetch(from_dir: Path | None = None, file: Path | None = None) -> IngestResult:
    retrieved = iso(utcnow()) or ""
    rows: list[LabelRow] = []
    rejected = 0
    hashes: list[str] = []
    if from_dir is not None:
        version = git_head(from_dir)
        files = sorted(from_dir.glob("sanctioned_addresses_*.json"))
        if not files:
            raise RuntimeError(f"No sanctioned_addresses_*.json files in {from_dir}")
        for path in files:
            asset = path.stem.replace("sanctioned_addresses_", "")
            text = path.read_text(encoding="utf-8")
            hashes.append(text)
            url = f"https://github.com/{REPO}/blob/{version or BRANCH}/{path.name}"
            r, rej = _rows_for(asset, json.loads(text), url, retrieved)
            rows.extend(r)
            rejected += rej
    else:
        version = BRANCH
        try:
            names = [i["name"] for i in http_get_json(API_LIST) if i["name"].endswith(".json")]
        except Exception:  # noqa: BLE001 - fall back to known asset lists
            names = [f"sanctioned_addresses_{a}.json" for a in list(ASSET_CHAIN) + ["USDT", "USDC"]]
        for name in sorted(names):
            if not name.startswith("sanctioned_addresses_"):
                continue
            asset = name[len("sanctioned_addresses_") : -len(".json")]
            try:
                text = http_get_text(RAW.format(name=name))
            except RuntimeError:
                continue
            hashes.append(text)
            r, rej = _rows_for(asset, json.loads(text), RAW.format(name=name), retrieved)
            rows.extend(r)
            rejected += rej
    return IngestResult(
        source="ofac",
        rows=rows,
        source_url=f"https://github.com/{REPO}/tree/{BRANCH}",
        version=version,
        content_sha256=content_hash(*hashes),
        notes={"rejected_invalid": rejected, "upstream": "US Treasury OFAC SDN list via 0xB10C/ofac-sanctioned-digital-currency-addresses"},
    )
