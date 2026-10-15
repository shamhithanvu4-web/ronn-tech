"""M6: Solana (Helius) and Polygon adapters through the engine, and automatic EVM
cross-chain mapping (the same address on several EVM chains is one key holder).
Synthetic data recorded through the fake APIs, then replayed offline."""
from __future__ import annotations

import asyncio

import httpx

from app.engine.datasource import ProviderDataSource
from app.engine.pipeline import analyse
from app.providers import base, cache
from app.providers.context import AnalysisContext, use_context
from tests.engine_helpers import InMemoryLabels
from tests.fake_apis import FakeAPIs
from tests.helpers import load


def _record_then_replay(scenario, chain_hint, data_mode, monkeypatch):
    data_mode("record")
    monkeypatch.setattr(base, "HTTP_TRANSPORT", FakeAPIs(scenario).transport())
    with use_context(AnalysisContext(analysis_id="rec")):
        recorded = asyncio.run(analyse(scenario["suspect"], ProviderDataSource(), InMemoryLabels(scenario), chain_hint=chain_hint))
    cache.clear()
    data_mode("replay")

    def offline(_request):
        raise AssertionError("network used in replay mode")

    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(offline))
    with use_context(AnalysisContext(analysis_id="rep")):
        replayed = asyncio.run(analyse(scenario["suspect"], ProviderDataSource(), InMemoryLabels(scenario), chain_hint=chain_hint))
    return recorded, replayed


def test_solana_helius_path_to_exchange(fixtures_dir, evidence_dir, data_mode, monkeypatch):
    sc = load("synthetic_engine_solana.json")
    monkeypatch.setenv("HELIUS_API_KEY", "TEST-KEY-NOT-REAL")
    recorded, replayed = _record_then_replay(sc, None, data_mode, monkeypatch)
    for r in (recorded, replayed):
        assert r["suspect"]["family"] == "solana" and r["suspect"]["active_chains"] == ["solana"]
        nv = r["summary"]["nearest_vasp"]
        assert nv["entity_name"].startswith("Zetax") and nv["hops"] == 1 and nv["chain"] == "solana"
        hop = r["candidates"][0]["paths"][0]["hops_detail"][0]
        assert hop["transfers"][0]["tx_hash"] == "syn-sol-2" and hop["transfers"][0]["explorer_url"].endswith("syn-sol-2")
    for f in fixtures_dir.rglob("*.json"):
        assert "TEST-KEY-NOT-REAL" not in f.read_text()


def test_solana_disabled_without_key(data_mode, monkeypatch):
    from app.errors import KeyMissing

    sc = load("synthetic_engine_solana.json")
    monkeypatch.setenv("HELIUS_API_KEY", "")
    data_mode("live")  # in replay mode Solana stays enabled so recorded fixtures can be used

    def offline(_request):
        raise AssertionError("a disabled chain must not be queried")

    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(offline))
    try:
        asyncio.run(analyse(sc["suspect"], ProviderDataSource(), InMemoryLabels(sc)))
    except KeyMissing as exc:
        assert "solana" in exc.detail
    else:  # pragma: no cover
        raise AssertionError("expected KeyMissing")


def test_evm_cross_chain_mapping(fixtures_dir, evidence_dir, data_mode, monkeypatch):
    sc = load("synthetic_engine_cross_evm.json")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "TEST-KEY-NOT-REAL")
    recorded, replayed = _record_then_replay(sc, None, data_mode, monkeypatch)
    for r in (recorded, replayed):
        statuses = {c["chain"]: c["status"] for c in r["cross_chain"]["evm_mapping"]}
        assert statuses["ethereum"] == "active" and statuses["polygon"] == "active"
        assert statuses["arbitrum"] in ("inactive", "requires_paid_plan") and "bsc" in statuses
        assert set(r["suspect"]["active_chains"]) == {"ethereum", "polygon"}
        assert "one key holder" in r["cross_chain"]["same_key_holder_note"]
        nv = r["summary"]["nearest_vasp"]
        assert nv["entity_name"].startswith("Thetax") and nv["chain"] == "polygon" and nv["hops"] == 1
    assert [c["entity_name"] for c in recorded["candidates"]] == [c["entity_name"] for c in replayed["candidates"]]
