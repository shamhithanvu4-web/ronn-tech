"""Install a synthetic scenario as replay fixtures + DB labels, so API tests can run the
real background job end to end offline. Synthetic data stays in tests/fixtures/synthetic_*."""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import delete

from app.db import session_scope
from app.engine.datasource import ProviderDataSource
from app.engine.pipeline import analyse
from app.labels.store import IngestResult, LabelRow, replace_source
from app.models import Label
from app.providers import base
from app.providers.context import AnalysisContext, use_context
from tests.engine_helpers import InMemoryLabels
from tests.fake_apis import FakeAPIs
from tests.helpers import load

SOURCE = "synthetic_world"


def install_world(name: str, monkeypatch, data_mode) -> dict:
    scenario = load(name)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "TEST-KEY-NOT-REAL")
    monkeypatch.setenv("TRONGRID_API_KEY", "TEST-KEY-NOT-REAL")
    data_mode("record")
    monkeypatch.setattr(base, "HTTP_TRANSPORT", FakeAPIs(scenario).transport())
    with use_context(AnalysisContext(analysis_id=None)):
        asyncio.run(analyse(scenario["suspect"], ProviderDataSource(), InMemoryLabels(scenario), chain_hint=scenario["chain"]))
    monkeypatch.setattr(base, "HTTP_TRANSPORT", None)
    data_mode("replay")
    rows = [
        LabelRow(l["chain"], l["address"], l["entity_name"], l["entity_type"], l["is_vasp"], l["role"], SOURCE,
                 "https://example.invalid/synthetic", l["reliability"], l["tier"], "2026-01-01T00:00:00Z", {})
        for l in scenario["labels"]
    ]
    with session_scope() as db:
        replace_source(db, IngestResult(source=SOURCE, rows=rows, source_url=None, version="test"))
    return scenario


def remove_world() -> None:
    with session_scope() as db:
        db.execute(delete(Label).where(Label.source.in_([SOURCE, "inferred_deposit", "inferred_btc_cluster"])))


def wait_done(client, headers, analysis_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/analyses/{analysis_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"analysis {analysis_id} did not finish in {timeout}s")
