"""M6: ground-truth discovery and the evaluation metrics, on synthetic scenarios served by
the fake APIs (the real run uses live data; see scripts/build_ground_truth.py)."""
from __future__ import annotations

import asyncio

from app.engine import groundtruth as gt
from app.engine.datasource import ProviderDataSource
from app.engine.evaluation import evaluate, markdown, percentile
from app.providers import base
from app.providers.context import AnalysisContext, use_context
from tests.engine_helpers import InMemoryLabels, run_scenario
from tests.fake_apis import FakeAPIs
from tests.helpers import load
from tests.world import install_world, remove_world


def _hop0_positive(data_mode, monkeypatch):
    sc = load("synthetic_engine_deposit_hop0.json")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "TEST-KEY-NOT-REAL")
    data_mode("record")
    monkeypatch.setattr(base, "HTTP_TRANSPORT", FakeAPIs(sc).transport())
    hl = sc["labels"][0]
    hot = gt.HotWallet("ethereum", hl["address"], hl["entity_id"], hl["entity_name"], "synthetic", "A")
    with use_context(AnalysisContext(analysis_id=None)):
        positives, _near = asyncio.run(gt.positives_for_hot(ProviderDataSource(), InMemoryLabels(sc), hot))
    return sc, positives


def test_positives_come_from_sweep_verified_deposits(fixtures_dir, evidence_dir, data_mode, monkeypatch):
    sc, positives = _hop0_positive(data_mode, monkeypatch)
    assert len(positives) == 1
    p = positives[0]
    funder = next(t["from"] for t in sc["transfers"] if t["to"] == sc["suspect"] and t["tx_hash"] == "syn-dep-1")
    assert p.suspect == funder and p.deposit_address == sc["suspect"] and p.hops_upstream == 1
    assert p.expected_entity_name.startswith("Acmex") and "syn-sweep-1" in p.evidence_txs and "syn-dep-1" in p.evidence_txs


def test_label_only_check_for_negatives():
    assert gt.label_only_vasp_within(run_scenario("synthetic_engine_nomatch.json"), 3) == []
    assert gt.label_only_vasp_within(run_scenario("synthetic_engine_two_paths.json"), 3) == ["Epsilonx (synthetic)"]
    assert gt.label_only_vasp_within(run_scenario("synthetic_engine_two_paths.json"), 1) == []


def test_evaluate_reports_measured_metrics(fixtures_dir, evidence_dir, data_mode, monkeypatch):
    _sc, positives = _hop0_positive(data_mode, monkeypatch)
    install_world("synthetic_engine_deposit_hop0.json", monkeypatch, data_mode)
    try:
        hop0, nomatch = load("synthetic_engine_deposit_hop0.json"), load("synthetic_engine_nomatch.json")
        merged = {"transfers": hop0["transfers"] + nomatch["transfers"], "labels": []}
        data_mode("record")
        monkeypatch.setattr(base, "HTTP_TRANSPORT", FakeAPIs(merged).transport())
        truth = {
            "positives": [p.to_dict() for p in positives],
            "negatives": [{"id": "neg-1", "chain": "ethereum", "address": nomatch["suspect"]}],
        }
        out = evaluate(truth, None)
        m = out["metrics"]
        assert m["positives"] == 1 and m["negatives"] == 1 and m["errors"] == 0
        assert m["top1_accuracy"] == 1.0 and m["top3_accuracy"] == 1.0 and m["coverage"] == 1.0
        assert m["false_positive_rate"] == 0.0 and m["runtime_p50"] is not None
        pos = next(r for r in out["rows"] if r["kind"] == "positive")
        # The deposit address was hidden; the engine inferred it and still found the exchange.
        assert pos["nearest"].startswith("Acmex") and pos["nearest_correct"]
        md = markdown({"label": "t", "generated_at": "now", "data_mode": "record", "scoring_config_sha256": "0" * 64, **out})
        assert "Top-1 accuracy | 100.0%" in md and "False-positive rate on negatives | 0.0%" in md
    finally:
        remove_world()


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([1.0], 0.95) == 1.0


def test_negative_verification_ignores_inferred_labels():
    """A wallet whose only VASP evidence is an inferred deposit still counts as a negative
    candidate, so the engine's inference can be measured for false positives."""
    sc = load("synthetic_engine_tron_multihop.json")
    r = run_scenario("synthetic_engine_tron_multihop.json")
    assert r["summary"]["nearest_vasp"]["via"] == "inferred deposit address"
    # The labelled hot wallet sits at hop 3 as the sweep target, so a 2-hop check passes.
    assert gt.label_only_vasp_within(r, 2) == []
    assert gt.label_only_vasp_within(r, 3) == [sc["labels"][0]["entity_name"]]
