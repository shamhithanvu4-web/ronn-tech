"""Bitcoin via Blockstream Esplora (fallback mempool.space). No API key.

UTXO transactions are converted to Transfer rows by attributing each output to
the input addresses in proportion to their share of the total input value.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.config import chain_config, get_settings
from app.errors import ProviderError, ReplayMiss
from app.providers.base import (
    FetchResult,
    ProviderStatus,
    RawTx,
    Transfer,
    TxIO,
    fetch_json,
    note_ok,
    runtime_status,
)
from app.providers.prices import native_usd_price, usd_for

SATS = Decimal(100_000_000)
PAGE_SIZE = 25  # confirmed txs per Esplora page


def sats_to_btc(v: int | str | None) -> Decimal:
    return (Decimal(int(v or 0)) / SATS).quantize(Decimal("0.00000001"))


class BitcoinEsploraProvider:
    chain = "bitcoin"
    provider = "esplora"

    def __init__(self) -> None:
        cfg = chain_config(self.chain)
        self.base_urls: list[str] = list(cfg.get("base_urls") or ["https://blockstream.info/api"])
        self.rate = float(cfg.get("rate_per_sec", 2))
        self._tx_memo: dict[str, RawTx] = {}

    async def _get(self, path: str) -> FetchResult:
        last: Exception | None = None
        for base in self.base_urls:
            try:
                res = await fetch_json(
                    chain=self.chain,
                    provider=self.provider,
                    url=f"{base}{path}",
                    rate_per_sec=self.rate,
                    limiter_name="esplora",
                )
                note_ok(self.chain)
                return res
            except ReplayMiss as exc:
                last = exc
                continue
            except ProviderError as exc:
                last = exc
                continue
        assert last is not None
        raise last

    # ------------------------------------------------------------------ parsing
    def _parse_tx(self, tx: dict[str, Any], ref: str) -> RawTx:
        status = tx.get("status") or {}
        inputs = []
        for i, vin in enumerate(tx.get("vin") or []):
            prev = vin.get("prevout") or {}
            if vin.get("is_coinbase"):
                continue
            inputs.append(TxIO(address=prev.get("scriptpubkey_address"), value=str(sats_to_btc(prev.get("value"))), index=i))
        outputs = []
        for i, vout in enumerate(tx.get("vout") or []):
            outputs.append(TxIO(address=vout.get("scriptpubkey_address"), value=str(sats_to_btc(vout.get("value"))), index=i))
        raw = RawTx(
            chain=self.chain,
            tx_id=tx["txid"],
            block_time=status.get("block_time"),
            block_height=status.get("block_height"),
            inputs=inputs,
            outputs=outputs,
            fee=str(sats_to_btc(tx.get("fee"))) if tx.get("fee") is not None else None,
            raw_ref=ref,
        )
        self._tx_memo[raw.tx_id] = raw
        return raw

    @staticmethod
    def transfers_from_tx(raw: RawTx, address: str, price: Decimal | None, ref: str) -> list[Transfer]:
        """Transfer rows involving `address` from one UTXO transaction."""
        if raw.block_time is None:
            return []
        in_by_addr: dict[str, Decimal] = defaultdict(Decimal)
        for vin in raw.inputs:
            if vin.address:
                in_by_addr[vin.address] += Decimal(vin.value)
        total_in = sum(in_by_addr.values(), Decimal(0))
        if total_in <= 0:
            return []
        out_by_addr: dict[str, Decimal] = defaultdict(Decimal)
        for vout in raw.outputs:
            if vout.address:
                out_by_addr[vout.address] += Decimal(vout.value)
        rows: list[Transfer] = []
        for src, src_in in in_by_addr.items():
            share = src_in / total_in
            for dst, out_val in out_by_addr.items():
                if src == dst:
                    continue  # change back to the same address
                if address not in (src, dst):
                    continue
                amount = (out_val * share).quantize(Decimal("0.00000001"))
                if amount <= 0:
                    continue
                rows.append(
                    Transfer(
                        chain="bitcoin",
                        tx_hash=raw.tx_id,
                        block_time=int(raw.block_time),
                        from_addr=src,
                        to_addr=dst,
                        asset="BTC",
                        token_contract=None,
                        amount=format(amount, "f"),
                        usd_estimate=usd_for(amount, price),
                        is_internal=False,
                        raw_ref=ref,
                        block_number=raw.block_height,
                    )
                )
        return rows

    # ------------------------------------------------------------------- public
    async def get_address_txs(self, address: str, since: int | None, until: int | None, limit: int) -> list[RawTx]:
        txs: list[RawTx] = []
        res = await self._get(f"/address/{address}/txs")
        page = res.data or []
        last_txid = None
        while True:
            confirmed = [t for t in page if (t.get("status") or {}).get("confirmed")]
            for t in confirmed:
                raw = self._parse_tx(t, res.ref)
                last_txid = raw.tx_id
                if until is not None and raw.block_time and raw.block_time > until:
                    continue
                if since is not None and raw.block_time and raw.block_time < since:
                    continue
                txs.append(raw)
            oldest = min((t.get("status", {}).get("block_time") or 0 for t in confirmed), default=0)
            if len(txs) >= limit or len(confirmed) < PAGE_SIZE or last_txid is None:
                break
            if since is not None and oldest and oldest < since:
                break
            res = await self._get(f"/address/{address}/txs/chain/{last_txid}")
            page = res.data or []
            if not page:
                break
        return txs[:limit]

    async def get_transfers(
        self, address: str, since: int | None, until: int | None, limit: int, start_block: int | None = None
    ) -> list[Transfer]:
        txs = await self.get_address_txs(address, since, until, limit)
        price = await native_usd_price(self.chain)
        rows: list[Transfer] = []
        for raw in txs:
            rows.extend(self.transfers_from_tx(raw, address, price, raw.raw_ref))
        rows.sort(key=lambda r: r.block_time, reverse=True)
        return rows

    async def get_address_stats(self, address: str) -> dict[str, Any]:
        res = await self._get(f"/address/{address}")
        data = res.data or {}
        chain_stats = data.get("chain_stats") or {}
        return {"tx_count": int(chain_stats.get("tx_count") or 0)}

    async def get_tx(self, tx_id: str) -> RawTx:
        if tx_id in self._tx_memo:
            return self._tx_memo[tx_id]
        res = await self._get(f"/tx/{tx_id}")
        return self._parse_tx(res.data, res.ref)

    async def status(self) -> ProviderStatus:
        rt = runtime_status(self.chain)
        mode = get_settings().data_mode
        return ProviderStatus(
            chain=self.chain,
            provider="Blockstream Esplora (fallback mempool.space)",
            available=True,
            status="replay" if mode == "replay" else ("error" if rt.get("last_error") and not rt.get("last_ok_at") else "ok"),
            plan="keyless public API",
            limits="throttled to 2 req/s",
            key_env=None,
            key_present=True,
            last_error=rt.get("last_error"),
            last_ok_at=rt.get("last_ok_at"),
        )
