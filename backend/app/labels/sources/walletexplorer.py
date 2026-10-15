"""Live, cached WalletExplorer address lookup (tier D: historic Bitcoin clusters).

GET https://www.walletexplorer.com/api/1/address-lookup?address=<a>&caller=<id>
WalletExplorer clusters are historic and partly unmaintained, hence reliability 0.50.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import PRODUCT_NAME
from app.errors import AppError
from app.providers.base import fetch_json

URL = "https://www.walletexplorer.com/api/1/address-lookup"
UNNAMED = re.compile(r"^\[?[0-9a-f]{8,}\]?$", re.I)


async def lookup(address: str) -> dict[str, Any] | None:
    try:
        res = await fetch_json(
            chain="bitcoin",
            provider="walletexplorer",
            url=URL,
            params={"address": address, "caller": PRODUCT_NAME.lower().replace(" ", "-")},
            rate_per_sec=1,
            limiter_name="walletexplorer",
        )
    except AppError:
        return None
    data = res.data or {}
    if not data.get("found"):
        return None
    label = str(data.get("label") or "").strip()
    if not label or UNNAMED.match(label):
        return None
    name = re.sub(r"\.(com|io|net|org)(-old\d*|-output|-cold)?$", "", label, flags=re.I)
    return {
        "entity_name": name,
        "entity_type": None,
        "role": "UNKNOWN",
        "reliability": 0.50,
        "tier": "D",
        "source": "walletexplorer",
        "source_url": f"https://www.walletexplorer.com/address/{address}",
        "evidence": {"wallet_label": label, "wallet_id": data.get("wallet_id"), "raw_ref": res.ref, "note": "historic cluster"},
    }
