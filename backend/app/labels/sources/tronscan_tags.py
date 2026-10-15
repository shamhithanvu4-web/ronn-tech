"""Live, cached Tronscan account-tag lookup (tier B).

GET https://apilist.tronscanapi.com/api/accountv2?address=<a> (header TRON-PRO-API-KEY).
The public/address tag is matched to a known entity by name; unmatched tags become
UNKNOWN_SERVICE labels. Red tags (scam reports) become SCAM labels.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import get_settings
from app.errors import AppError
from app.providers.base import fetch_json

URL = "https://apilist.tronscanapi.com/api/accountv2"


def clean_tag(tag: str) -> str:
    base = re.split(r"[-:]", tag.strip(), maxsplit=1)[0]
    base = re.sub(r"(\s+(hot|cold)(\s*wallet)?)?(\s*\d+)?$", "", base, flags=re.I).strip()
    return base or tag.strip()


async def lookup(address: str) -> dict[str, Any] | None:
    key = get_settings().TRONSCAN_API_KEY
    headers = {"TRON-PRO-API-KEY": key} if key else {}
    try:
        res = await fetch_json(
            chain="tron",
            provider="tronscan",
            url=URL,
            params={"address": address},
            headers=headers,
            rate_per_sec=3,
            limiter_name="tronscan",
        )
    except AppError:
        return None
    data = res.data or {}
    red = str(data.get("redTag") or "").strip()
    tag = str(data.get("publicTag") or data.get("addressTag") or "").strip()
    if red:
        return {
            "entity_name": f"Tronscan report: {red}",
            "entity_type": "SCAM",
            "role": "UNKNOWN",
            "reliability": 0.80,
            "tier": "B",
            "source": "tronscan_tags",
            "source_url": f"https://tronscan.org/#/address/{address}",
            "evidence": {"redTag": red, "raw_ref": res.ref},
        }
    if not tag:
        return None
    role = "HOT" if re.search(r"hot", tag, re.I) else ("COLD" if re.search(r"cold", tag, re.I) else "UNKNOWN")
    return {
        "entity_name": clean_tag(tag),
        "entity_type": None,  # resolved against known entities by name
        "role": role,
        "reliability": 0.80,
        "tier": "B",
        "source": "tronscan_tags",
        "source_url": f"https://tronscan.org/#/address/{address}",
        "evidence": {"tag": tag, "raw_ref": res.ref},
    }
