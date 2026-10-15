"""Engine heuristics on synthetic mini-graphs (tests/fixtures/synthetic_engine_*.json).
One test (or more) per heuristic in SPEC §6."""
from __future__ import annotations

import math

import pytest

from app.engine.scoring import combine, hop_factor, nearest_vasp, tier_for, timing_factor
from tests.engine_helpers import run_scenario


def cand(result, name_prefix):
    return next(c for c in result["candidates"] if c["entity_name"].startswith(name_prefix))


# ---------------------------------------------------------------- §6.4 deposit inference
def test_hop0_deposit_inference_all_three_signals():
    r = run_scenario("synthetic_engine_deposit_hop0.json")
    dep = r["hop0"]["deposit_inference"][0]
    assert dep["reliability"] == 0.85
    assert set(dep["corroborations"]) == {"gas_topup", "exclusivity"}
    assert dep["evidence"]["sweep_txs"] == ["syn-sweep-1"]
    assert dep["evidence"]["deposit_txs"] == ["syn-dep-1"]
    assert dep["evidence"]["gas_topup_txs"] == ["syn-gas-1"]
    nv = r["summary"]["nearest_vasp"]
    assert nv["entity_name"].startswith("Acmex") and nv["hops"] == 0 and nv["confidence"] == 85.0
    assert nv["tier"] == "High" and nv["via"] == "inferred deposit address"
    # The hot wallet appears in the graph via a sweep edge but is not double-counted as a path.
    assert cand(r, "Acmex")["path_count"] == 1
    kinds = {e["kind"] for e in r["graph"]["edges"]}
    assert {"sweep", "gas"} <= kinds


def test_deposit_inference_sweep_only_reliability():
    r = run_scenario("synthetic_engine_deposit_sweep_only.json")
    dep = r["hop0"]["deposit_inference"][0]
    assert dep["reliability"] == 0.70 and dep["corroborations"] == []
    assert cand(r, "Acmex")["confidence"] == 70.0


def test_deposit_inference_respects_tau():
    r = run_scenario("synthetic_engine_deposit_late.json")
    assert r["hop0"]["deposit_inference"] == []
    c = cand(r, "Acmex")
    assert c["via"] == "labelled address" and c["min_hops"] == 1


def test_deposit_inference_downstream_on_tron_multihop():
    r = run_scenario("synthetic_engine_tron_multihop.json")
    nv = r["summary"]["nearest_vasp"]
    assert nv["hops"] == 2 and nv["via"] == "inferred deposit address"
    c = cand(r, "Betax")
    f = c["best_factors"]
    assert (f["R"], f["H"], f["V"], f["T"]) == (0.8, 0.85, 1.0, 1.0)  # exclusivity corroborates the sweep
    assert c["confidence"] == pytest.approx(68.0)
    assert r["deposit_inferences"][0]["hop"] == 2
    assert c["paths"][0]["nodes"][-1] == r["deposit_inferences"][0]["deposit_address"]


# ---------------------------------------------------------------- §6.5 Bitcoin clustering
def test_btc_common_input_clustering():
    r = run_scenario("synthetic_engine_btc_cluster.json")
    cl = r["cluster_inferences"][0]
    assert cl["reliability"] == 0.85 and cl["evidence"]["common_input_txs"] == ["syn-b-2"]
    c = cand(r, "Gammax")
    assert c["via"] == "common-input cluster" and c["min_hops"] == 1
    assert c["best_factors"]["V"] == pytest.approx(0.6 / 0.999, rel=1e-4)
    assert c["confidence"] == pytest.approx(round(85 * math.sqrt(0.6 / 0.999), 1), abs=0.1)


def test_coinjoin_is_skipped_and_flagged_peel_chain_detected():
    r = run_scenario("synthetic_engine_peel_coinjoin.json")
    codes = {t["code"]: t for t in r["typologies"]}
    assert "MIXER_EXPOSURE" in codes and "syn-p-cj" in codes["MIXER_EXPOSURE"]["details"]["coinjoin_txs"]
    assert codes["PEEL_CHAIN"]["details"]["steps"] >= 3
    assert {"syn-p-1", "syn-p-2", "syn-p-3"} <= set(codes["PEEL_CHAIN"]["evidence_txs"])
    assert r["cluster_inferences"] == []  # CoinJoin co-inputs never create a cluster


# ---------------------------------------------------------------- §6.3 traversal
def test_terminal_nodes_and_bridge_breakpoint():
    r = run_scenario("synthetic_engine_mixer_bridge.json")
    assert r["summary"]["nearest_vasp"] is None
    roles = {n["address"]: n for n in r["graph"]["nodes"]}
    mixer = next(n for n in roles.values() if n["role"] == "mixer")
    assert mixer["terminal_reason"] == "labelled MIXER" and not mixer["expanded"]
    bp = r["cross_chain"]["breakpoints"][0]
    assert bp["amounts"] == {"ETH": "5"} and bp["tx_hashes"] == ["syn-m-2"]
    assert all(not c["routable"] for c in r["candidates"])


def test_high_activity_unlabelled_service_is_terminal():
    r = run_scenario("synthetic_engine_high_activity.json")
    svc = r["possible_unlabelled_services"][0]
    assert svc["activity"]["counterparties_window"] > 200 and svc["value_share"] == 1.0
    node = next(n for n in r["graph"]["nodes"] if n["address"] == svc["address"])
    assert node["role"] == "high_activity" and node["terminal_reason"].startswith("unlabelled high-activity")


def test_dust_and_zero_value_not_followed():
    r = run_scenario("synthetic_engine_typologies.json")
    followed = {e["target"].split(":")[1] for e in r["graph"]["edges"] if e["kind"] == "flow"}
    assert not any(a.startswith("0x8080ee") for a in followed)  # zero-value poisoning sender
    assert "0x" + "9b" * 20 not in followed
    assert r["address_poisoning_candidates"]


def test_node_budget_marks_partial():
    from app.config import scoring_config

    cfg = scoring_config()["traversal"]
    old = cfg["node_budget"]
    cfg["node_budget"] = 4
    try:
        r = run_scenario("synthetic_engine_typologies.json")
    finally:
        cfg["node_budget"] = old
    assert r["summary"]["partial"] is True
    assert any("node budget" in p for p in r["summary"]["partial_reasons"])


def test_api_budget_marks_partial():
    full = run_scenario("synthetic_engine_tron_multihop.json")
    assert full["summary"]["partial"] is False and full["summary"]["api_calls"] >= 3
    r = run_scenario("synthetic_engine_tron_multihop.json", api_call_budget=2)
    assert r["summary"]["partial"] is True
    assert "API call budget exhausted" in r["summary"]["partial_reasons"]
    assert r["summary"]["api_calls"] == 2


def test_max_hops_limits_traversal():
    r = run_scenario("synthetic_engine_tron_multihop.json", max_hops=1)
    assert r["summary"]["nearest_vasp"] is None
    assert max(n["hop"] or 0 for n in r["graph"]["nodes"] if n["direction"] == "forward") == 1


def test_backward_funding_source():
    r = run_scenario("synthetic_engine_funding.json")
    f = r["funding_sources"][0]
    assert f["entity_name"].startswith("Deltax") and f["confidence"] == 95.0 and f["min_hops"] == 1
    r2 = run_scenario("synthetic_engine_funding.json", include_funding=False)
    assert r2["funding_sources"] == []


def test_no_match_is_honest():
    r = run_scenario("synthetic_engine_nomatch.json")
    assert r["candidates"] == [] and r["summary"]["nearest_vasp"] is None
    assert r["risk"]["level"] == "Low"
    assert "No VASP found" in r["summary"]["recommended_action"]


# ---------------------------------------------------------------- §6.6 scoring
def test_multiple_paths_combine():
    r = run_scenario("synthetic_engine_two_paths.json")
    c = cand(r, "Epsilonx")
    scores = sorted(p["score"] for p in c["paths"])
    expected = 1 - (1 - scores[0]) * (1 - scores[1])
    assert c["confidence"] == pytest.approx(round(expected * 100, 1))
    assert {p["factors"]["T"] for p in c["paths"]} == {0.8, 0.6}  # 5-day and 40-day gaps
    p0 = max(c["paths"], key=lambda p: p["score"])
    assert p0["score"] == pytest.approx(0.95 * 0.85 * math.sqrt(0.5) * 0.8, rel=1e-4)
    assert c["raise_note"]


def test_scoring_primitives():
    assert [hop_factor(h) for h in range(6)] == [1.0, 1.0, 0.85, 0.70, 0.55, 0.40]
    assert timing_factor([0])[0] == 1.0
    assert timing_factor([0, 72 * 3600])[0] == 1.0
    assert timing_factor([0, 72 * 3600 + 1])[0] == 0.8
    assert timing_factor([0, 30 * 86400 + 1])[0] == 0.6
    assert [tier_for(x) for x in (75, 74.9, 50, 49.9, 25, 24.9)] == ["High", "Medium", "Medium", "Low", "Low", "Weak"]


def _c(name, conf, hops, vasp=True):
    return {"entity_name": name, "confidence": conf, "min_hops": hops, "is_vasp": vasp}


def test_nearest_vasp_rule():
    assert nearest_vasp([_c("far", 95, 3), _c("near", 60, 1)])["entity_name"] == "near"
    assert nearest_vasp([_c("a", 60, 1), _c("b", 70, 1)])["entity_name"] == "b"  # tie -> higher confidence
    assert nearest_vasp([_c("weak", 49.9, 0), _c("mixer", 99, 0, vasp=False)]) is None


def test_combine_weak_candidates_not_routable():
    from app.labels.store import LabelHit

    hit = LabelHit(None, "ethereum", "0x" + "1" * 40, 1, "X", "CEX", True, "HOT", "s", None, 0.5, "D", None, {})
    path = {"score": 0.2, "hops": 3, "factors": {"R": 0.5, "H": 0.7, "V": 0.3, "T": 1.0}, "label": {"tier": "D", "source": "s"}, "via": "labelled address"}
    out = combine({1: [path]}, {1: hit}, partial=False)
    assert out[0]["tier"] == "Weak" and out[0]["routable"] is False


# ---------------------------------------------------------------- §6.7 typologies
def test_typologies_detected_with_evidence():
    r = run_scenario("synthetic_engine_typologies.json")
    codes = {t["code"]: t for t in r["typologies"]}
    assert {"FAN_OUT", "FAN_IN", "STRUCTURING", "RAPID_PASS_THROUGH", "ADDRESS_POISONING"} <= set(codes)
    assert codes["ADDRESS_POISONING"]["evidence_txs"] == ["syn-f-poison"]
    look = codes["ADDRESS_POISONING"]["details"]["lookalikes"][0]
    assert look["imitates"] == "0x" + "80" * 20
    assert codes["STRUCTURING"]["details"]["count"] >= 5
    assert "syn-f-rapid" in codes["RAPID_PASS_THROUGH"]["evidence_txs"]
    for t in r["typologies"]:
        assert t["evidence_txs"], t["code"]


# ---------------------------------------------------------------- §6.8 risk
def test_risk_levels():
    assert run_scenario("synthetic_engine_sanctioned.json")["risk"]["level"] == "Severe"
    high = run_scenario("synthetic_engine_mixer_bridge.json")["risk"]
    assert high["level"] == "High" and "MIXER" in high["reasons"][0]["reason"]
    med = run_scenario("synthetic_engine_typologies.json")["risk"]
    assert med["level"] == "Medium" and "typologies" in med["reasons"][0]["reason"]
    low = run_scenario("synthetic_engine_nomatch.json")["risk"]
    assert low["level"] == "Low" and low["reasons"]


# ---------------------------------------------------------------- graph / output
def test_graph_and_evidence_paths_trace_to_tx_hashes():
    r = run_scenario("synthetic_engine_tron_multihop.json")
    path = cand(r, "Betax")["paths"][0]
    for hop in path["hops_detail"]:
        assert hop["transfers"] and all(t["tx_hash"] and t["time_ist"].endswith("IST") for t in hop["transfers"])
        assert hop["transfers"][0]["explorer_url"].startswith("https://tronscan.org/")
    from app.engine.graph_builder import to_cytoscape

    els = to_cytoscape(r["graph"])["elements"]
    assert any(e["group"] == "nodes" and e["data"]["role"] == "suspect" for e in els)
    assert any(e["group"] == "nodes" and e["data"]["role"] == "deposit_inferred" for e in els)
    assert all(e["data"]["width"] >= 1 for e in els if e["group"] == "edges")
