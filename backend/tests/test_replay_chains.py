"""One record -> replay round trip per chain through the real provider adapters.

Phase 1 (DATA_MODE=record): the pipeline talks to a fake API (synthetic scenario) and
every raw response is written as a fixture. Phase 2 (DATA_MODE=replay, network removed):
the same analysis must produce the same attribution from fixtures alone, offline."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.engine.datasource import ProviderDataSource
from app.engine.pipeline import analyse
from app.providers import base
from app.providers.context import AnalysisContext, use_context
from tests.engine_helpers import InMemoryLabels, run_scenario
from tests.fake_apis import FakeAPIs
from tests.helpers import load

CASES = [
    ("bitcoin", "synthetic_engine_btc_cluster.json", "Gammax"),
    ("ethereum", "synthetic_engine_deposit_hop0.json", "Acmex"),
    ("tron", "synthetic_engine_tron_multihop.json", "Betax"),
]


def _run(scenario, evidence_dir=None):
    ctx = AnalysisContext(analysis_id="replay-test", evidence_dir=evidence_dir)
    with use_context(ctx):
        result = asyncio.run(
            analyse(scenario["suspect"], ProviderDataSource(), InMemoryLabels(scenario), chain_hint=scenario["chain"])
        )
    return result, ctx


def _summary(r):
    return [(c["entity_name"], c["confidence"], c["min_hops"], c["via"]) for c in r["candidates"]]


@pytest.mark.parametrize("chain,fixture,entity", CASES)
def test_record_then_replay_offline(chain, fixture, entity, fixtures_dir, evidence_dir, data_mode, monkeypatch):
    scenario = load(fixture)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "TEST-KEY-NOT-REAL")
    monkeypatch.setenv("TRONGRID_API_KEY", "TEST-KEY-NOT-REAL")
    fake = FakeAPIs(scenario)

    data_mode("record")
    monkeypatch.setattr(base, "HTTP_TRANSPORT", fake.transport())
    recorded, ctx_rec = _run(scenario, evidence_dir / "rec")
    assert fake.requests, "record phase must hit the (fake) network"
    assert any(f.suffix == ".json" for f in fixtures_dir.rglob("*.json"))
    for f in fixtures_dir.rglob("*.json"):
        assert "TEST-KEY-NOT-REAL" not in f.read_text()

    # Replay: no transport (any network call would fail) and empty cache.
    from app.providers import cache

    cache.clear()
    data_mode("replay")

    def offline(_request):
        raise AssertionError("network used in replay mode")

    monkeypatch.setattr(base, "HTTP_TRANSPORT", httpx.MockTransport(offline))
    replayed, ctx_rep = _run(scenario, evidence_dir / "rep")

    assert _summary(replayed) == _summary(recorded)
    assert replayed["summary"]["nearest_vasp"]["entity_name"].startswith(entity)
    # Same answer as the in-memory engine run on the same synthetic scenario.
    direct = run_scenario(fixture)
    assert _summary(replayed) == _summary(direct)
    # Every response used in replay is evidence with a SHA-256 and the fixture origin.
    assert ctx_rep.evidence and all(e["origin"] == "fixture" and len(e["content_sha256"]) == 64 for e in ctx_rep.evidence)
    assert {e["content_sha256"] for e in ctx_rep.evidence} == {e["content_sha256"] for e in ctx_rec.evidence}
