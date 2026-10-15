"""In-memory data source and label lookup driven by tests/fixtures/synthetic_engine_*.json."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.engine.pipeline import analyse
from app.labels.store import LabelHit
from app.providers.base import RawTx, Transfer, TxIO
from app.providers.bitcoin_esplora import BitcoinEsploraProvider
from app.providers.context import AnalysisContext, current, use_context
from tests.helpers import load


class SyntheticDataSource:
    def __init__(self, scenario: dict[str, Any]):
        self.chain = scenario["chain"]
        self.raw: list[RawTx] = []
        self.transfers: list[Transfer] = []
        self.calls = 0
        for r in scenario.get("raw_txs", []):
            self.raw.append(
                RawTx(
                    chain=r["chain"], tx_id=r["tx_id"], block_time=r["block_time"], block_height=r["block_height"],
                    inputs=[TxIO(i["address"], i["value"], n) for n, i in enumerate(r["inputs"])],
                    outputs=[TxIO(o["address"], o["value"], n) for n, o in enumerate(r["outputs"])],
                    raw_ref="synthetic",
                )
            )
        self.btc_price = Decimal(scenario["btc_price"]) if scenario.get("btc_price") else None
        for t in scenario.get("transfers", []):
            self.transfers.append(
                Transfer(
                    chain=t["chain"], tx_hash=t["tx_hash"], block_time=t["block_time"], from_addr=t["from"], to_addr=t["to"],
                    asset=t["asset"], token_contract=t["token_contract"], amount=t["amount"], usd_estimate=t["usd"],
                    is_internal=False, raw_ref="synthetic", block_number=t.get("block_number"), in_scope=t.get("in_scope", True),
                )
            )

    def _charge(self):
        self.calls += 1
        ctx = current()
        if ctx is not None:
            ctx.charge_call()  # behave like a provider: each fetch costs one API call

    async def get_transfers(self, chain, address, since, until, limit, start_block=None):
        self._charge()
        if chain != self.chain:
            return []
        if chain == "bitcoin":
            rows = []
            for tx in self._raw_for(address, since, until, limit):
                rows.extend(BitcoinEsploraProvider.transfers_from_tx(tx, address, self.btc_price, "synthetic"))
            return sorted(rows, key=lambda r: r.block_time, reverse=True)
        rows = [
            t for t in self.transfers
            if address in (t.from_addr, t.to_addr)
            and (since is None or t.block_time >= since)
            and (until is None or t.block_time <= until)
            and (start_block is None or t.block_number is None or t.block_number >= start_block)
        ]
        return sorted(rows, key=lambda r: (r.block_time, r.tx_hash), reverse=True)[:limit]

    async def get_raw_txs(self, chain, address, since, until, limit):
        self._charge()
        return self._raw_for(address, since, until, limit)

    def _raw_for(self, address, since, until, limit):
        return [
            tx for tx in self.raw
            if (address in {i.address for i in tx.inputs} or address in {o.address for o in tx.outputs})
            and (since is None or tx.block_time >= since)
            and (until is None or tx.block_time <= until)
        ][:limit]

    async def get_address_stats(self, chain, address):
        return None


class InMemoryLabels:
    def __init__(self, scenario: dict[str, Any]):
        self.hits: dict[tuple[str, str], list[LabelHit]] = {}
        for lab in scenario.get("labels", []):
            hit = LabelHit(
                label_id=None, chain=lab["chain"], address=lab["address"], entity_id=lab["entity_id"],
                entity_name=lab["entity_name"], entity_type=lab["entity_type"], is_vasp=lab["is_vasp"], role=lab["role"],
                source=lab["source"], source_url="https://example.invalid/synthetic", reliability=lab["reliability"],
                tier=lab["tier"], retrieved_at="2026-01-01T00:00:00Z", evidence={},
            )
            self.hits.setdefault((lab["chain"], lab["address"]), []).append(hit)

    def lookup(self, chain, address):
        return sorted(self.hits.get((chain, address), []), key=lambda h: -h.reliability)

    def remember(self, hit):
        self.hits.setdefault((hit.chain, hit.address), [])
        self.hits[(hit.chain, hit.address)] = [h for h in self.hits[(hit.chain, hit.address)] if h.source != hit.source] + [hit]

    async def live_lookup(self, chain, address):
        return None


def run_scenario(name: str, **kwargs: Any) -> dict[str, Any]:
    scenario = load(name)
    ds = SyntheticDataSource(scenario)
    labels = InMemoryLabels(scenario)
    ctx = AnalysisContext(analysis_id=None, api_call_budget=kwargs.pop("api_call_budget", 600))
    with use_context(ctx):
        return asyncio.run(analyse(scenario["suspect"], ds, labels, chain_hint=scenario["chain"], **kwargs))
