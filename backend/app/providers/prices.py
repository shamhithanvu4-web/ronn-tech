"""USD estimates (SPEC §4): stablecoins = 1.0; native coins use CoinGecko's
simple-price endpoint (current price). Shown as "approximate, current price".
"""
from __future__ import annotations

import threading
import time
from decimal import Decimal

from app.config import chain_config, load_yaml
from app.errors import AppError
from app.providers.base import fetch_json
from app.providers.context import current

USD_NOTE = "approximate, current price"
_memo: dict[str, tuple[float, Decimal | None]] = {}
_lock = threading.Lock()
_TTL = 600.0


async def native_usd_price(chain: str) -> Decimal | None:
    try:
        coin = chain_config(chain).get("coingecko_id")
    except KeyError:
        return None
    if not coin:
        return None
    # Memoised per analysis only, so every analysis records the price response it used
    # as evidence (the SQLite cache still avoids repeated network calls).
    ctx = current()
    memo = ctx.memo if ctx is not None else _memo
    with _lock:
        hit = memo.get(f"price:{coin}")
        if hit and time.monotonic() - hit[0] < _TTL:
            return hit[1]
    cfg = load_yaml("chains.yaml").get("prices", {})
    base = cfg.get("base_url", "https://api.coingecko.com/api/v3")
    price: Decimal | None = None
    try:
        res = await fetch_json(
            chain="coingecko",
            provider="coingecko",
            url=f"{base}/simple/price",
            params={"ids": coin, "vs_currencies": "usd"},
            rate_per_sec=float(cfg.get("rate_per_sec", 0.5)),
        )
        value = (res.data or {}).get(coin, {}).get("usd")
        price = Decimal(str(value)) if value is not None else None
    except AppError:
        price = None
    if ctx is not None:
        with _lock:
            memo[f"price:{coin}"] = (time.monotonic(), price)
    return price


def clear_memo() -> None:
    with _lock:
        _memo.clear()


def usd_for(amount: Decimal, price: Decimal | None, stablecoin: bool = False) -> str | None:
    if stablecoin:
        return format(amount.quantize(Decimal("0.01")), "f")
    if price is None:
        return None
    return format((amount * price).quantize(Decimal("0.01")), "f")
