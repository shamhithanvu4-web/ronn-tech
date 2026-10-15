"""Provider adapters in replay mode (synthetic fixtures), record mode, retries, plan gating.
Also parses any *real* recorded fixtures found in backend/fixtures/ (skipped if none)."""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.config import FIXTURES_DIR
from app.errors import PlanRequired, ReplayMiss
from app.providers import base, make_provider
from app.providers.context import AnalysisContext, use_context
from app.providers.recorder import canonical_url, fixture_path, request_key
from tests.helpers import install_set

BTC_A = "bc1q5xs6rgdp5xs6rgdp5xs6rgdp5xs6rgdp4gkthz"
BTC_B = "bc1qk2et9v4jk2et9v4jk2et9v4jk2et9v4jhzxjr8"
BTC_X = "bc1q7mm0dahk7mm0dahk7mm0dahk7mm0dahk8sttd7"
EVM_S = "0x1111111111111111111111111111111111111111"
TRON_S = "TQhqRHgEonKEYqudomS8243o3bejg8dt1d"


@pytest.mark.asyncio
async def test_bitcoin_replay_proportional_attribution(fixtures_dir, evidence_dir):
    install_set("synthetic_providers.json")
    ctx = AnalysisContext(analysis_id="t-btc", evidence_dir=evidence_dir / "t-btc")
    with use_context(ctx):
        rows = await make_provider("bitcoin").get_transfers(BTC_A, None, None, 100)
    # Spend tx: A contributed 60 % of inputs -> 60 % of each output; receive tx: one inbound row.
    out = {(r.from_addr, r.to_addr): r for r in rows}
    assert out[(BTC_A, BTC_B)].amount == "0.42000000"
    assert out[(BTC_A, "bc1qc0pu8s7rc0pu8s7rc0pu8s7rc0pu8s7rfdgfg5")].amount == "0.17400000"
    assert (BTC_X, BTC_B) not in out  # rows not involving the queried address are dropped
    inbound = [r for r in rows if r.to_addr == BTC_A]
    assert len(inbound) == 1 and inbound[0].amount == "0.60000000"
    assert inbound[0].usd_estimate == "30000.00"
    assert all(r.tx_hash != "synthetic-btc-mempool" for r in rows)
    # Every raw response was captured as evidence with its SHA-256.
    assert ctx.api_calls == 2 and len(ctx.evidence) == 2
    assert all(len(e["content_sha256"]) == 64 for e in ctx.evidence)


@pytest.mark.asyncio
async def test_etherscan_replay_tokens_by_contract(fixtures_dir):
    install_set("synthetic_providers.json")
    rows = await make_provider("ethereum").get_transfers(EVM_S, None, None, 1000)
    hashes = {r.tx_hash for r in rows}
    assert "0xsynthetic02" not in hashes  # failed transaction skipped
    usdt = next(r for r in rows if r.tx_hash == "0xsynthetic03")
    assert usdt.asset == "USDT" and usdt.amount == "1000" and usdt.usd_estimate == "1000.00" and usdt.in_scope
    fake = next(r for r in rows if r.tx_hash == "0xsynthetic04")
    assert fake.in_scope is False and fake.usd_estimate is None  # fake "USDT": wrong contract
    eth = next(r for r in rows if r.tx_hash == "0xsynthetic01")
    assert eth.asset == "ETH" and Decimal(eth.amount) == Decimal("1.5") and eth.usd_estimate == "3000.00"
    assert eth.block_number == 18000000


@pytest.mark.asyncio
async def test_bsc_plan_gating_is_detected_not_faked(fixtures_dir):
    install_set("synthetic_providers.json")
    prov = make_provider("bsc")
    with pytest.raises(PlanRequired):
        await prov.get_transfers(EVM_S, None, None, 1000)
    status = await prov.status()
    assert status.plan == "requires paid plan"


@pytest.mark.asyncio
async def test_trongrid_fingerprint_pagination_and_hex_addresses(fixtures_dir):
    install_set("synthetic_providers.json")
    rows = await make_provider("tron").get_transfers(TRON_S, None, None, 1000)
    usdt = sorted((r for r in rows if r.asset == "USDT"), key=lambda r: r.block_time)
    assert [r.tx_hash for r in usdt] == ["synthetic-tron-01", "synthetic-tron-02"]
    assert usdt[1].amount == "495" and usdt[1].usd_estimate == "495.00"
    trx = [r for r in rows if r.asset == "TRX"]
    assert len(trx) == 1
    assert trx[0].from_addr == "TTpKHSFUdoi9j2zacMcRx522rztL61ojFS" and trx[0].to_addr == TRON_S
    assert trx[0].amount == "30" and trx[0].usd_estimate == "7.50"


@pytest.mark.asyncio
async def test_replay_miss_is_explicit(fixtures_dir):
    with pytest.raises(ReplayMiss):
        await make_provider("bitcoin").get_transfers(BTC_B, None, None, 10)


@pytest.mark.asyncio
async def test_budget_is_enforced(fixtures_dir):
    from app.providers.context import BudgetExceeded

    install_set("synthetic_providers.json")
    ctx = AnalysisContext(analysis_id="t-budget", api_call_budget=1)
    with use_context(ctx), pytest.raises(BudgetExceeded):
        await make_provider("tron").get_transfers(TRON_S, None, None, 1000)


@pytest.mark.asyncio
async def test_record_mode_saves_fixture_without_api_key(fixtures_dir, data_mode, monkeypatch):
    data_mode("record")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "SECRET-KEY-123")
    from app import config

    config.reset_settings_cache()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")  # retried with backoff
        assert request.url.params.get("apikey") == "SECRET-KEY-123"
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": "18000000"})

    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(base, "_backoff", lambda attempt, retry_after=None: 0.0)
    prov = make_provider("ethereum")
    block = await prov.block_at(1700000000, "before")
    assert block == 18000000 and calls["n"] == 2
    params = {"chainid": 1, "module": "block", "action": "getblocknobytime", "timestamp": 1700000000, "closest": "before"}
    key, _ = request_key("GET", canonical_url("https://api.etherscan.io/v2/api", params), None)
    path = fixture_path("ethereum", key)
    assert path.exists()
    text = path.read_text()
    assert "SECRET-KEY-123" not in text and "apikey" not in text
    # Second call is served from the cache: no new HTTP request.
    prov2 = make_provider("ethereum")
    assert await prov2.block_at(1700000000, "before") == 18000000
    assert calls["n"] == 2


def _real_fixture_files():
    if not FIXTURES_DIR.exists():
        return []
    return [p for p in FIXTURES_DIR.glob("*/*.json")]


@pytest.mark.skipif(not _real_fixture_files(), reason="no real fixtures recorded yet (run scripts/record_fixtures.py with API keys)")
def test_real_recorded_fixtures_are_intact():
    from app.util import sha256_hex

    for path in _real_fixture_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        assert sha256_hex(data["body"]) == data["content_sha256"], path
        assert "apikey=" not in data["url"] and "api-key=" not in data["url"], path
