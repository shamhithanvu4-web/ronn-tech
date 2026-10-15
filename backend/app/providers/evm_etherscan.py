"""EVM chains via Etherscan API V2 (one key, chainid parameter).

Ethereum (1), Polygon (137), Arbitrum (42161) and BNB Smart Chain (56).
Plan gating is detected from the API response for every chain, not only BSC.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.config import chain_config, get_settings, tokens_for
from app.errors import KeyMissing, PlanRequired, ProviderError
from app.providers.base import (
    ProviderStatus,
    RawTx,
    Transfer,
    TxIO,
    fetch_json,
    note_error,
    note_ok,
    runtime_status,
)
from app.providers.prices import native_usd_price, usd_for
from app.util import now_unix

PLAN_MARKERS = (
    "free api access is not supported",
    "not supported for this chain",
    "upgrade your api plan",
)
RATE_MARKERS = ("max rate limit reached", "rate limit")
MAX_RESULT_WINDOW = 10_000  # Etherscan: page * offset must stay <= 10,000


def _msg(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    return f"{data.get('message', '')} {data.get('result', '') if isinstance(data.get('result'), str) else ''}".lower()


def _is_rate_limited(data: Any) -> bool:
    return isinstance(data, dict) and data.get("status") == "0" and any(m in _msg(data) for m in RATE_MARKERS)


def _is_cacheable(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("status") == "1":
        return True
    return "no transactions found" in _msg(data) or data.get("result") == []


class EtherscanProvider:
    provider = "etherscan"

    def __init__(self, chain: str) -> None:
        cfg = chain_config(chain)
        if cfg.get("family") != "evm":
            raise ValueError(f"{chain} is not an EVM chain")
        self.chain = chain
        self.chainid = int(cfg["chainid"])
        self.base_url = cfg.get("base_url", "https://api.etherscan.io/v2/api")
        self.rate = float(cfg.get("rate_per_sec", 3))
        self.native = cfg.get("native", "ETH")
        self.decimals = int(cfg.get("decimals", 18))
        self.tokens = {t["contract"].lower(): t for t in tokens_for(chain)}
        self.free_tier = bool(cfg.get("free_tier", chain != "bsc"))
        self._block_memo: dict[tuple[int, str], int] = {}

    # ------------------------------------------------------------------ calls
    async def _call(self, params: dict[str, Any]) -> tuple[Any, str]:
        settings = get_settings()
        key = settings.ETHERSCAN_API_KEY
        if not key and settings.data_mode != "replay":
            raise KeyMissing(
                "ETHERSCAN_API_KEY is not set in backend/.env (sign up at https://etherscan.io/register).",
            )
        full = {"chainid": self.chainid, **params, "apikey": key or "REPLAY"}
        res = await fetch_json(
            chain=self.chain,
            provider=self.provider,
            url=self.base_url,
            params=full,
            rate_per_sec=self.rate,
            limiter_name="etherscan",
            retry_on=_is_rate_limited,
            cacheable=_is_cacheable,
        )
        data = res.data
        if not isinstance(data, dict):
            raise ProviderError(f"Unexpected Etherscan response for {self.chain}")
        status = str(data.get("status", ""))
        message = str(data.get("message", ""))
        result = data.get("result")
        if status == "1":
            note_ok(self.chain)
            return result, res.ref
        text = _msg(data)
        if "no transactions found" in text or (status == "0" and result == []):
            note_ok(self.chain)
            return [], res.ref
        if any(m in text for m in PLAN_MARKERS):
            note_error(self.chain, "requires paid plan", plan_required=True)
            raise PlanRequired(f"{self.chain} requires a paid Etherscan plan (free tier does not cover it).")
        if "invalid api key" in text or "missing/invalid api key" in text:
            note_error(self.chain, "invalid API key")
            raise ProviderError("Etherscan rejected the API key (check ETHERSCAN_API_KEY).", code="invalid_api_key")
        note_error(self.chain, f"{message}: {result}")
        raise ProviderError(f"Etherscan error on {self.chain}: {message} {result if isinstance(result, str) else ''}".strip())

    async def block_at(self, timestamp: int, closest: str) -> int | None:
        memo_key = (int(timestamp), closest)
        if memo_key in self._block_memo:
            return self._block_memo[memo_key]
        result, _ = await self._call(
            {"module": "block", "action": "getblocknobytime", "timestamp": int(timestamp), "closest": closest}
        )
        try:
            block = int(result)
        except (TypeError, ValueError):
            return None
        self._block_memo[memo_key] = block
        return block

    async def _safe_block(self, timestamp: int, closest: str) -> int | None:
        try:
            return await self.block_at(timestamp, closest)
        except (PlanRequired, KeyMissing):
            raise
        except ProviderError:
            return None

    async def _paged(self, action: str, address: str, start: int, end: int, limit: int) -> list[tuple[dict, str]]:
        rows: list[tuple[dict, str]] = []
        offset = min(1000, max(1, limit))
        page = 1
        while len(rows) < limit and page * offset <= MAX_RESULT_WINDOW:
            result, ref = await self._call(
                {
                    "module": "account",
                    "action": action,
                    "address": address,
                    "startblock": start,
                    "endblock": end,
                    "page": page,
                    "offset": offset,
                    "sort": "desc",
                }
            )
            batch = result or []
            rows.extend((r, ref) for r in batch)
            if len(batch) < offset:
                break
            page += 1
        return rows[:limit]

    # ------------------------------------------------------------------ public
    async def get_transfers(
        self,
        address: str,
        since: int | None,
        until: int | None,
        limit: int,
        start_block: int | None = None,
    ) -> list[Transfer]:
        address = address.lower()
        start, end = 0, 99_999_999
        # Timestamps are rounded to the hour so block lookups are shared (and cached)
        # across nearby queries. A known start block (e.g. the block of the transfer
        # that brought funds to this address) avoids a lookup entirely.
        if start_block is not None:
            start = int(start_block)
        elif since is not None:
            b = await self._safe_block(int(since) // 3600 * 3600, "before")
            start = b if b is not None else 0
        if until is not None and int(until) < now_unix() - 3600:
            b = await self._safe_block(-(-int(until) // 3600) * 3600, "after")
            end = b if b is not None else end
        normal, tokens, internal = await asyncio.gather(
            self._paged("txlist", address, start, end, limit),
            self._paged("tokentx", address, start, end, limit),
            self._paged("txlistinternal", address, start, end, limit),
        )
        price = await native_usd_price(self.chain)
        out: list[Transfer] = []
        scale = Decimal(10) ** self.decimals
        for row, ref in normal:
            if str(row.get("isError", "0")) == "1" or str(row.get("txreceipt_status", "1")) == "0":
                continue
            to_addr = (row.get("to") or row.get("contractAddress") or "").lower()
            amount = Decimal(int(row.get("value") or 0)) / scale
            out.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=row["hash"],
                    block_time=int(row["timeStamp"]),
                    from_addr=(row.get("from") or "").lower(),
                    to_addr=to_addr,
                    asset=self.native,
                    token_contract=None,
                    amount=format(amount.normalize() if amount else Decimal(0), "f"),
                    usd_estimate=usd_for(amount, price),
                    is_internal=False,
                    raw_ref=ref,
                    block_number=int(row.get("blockNumber") or 0) or None,
                )
            )
        for row, ref in internal:
            if str(row.get("isError", "0")) == "1":
                continue
            amount = Decimal(int(row.get("value") or 0)) / scale
            out.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=row["hash"],
                    block_time=int(row["timeStamp"]),
                    from_addr=(row.get("from") or "").lower(),
                    to_addr=(row.get("to") or row.get("contractAddress") or "").lower(),
                    asset=self.native,
                    token_contract=None,
                    amount=format(amount.normalize() if amount else Decimal(0), "f"),
                    usd_estimate=usd_for(amount, price),
                    is_internal=True,
                    raw_ref=ref,
                    block_number=int(row.get("blockNumber") or 0) or None,
                )
            )
        for row, ref in tokens:
            contract = (row.get("contractAddress") or "").lower()
            tok = self.tokens.get(contract)
            decimals = int(row.get("tokenDecimal") or (tok or {}).get("decimals") or 0)
            amount = Decimal(int(row.get("value") or 0)) / (Decimal(10) ** decimals)
            symbol = tok["symbol"] if tok else str(row.get("tokenSymbol") or "TOKEN")[:16]
            out.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=row["hash"],
                    block_time=int(row["timeStamp"]),
                    from_addr=(row.get("from") or "").lower(),
                    to_addr=(row.get("to") or "").lower(),
                    asset=symbol,
                    token_contract=contract,
                    amount=format(amount.normalize() if amount else Decimal(0), "f"),
                    usd_estimate=usd_for(amount, None, stablecoin=bool(tok and tok.get("stablecoin"))) if tok else None,
                    is_internal=False,
                    raw_ref=ref,
                    block_number=int(row.get("blockNumber") or 0) or None,
                    in_scope=tok is not None,
                )
            )
        if since is not None:
            out = [t for t in out if t.block_time >= since]
        if until is not None:
            out = [t for t in out if t.block_time <= until]
        out.sort(key=lambda t: (t.block_time, t.tx_hash), reverse=True)
        return out

    async def get_tx(self, tx_id: str) -> RawTx:
        result, ref = await self._call({"module": "proxy", "action": "eth_getTransactionByHash", "txhash": tx_id})
        tx = result or {}
        value = Decimal(int(tx.get("value", "0x0"), 16)) / (Decimal(10) ** self.decimals) if tx else Decimal(0)
        return RawTx(
            chain=self.chain,
            tx_id=tx_id,
            block_time=None,
            block_height=int(tx["blockNumber"], 16) if tx.get("blockNumber") else None,
            inputs=[TxIO(address=(tx.get("from") or "").lower(), value=format(value, "f"))],
            outputs=[TxIO(address=(tx.get("to") or "").lower(), value=format(value, "f"))],
            raw_ref=ref,
        )

    async def status(self) -> ProviderStatus:
        settings = get_settings()
        rt = runtime_status(self.chain)
        key_present = bool(settings.ETHERSCAN_API_KEY)
        if settings.data_mode == "replay":
            st = "replay"
        elif not key_present:
            st = "key_missing"
        elif rt.get("plan_required") or (not self.free_tier and not rt.get("last_ok_at")):
            st = "requires_paid_plan"
        elif rt.get("last_error") and not rt.get("last_ok_at"):
            st = "error"
        else:
            st = "ok"
        return ProviderStatus(
            chain=self.chain,
            provider=f"Etherscan API V2 (chainid {self.chainid})",
            available=st in ("ok", "replay"),
            status=st,
            plan="free tier" if self.free_tier else "requires paid plan",
            limits="free tier: 3 req/s, 100,000 calls/day (shared across chains)",
            key_env="ETHERSCAN_API_KEY",
            key_present=key_present,
            last_error=rt.get("last_error"),
            last_ok_at=rt.get("last_ok_at"),
            note=None if self.free_tier else "Not on Etherscan's free tier; shown as requires paid plan. BSC data is never faked.",
        )
