"""Live Chainalysis free sanctions screening API (only when CHAINALYSIS_API_KEY is set).

GET https://public.chainalysis.com/api/v1/address/<a> with header X-API-Key.
A non-empty "identifications" list means the address is on a sanctions list.
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.errors import AppError
from app.providers.base import fetch_json

URL = "https://public.chainalysis.com/api/v1/address/{address}"


def enabled() -> bool:
    return bool(get_settings().CHAINALYSIS_API_KEY)


async def lookup(chain: str, address: str) -> dict[str, Any] | None:
    key = get_settings().CHAINALYSIS_API_KEY
    if not key and get_settings().data_mode != "replay":
        return None
    try:
        res = await fetch_json(
            chain=chain,
            provider="chainalysis",
            url=URL.format(address=address),
            headers={"X-API-Key": key} if key else {},
            rate_per_sec=2,
            limiter_name="chainalysis",
        )
    except AppError:
        return None
    idents = (res.data or {}).get("identifications") or []
    if not idents:
        return None
    first = idents[0]
    return {
        "entity_name": "Sanctioned address (Chainalysis screening)",
        "entity_type": "SANCTIONED",
        "role": "UNKNOWN",
        "reliability": 0.95,
        "tier": "A",
        "source": "chainalysis_sanctions",
        "source_url": first.get("url") or "https://public.chainalysis.com",
        "evidence": {"identifications": idents, "raw_ref": res.ref},
    }
