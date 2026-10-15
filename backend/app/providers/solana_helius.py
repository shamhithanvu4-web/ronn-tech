"""Solana via Helius enhanced transactions (parsed transfers).

Enabled only when HELIUS_API_KEY is set (or in replay mode with fixtures).
GET {base}/v0/addresses/{a}/transactions?api-key=...&limit=100&before-signature=...
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.config import chain_config, get_settings, tokens_for
from app.errors import KeyMissing, ProviderError
from app.providers.base import (
    ProviderStatus,
    RawTx,
    Transfer,
    fetch_json,
    note_ok,
    runtime_status,
)
from app.providers.prices import native_usd_price, usd_for

LAMPORTS = Decimal(1_000_000_000)
PAGE_LIMIT = 100


class SolanaHeliusProvider:
    chain = "solana"
    provider = "helius"

    def __init__(self) -> None:
        cfg = chain_config(self.chain)
        self.base_url = cfg.get("base_url", "https://api-mainnet.helius-rpc.com")
        self.rate = float(cfg.get("rate_per_sec", 5))
        self.tokens = {t["contract"]: t for t in tokens_for(self.chain)}

    @staticmethod
    def enabled() -> bool:
        s = get_settings()
        return bool(s.HELIUS_API_KEY) or s.data_mode == "replay"

    async def _page(self, address: str, before: str | None) -> tuple[list[dict], str]:
        settings = get_settings()
        if not settings.HELIUS_API_KEY and settings.data_mode != "replay":
            raise KeyMissing("HELIUS_API_KEY is not set in backend/.env (sign up at https://dashboard.helius.dev).")
        params: dict[str, Any] = {"api-key": settings.HELIUS_API_KEY or "REPLAY", "limit": PAGE_LIMIT}
        if before:
            params["before-signature"] = before
        res = await fetch_json(
            chain=self.chain,
            provider=self.provider,
            url=f"{self.base_url}/v0/addresses/{address}/transactions",
            params=params,
            rate_per_sec=self.rate,
            limiter_name="helius",
        )
        data = res.data
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"Helius error: {data.get('error')}")
        note_ok(self.chain)
        return (data or []), res.ref

    async def get_transfers(
        self, address: str, since: int | None, until: int | None, limit: int, start_block: int | None = None
    ) -> list[Transfer]:
        price = await native_usd_price(self.chain)
        out: list[Transfer] = []
        before = None
        seen = 0
        while seen < limit:
            batch, ref = await self._page(address, before)
            if not batch:
                break
            stop = False
            for tx in batch:
                seen += 1
                ts = int(tx.get("timestamp") or 0)
                if until is not None and ts > until:
                    continue
                if since is not None and ts < since:
                    stop = True
                    break
                if tx.get("transactionError"):
                    continue
                out.extend(self._rows(tx, ref, price))
            before = batch[-1].get("signature")
            if stop or len(batch) < PAGE_LIMIT or not before:
                break
        out = [t for t in out if address in (t.from_addr, t.to_addr)]
        out.sort(key=lambda t: (t.block_time, t.tx_hash), reverse=True)
        return out

    def _rows(self, tx: dict, ref: str, price: Decimal | None) -> list[Transfer]:
        rows: list[Transfer] = []
        sig = tx.get("signature", "")
        ts = int(tx.get("timestamp") or 0)
        slot = tx.get("slot")
        for nt in tx.get("nativeTransfers") or []:
            amount = Decimal(int(nt.get("amount") or 0)) / LAMPORTS
            rows.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=sig,
                    block_time=ts,
                    from_addr=nt.get("fromUserAccount") or "",
                    to_addr=nt.get("toUserAccount") or "",
                    asset="SOL",
                    token_contract=None,
                    amount=format(amount.normalize() if amount else Decimal(0), "f"),
                    usd_estimate=usd_for(amount, price),
                    is_internal=False,
                    raw_ref=ref,
                    block_number=slot,
                )
            )
        for tt in tx.get("tokenTransfers") or []:
            mint = tt.get("mint")
            tok = self.tokens.get(mint)
            amount = Decimal(str(tt.get("tokenAmount") or 0))
            rows.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=sig,
                    block_time=ts,
                    from_addr=tt.get("fromUserAccount") or "",
                    to_addr=tt.get("toUserAccount") or "",
                    asset=tok["symbol"] if tok else "SPL",
                    token_contract=mint,
                    amount=format(amount.normalize() if amount else Decimal(0), "f"),
                    usd_estimate=usd_for(amount, None, stablecoin=True) if tok else None,
                    is_internal=False,
                    raw_ref=ref,
                    block_number=slot,
                    in_scope=tok is not None,
                )
            )
        return rows

    async def get_tx(self, tx_id: str) -> RawTx:
        raise ProviderError("Single-transaction lookup is not implemented for Solana; use address history.")

    async def status(self) -> ProviderStatus:
        settings = get_settings()
        rt = runtime_status(self.chain)
        key_present = bool(settings.HELIUS_API_KEY)
        if settings.data_mode == "replay":
            st = "replay"
        elif not key_present:
            st = "disabled"
        elif rt.get("last_error") and not rt.get("last_ok_at"):
            st = "error"
        else:
            st = "ok"
        return ProviderStatus(
            chain=self.chain,
            provider="Helius enhanced transactions",
            available=st in ("ok", "replay"),
            status=st,
            plan="free developer key" if key_present else "disabled until HELIUS_API_KEY is set",
            limits="5 req/s client-side",
            key_env="HELIUS_API_KEY",
            key_present=key_present,
            last_error=rt.get("last_error"),
            last_ok_at=rt.get("last_ok_at"),
        )

