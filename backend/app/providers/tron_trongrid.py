"""TRON via TronGrid (header TRON-PRO-API-KEY). USDT-TRC20 is the priority asset.

- TRC-20: /v1/accounts/{a}/transactions/trc20?limit=200&only_confirmed=true&contract_address=...
  with fingerprint pagination.
- Native TRX: /v1/accounts/{a}/transactions (TransferContract and internal transfers).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.config import chain_config, get_settings, tokens_for
from app.engine.validate import tron_hex_to_base58
from app.errors import ProviderError
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

PAGE_LIMIT = 200
SUN = Decimal(1_000_000)


def _to_b58(addr: str | None) -> str:
    if not addr:
        return ""
    if addr.startswith("T"):
        return addr
    try:
        return tron_hex_to_base58(addr)
    except ValueError:
        return addr


class TronGridProvider:
    chain = "tron"
    provider = "trongrid"

    def __init__(self) -> None:
        cfg = chain_config(self.chain)
        self.base_url = cfg.get("base_url", "https://api.trongrid.io")
        self.rate = float(cfg.get("rate_per_sec", 10))
        self.tokens = {t["contract"]: t for t in tokens_for(self.chain)}

    def _headers(self) -> dict[str, str]:
        key = get_settings().TRONGRID_API_KEY
        return {"TRON-PRO-API-KEY": key} if key else {}

    async def _get(self, path: str, params: dict[str, Any]) -> tuple[dict, str]:
        res = await fetch_json(
            chain=self.chain,
            provider=self.provider,
            url=f"{self.base_url}{path}",
            params=params,
            headers=self._headers(),
            rate_per_sec=self.rate,
            limiter_name="trongrid",
            cacheable=lambda d: isinstance(d, dict) and d.get("success", True) is not False,
        )
        data = res.data or {}
        if isinstance(data, dict) and data.get("success") is False:
            msg = str(data.get("error") or data.get("message") or "unknown error")
            note_error(self.chain, msg)
            raise ProviderError(f"TronGrid error: {msg}")
        note_ok(self.chain)
        return data, res.ref

    async def _paged(self, path: str, params: dict[str, Any], limit: int) -> list[tuple[dict, str]]:
        rows: list[tuple[dict, str]] = []
        fingerprint = None
        while len(rows) < limit:
            p = dict(params)
            p["limit"] = PAGE_LIMIT
            if fingerprint:
                p["fingerprint"] = fingerprint
            data, ref = await self._get(path, p)
            batch = data.get("data") or []
            rows.extend((r, ref) for r in batch)
            fingerprint = (data.get("meta") or {}).get("fingerprint")
            if not fingerprint or not batch:
                break
        return rows[:limit]

    async def get_transfers(
        self, address: str, since: int | None, until: int | None, limit: int, start_block: int | None = None
    ) -> list[Transfer]:
        base_params: dict[str, Any] = {"only_confirmed": "true"}
        if since is not None:
            base_params["min_timestamp"] = int(since) * 1000
        if until is not None:
            base_params["max_timestamp"] = int(until) * 1000
        jobs = []
        for contract in self.tokens:
            params = dict(base_params, contract_address=contract)
            jobs.append(self._paged(f"/v1/accounts/{address}/transactions/trc20", params, limit))
        jobs.append(self._paged(f"/v1/accounts/{address}/transactions", dict(base_params), limit))
        results = await asyncio.gather(*jobs)
        price = await native_usd_price(self.chain)
        out: list[Transfer] = []
        for rows in results[:-1]:
            for row, ref in rows:
                if row.get("type") not in (None, "Transfer"):
                    continue
                info = row.get("token_info") or {}
                contract = info.get("address")
                tok = self.tokens.get(contract)
                decimals = int(info.get("decimals") or (tok or {}).get("decimals") or 6)
                amount = Decimal(int(row.get("value") or 0)) / (Decimal(10) ** decimals)
                out.append(
                    Transfer(
                        chain=self.chain,
                        tx_hash=row["transaction_id"],
                        block_time=int(row["block_timestamp"]) // 1000,
                        from_addr=_to_b58(row.get("from")),
                        to_addr=_to_b58(row.get("to")),
                        asset=tok["symbol"] if tok else str(info.get("symbol") or "TRC20")[:16],
                        token_contract=contract,
                        amount=format(amount.normalize() if amount else Decimal(0), "f"),
                        usd_estimate=usd_for(amount, None, stablecoin=bool(tok and tok.get("stablecoin"))) if tok else None,
                        is_internal=False,
                        raw_ref=ref,
                        in_scope=tok is not None,
                    )
                )
        for row, ref in results[-1]:
            out.extend(self._native_rows(row, ref, price))
        if since is not None:
            out = [t for t in out if t.block_time >= since]
        if until is not None:
            out = [t for t in out if t.block_time <= until]
        out.sort(key=lambda t: (t.block_time, t.tx_hash), reverse=True)
        return out

    def _native_rows(self, row: dict, ref: str, price: Decimal | None) -> list[Transfer]:
        rows: list[Transfer] = []
        if "internal_tx_id" in row:
            value = ((row.get("data") or {}).get("call_value") or {}).get("_")
            if not value:
                return rows
            amount = Decimal(int(value)) / SUN
            rows.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=row.get("tx_id") or row["internal_tx_id"],
                    block_time=int(row.get("block_timestamp") or 0) // 1000,
                    from_addr=_to_b58(row.get("from_address")),
                    to_addr=_to_b58(row.get("to_address")),
                    asset="TRX",
                    token_contract=None,
                    amount=format(amount.normalize(), "f"),
                    usd_estimate=usd_for(amount, price),
                    is_internal=True,
                    raw_ref=ref,
                )
            )
            return rows
        ret = (row.get("ret") or [{}])[0]
        if ret.get("contractRet") not in (None, "SUCCESS"):
            return rows
        for contract in (row.get("raw_data") or {}).get("contract") or []:
            if contract.get("type") != "TransferContract":
                continue
            val = (contract.get("parameter") or {}).get("value") or {}
            amount = Decimal(int(val.get("amount") or 0)) / SUN
            rows.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=row["txID"],
                    block_time=int(row.get("block_timestamp") or 0) // 1000,
                    from_addr=_to_b58(val.get("owner_address")),
                    to_addr=_to_b58(val.get("to_address")),
                    asset="TRX",
                    token_contract=None,
                    amount=format(amount.normalize() if amount else Decimal(0), "f"),
                    usd_estimate=usd_for(amount, price),
                    is_internal=False,
                    raw_ref=ref,
                    block_number=row.get("blockNumber"),
                )
            )
        return rows

    async def get_tx(self, tx_id: str) -> RawTx:
        res = await fetch_json(
            chain=self.chain,
            provider=self.provider,
            url=f"{self.base_url}/wallet/gettransactionbyid",
            method="POST",
            json_body={"value": tx_id},
            headers=self._headers(),
            rate_per_sec=self.rate,
            limiter_name="trongrid",
        )
        tx = res.data or {}
        inputs, outputs = [], []
        for contract in (tx.get("raw_data") or {}).get("contract") or []:
            val = (contract.get("parameter") or {}).get("value") or {}
            amount = format(Decimal(int(val.get("amount") or 0)) / SUN, "f")
            inputs.append(TxIO(address=_to_b58(val.get("owner_address")), value=amount))
            outputs.append(TxIO(address=_to_b58(val.get("to_address") or val.get("contract_address")), value=amount))
        return RawTx(chain=self.chain, tx_id=tx_id, block_time=None, block_height=None, inputs=inputs, outputs=outputs, raw_ref=res.ref)

    async def status(self) -> ProviderStatus:
        settings = get_settings()
        rt = runtime_status(self.chain)
        key_present = bool(settings.TRONGRID_API_KEY)
        if settings.data_mode == "replay":
            st = "replay"
        elif rt.get("last_error") and not rt.get("last_ok_at"):
            st = "error"
        else:
            st = "ok"
        return ProviderStatus(
            chain=self.chain,
            provider="TronGrid",
            available=st in ("ok", "replay"),
            status=st,
            plan="free key" if key_present else "keyless (heavily rate-limited)",
            limits="10 req/s client-side; TronGrid daily quota per key",
            key_env="TRONGRID_API_KEY",
            key_present=key_present,
            last_error=rt.get("last_error"),
            last_ok_at=rt.get("last_ok_at"),
            note=None if key_present else "Add TRONGRID_API_KEY for reliable access.",
        )
