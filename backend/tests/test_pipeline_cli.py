"""`python -m app.engine.pipeline <address>` prints candidates with factor breakdowns,
and background jobs persist results, evidence and inferred labels.
Uses synthetic fixtures recorded through the fake API and synthetic labels in the DB."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from app.db import session_scope
from app.engine.datasource import ProviderDataSource
from app.engine.pipeline import _cli, analyse
from app.labels.store import IngestResult, LabelRow, replace_source
from app.models import Analysis, Label, RawResponse
from app.providers import base
from app.providers.context import AnalysisContext, use_context
from tests.engine_helpers import InMemoryLabels
from tests.fake_apis import FakeAPIs
from tests.helpers import load

SCENARIO = "synthetic_engine_deposit_hop0.json"


@pytest.fixture()
def synthetic_world(fixtures_dir, evidence_dir, data_mode, monkeypatch):
    scenario = load(SCENARIO)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "TEST-KEY-NOT-REAL")
    data_mode("record")
    monkeypatch.setattr(base, "HTTP_TRANSPORT", FakeAPIs(scenario).transport())
    with use_context(AnalysisContext(analysis_id=None)):
        asyncio.run(analyse(scenario["suspect"], ProviderDataSource(), InMemoryLabels(scenario), chain_hint="ethereum"))
    monkeypatch.setattr(base, "HTTP_TRANSPORT", None)
    data_mode("replay")
    rows = [
        LabelRow(l["chain"], l["address"], l["entity_name"], l["entity_type"], l["is_vasp"], l["role"], "synthetic_cli",
                 "https://example.invalid/synthetic", l["reliability"], l["tier"], "2026-01-01T00:00:00Z", {})
        for l in scenario["labels"]
    ]
    with session_scope() as db:
        replace_source(db, IngestResult(source="synthetic_cli", rows=rows, source_url=None, version="test"))
    yield scenario
    with session_scope() as db:
        db.execute(delete(Label).where(Label.source.in_(["synthetic_cli", "inferred_deposit"])))


def test_cli_prints_candidates_with_factors(synthetic_world, capsys):
    code = _cli([synthetic_world["suspect"], "--chain", "ethereum"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Nearest VASP: Acmex (synthetic) · Confidence 85 · High · 0 hop(s) · via inferred deposit address" in out
    assert "R     H     V        T" in out and "0.85  1.00  1.0000  1.0" in out
    assert "what would raise this:" in out


def test_cli_reports_invalid_address(capsys):
    assert _cli(["0x123"]) == 2
    assert "invalid_address" in capsys.readouterr().err


def test_job_persists_result_evidence_and_inferred_label(synthetic_world):
    from app.engine import jobs

    aid = jobs.create_analysis(synthetic_world["suspect"], "analyst", chain="ethereum")
    result = jobs.run_analysis(aid)
    assert result is not None
    with session_scope() as db:
        a = db.get(Analysis, aid)
        assert a.status == "done" and a.stage == "done" and a.progress == 100
        assert a.nearest_entity_name.startswith("Acmex") and a.nearest_confidence == 85.0 and a.nearest_hops == 0
        assert len(a.result_sha256) == 64
        raws = db.execute(select(RawResponse).where(RawResponse.analysis_id == aid)).scalars().all()
        assert raws and all(len(r.content_sha256) == 64 for r in raws)
        inferred = db.execute(select(Label).where(Label.source == "inferred_deposit")).scalars().all()
        assert len(inferred) == 1 and inferred[0].tier == "C" and inferred[0].reliability == 0.85
        assert inferred[0].evidence["sweep_txs"] == ["syn-sweep-1"]


def test_job_failure_is_recorded(fixtures_dir, data_mode):
    from app.engine import jobs

    data_mode("replay")
    aid = jobs.create_analysis("0x" + "ab" * 20, "analyst", chain="ethereum")
    assert jobs.run_analysis(aid) is None
    with session_scope() as db:
        a = db.get(Analysis, aid)
        assert a.status == "failed" and a.error_code in ("upstream_provider_error", "replay_fixture_missing")
