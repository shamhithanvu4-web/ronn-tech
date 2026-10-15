"""M5: cases and their state transitions, lawful requests with two-person approval,
the simulated SAHYOG adapter (HMAC-verified inbound, outbox), batch import, watchlist
and alerts, VASP directory, dashboard. Synthetic data only (tests/fixtures/synthetic_*)."""
from __future__ import annotations

import json
import re
import time

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.integrations.sahyog import sign
from app.models import Analysis, Entity, User
from tests.conftest import auth
from tests.helpers import load
from tests.world import install_world, remove_world, wait_done

SECRET = "test-sahyog-shared-secret"


def _wait_case(client, h, case_id, status="REVIEW", timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        c = client.get(f"/api/cases/{case_id}", headers=h).json()
        if c["status"] == status:
            return c
        time.sleep(0.1)
    raise AssertionError(f"case {case_id} never reached {status}: {c['status']}")


@pytest.fixture()
def hop0_world(fixtures_dir, evidence_dir, data_mode, monkeypatch):
    sc = install_world("synthetic_engine_deposit_hop0.json", monkeypatch, data_mode)
    yield sc
    remove_world()


# ----------------------------------------------------------------- cases
def test_case_numbering_and_transitions(client, tokens):
    h = auth(tokens["analyst"])
    r = client.post("/api/cases", json={"title": "Task scam ring", "crime_type": "task scam", "complaint_ref": "NCRP-TEST-1"}, headers=h)
    assert r.status_code == 201, r.text
    c = r.json()
    assert re.fullmatch(r"RT-\d{4}-\d{6}", c["case_no"]) and c["status"] == "NEW"
    c2 = client.post("/api/cases", json={"title": "Second"}, headers=h).json()
    assert int(c2["case_no"][-6:]) == int(c["case_no"][-6:]) + 1
    assert client.post("/api/cases", json={"title": "x", "crime_type": "jaywalking"}, headers=h).status_code == 422

    bad = client.patch(f"/api/cases/{c['id']}", json={"status": "DISPATCHED_SIMULATED"}, headers=h)
    assert bad.status_code == 409 and bad.json()["code"] == "invalid_transition"
    ok = client.patch(f"/api/cases/{c['id']}", json={"status": "REVIEW", "note": "triaged"}, headers=h)
    assert ok.status_code == 200 and ok.json()["status"] == "REVIEW"
    closed = client.patch(f"/api/cases/{c['id']}", json={"status": "CLOSED"}, headers=h).json()
    assert closed["status"] == "CLOSED"
    d = client.get(f"/api/cases/{c['id']}", headers=h).json()
    kinds = [e["kind"] for e in d["timeline"]]
    assert "created" in kinds and kinds.count("status") == 2
    assert d["allowed_status"] == ["REVIEW"]
    assert client.post("/api/analyses", json={"address": "0x" + "33" * 20, "case_id": c["id"]}, headers=h).status_code == 409


# ----------------------------------------------------------------- requests
def test_request_lifecycle_two_person_rule_and_routing(client, tokens, hop0_world):
    ha, hs, hadm = auth(tokens["analyst"]), auth(tokens["supervisor"]), auth(tokens["admin"])
    case = client.post("/api/cases", json={"title": "Investment fraud", "crime_type": "investment fraud", "fir_no": "FIR-1/2026"}, headers=ha).json()
    r = client.post("/api/analyses", json={"address": hop0_world["suspect"], "chain": "ethereum", "case_id": case["id"]}, headers=ha)
    a = wait_done(client, ha, r.json()["id"])
    assert a["status"] == "done"
    d = _wait_case(client, ha, case["id"], "REVIEW")
    assert d["wallets"][0]["analysis_id"] == a["id"]

    # Freeze has no default legal basis; an unknown basis is rejected.
    r = client.post(f"/api/cases/{case['id']}/requests", json={"analysis_id": a["id"], "request_type": "FREEZE_HOLD"}, headers=ha)
    assert r.status_code == 400 and r.json()["code"] == "legal_basis_required"
    r = client.post(f"/api/cases/{case['id']}/requests", json={"analysis_id": a["id"], "request_type": "INFORMATION_DISCLOSURE", "legal_basis_id": "nope"}, headers=ha)
    assert r.status_code == 400 and r.json()["code"] == "invalid_legal_basis"
    # Supervisors approve; they do not draft.
    assert client.post(f"/api/cases/{case['id']}/requests", json={"analysis_id": a["id"], "request_type": "INFORMATION_DISCLOSURE"}, headers=hs).status_code == 403

    r = client.post(f"/api/cases/{case['id']}/requests", json={"analysis_id": a["id"], "request_type": "INFORMATION_DISCLOSURE", "notes": "urgent"}, headers=ha)
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "DRAFT" and req["legal_basis_id"] == "bnss_94"
    assert req["entity_name"].startswith("Acmex") and req["confidence"] == 85.0
    assert req["routing"] == "Manual routing required"  # sahyog_onboarded unknown
    assert req["legal_note"] == "Legal provisions to be confirmed by the investigating officer."
    assert req["payload"]["tx_hashes"]
    assert client.get(f"/api/cases/{case['id']}", headers=ha).json()["status"] == "REQUEST_DRAFTED"

    # Dispatch before approval is refused; the analyst cannot approve.
    assert client.post(f"/api/requests/{req['id']}/dispatch", headers=ha).json()["code"] == "invalid_transition"
    r = client.post(f"/api/requests/{req['id']}/approve", json={}, headers=ha)
    assert r.status_code == 403
    r = client.post(f"/api/requests/{req['id']}/approve", json={"note": "checked"}, headers=hs)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["approved_by"] == "supervisor"
    assert client.post(f"/api/requests/{req['id']}/approve", json={}, headers=auth(tokens["supervisor2"])).status_code == 409
    assert client.get(f"/api/cases/{case['id']}", headers=ha).json()["status"] == "REQUEST_APPROVED"

    r = client.post(f"/api/requests/{req['id']}/dispatch", headers=hs)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "DISPATCHED_SIMULATED"
    assert body["outbox"]["status"].startswith("Queued to SAHYOG (simulated)") and "manual routing required" in body["outbox"]["status"]
    assert body["outbox"]["payload"]["simulated"] is True and len(body["outbox"]["payload_sha256"]) == 64
    ob = client.get("/api/integrations/sahyog/outbox", headers=ha).json()
    assert ob["simulated"] and ob["items"][0]["request_id"] == req["id"]

    pdf = client.get(f"/api/requests/{req['id']}/pdf", headers=ha)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    v = client.post("/api/reports/verify", files={"file": ("q.pdf", pdf.content, "application/pdf")}, headers=ha).json()
    assert v["valid"] and v["record"]["kind"] == "request"

    r = client.post(f"/api/requests/{req['id']}/response", json={"note": "KYC received"}, headers=ha)
    assert r.json()["status"] == "RESPONSE_RECEIVED"
    assert client.get(f"/api/cases/{case['id']}", headers=ha).json()["status"] == "RESPONSE_RECEIVED"

    # ADMIN records SAHYOG onboarding (a source URL is mandatory); new drafts route via SAHYOG.
    eid = req["entity_id"]
    assert client.patch(f"/api/entities/{eid}", json={"sahyog_onboarded": True}, headers=ha).status_code == 403
    r = client.patch(f"/api/entities/{eid}", json={"sahyog_onboarded": True}, headers=hadm)
    assert r.status_code == 400 and r.json()["code"] == "source_url_required"
    r = client.patch(f"/api/entities/{eid}", json={"sahyog_onboarded": True, "verified_source_url": "https://example.invalid/onboarding-notice"}, headers=hadm)
    assert r.status_code == 200 and r.json()["sahyog_onboarded"] is True and r.json()["verification_status"] == "verified"
    r = client.post(f"/api/cases/{case['id']}/requests", json={"analysis_id": a["id"], "request_type": "FREEZE_HOLD", "legal_basis_id": "bnss_106"}, headers=ha)
    assert r.status_code == 201 and r.json()["routing"] == "SAHYOG (simulated)"
    client.patch(f"/api/entities/{eid}", json={"clear": ["sahyog_onboarded"]}, headers=hadm)


def test_same_person_cannot_approve(hop0_world, client, tokens):
    from app.errors import Forbidden
    from app.lawful_requests import service

    ha = auth(tokens["analyst"])
    case = client.post("/api/cases", json={"title": "Two-person rule"}, headers=ha).json()
    aid = client.post("/api/analyses", json={"address": hop0_world["suspect"], "chain": "ethereum", "case_id": case["id"]}, headers=ha).json()["id"]
    wait_done(client, ha, aid)
    with session_scope() as db:
        sup = db.execute(select(User).where(User.username == "supervisor")).scalar_one()
        req = service.draft(db, case["id"], aid, "INFORMATION_DISCLOSURE", None, "", sup)  # service-level draft by a supervisor
        with pytest.raises(Forbidden) as exc:
            service.approve(db, req.id, sup)
        assert exc.value.code == "same_person"
        sup2 = db.execute(select(User).where(User.username == "supervisor2")).scalar_one()
        assert service.approve(db, req.id, sup2).status == "APPROVED"


def test_no_routable_vasp_blocks_request(client, tokens, fixtures_dir, evidence_dir, data_mode, monkeypatch):
    sc = install_world("synthetic_engine_nomatch.json", monkeypatch, data_mode)
    try:
        ha = auth(tokens["analyst"])
        case = client.post("/api/cases", json={"title": "No match"}, headers=ha).json()
        aid = client.post("/api/analyses", json={"address": sc["suspect"], "chain": "ethereum", "case_id": case["id"]}, headers=ha).json()["id"]
        a = wait_done(client, ha, aid)
        assert a["result"]["summary"]["nearest_vasp"] is None
        r = client.post(f"/api/cases/{case['id']}/requests", json={"analysis_id": aid, "request_type": "INFORMATION_DISCLOSURE"}, headers=ha)
        assert r.status_code == 409 and r.json()["code"] == "no_routable_vasp"
    finally:
        remove_world()


# ----------------------------------------------------------------- SAHYOG (simulated)
def test_sahyog_inbound_hmac_and_auto_analysis(client, tokens, hop0_world):
    payload = {
        "sahyog_ref": "SAHYOG-TEST-0001",
        "agency": "Test Cyber Cell",
        "officer": "SI Test",
        "complaint_ref": "NCRP-0001",
        "wallets": [{"address": hop0_world["suspect"], "chain_hint": "ethereum"}, {"address": "0x1234", "chain_hint": "ethereum"}],
    }
    body = json.dumps(payload).encode()
    url = "/api/integrations/sahyog/cases"
    r = client.post(url, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["code"] == "signature_missing"
    r = client.post(url, content=body, headers={"X-Signature": "00" * 32})
    assert r.status_code == 401 and r.json()["code"] == "signature_invalid"
    tampered = body.replace(b"Test Cyber Cell", b"Evil Cyber Cell")
    r = client.post(url, content=tampered, headers={"X-Signature": sign(body, SECRET)})
    assert r.status_code == 401
    r = client.post(url, content=body, headers={"X-Signature": "sha256=" + sign(body, SECRET)})
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["status"] == "accepted" and out["simulated"] is True
    assert len(out["analyses"]) == 1 and out["rejected"][0]["address"] == "0x1234"

    ha = auth(tokens["analyst"])
    wait_done(client, ha, out["analyses"][0]["analysis_id"])
    c = _wait_case(client, ha, out["case_id"], "REVIEW")
    assert c["source"] == "sahyog_simulated" and c["sahyog_ref"] == "SAHYOG-TEST-0001" and c["agency"] == "Test Cyber Cell"
    assert any(e["kind"] == "sahyog_inbound" for e in c["timeline"]) and any(e["kind"] == "wallet_rejected" for e in c["timeline"])
    a = c["analyses"][0]
    assert a["nearest_entity_name"].startswith("Acmex") and a["created_by"] == "sahyog-simulated"

    dup = client.post(url, content=body, headers={"X-Signature": sign(body, SECRET)}).json()
    assert dup["status"] == "duplicate" and dup["case_id"] == out["case_id"]
    bad = b'{"sahyog_ref": "x"}'
    r = client.post(url, content=bad, headers={"X-Signature": sign(bad, SECRET)})
    assert r.status_code == 422


def test_sahyog_inbound_requires_configured_secret(client, monkeypatch):
    from app import config

    monkeypatch.setenv("SAHYOG_SHARED_SECRET", "")
    config.reset_settings_cache()
    try:
        r = client.post("/api/integrations/sahyog/cases", content=b"{}", headers={"X-Signature": "x"})
        assert r.status_code == 503 and r.json()["code"] == "sahyog_not_configured"
    finally:
        monkeypatch.undo()
        config.reset_settings_cache()


# ----------------------------------------------------------------- batch import
def test_batch_import_creates_one_case_with_analyses(client, tokens, hop0_world):
    ha = auth(tokens["analyst"])
    csv_text = (
        "address,chain_hint,complaint_ref\n"
        f"{hop0_world['suspect']},ethereum,NCRP-77\n"
        "not-an-address,,NCRP-77\n"
        f"{hop0_world['suspect']},ethereum,NCRP-77\n"
    )
    r = client.post("/api/cases/import", files={"file": ("wallets.csv", csv_text.encode(), "text/csv")}, data={"title": "Import test", "crime_type": "task scam"}, headers=ha)
    assert r.status_code == 201, r.text
    out = r.json()
    assert len(out["analyses"]) == 1 and len(out["rejected"]) == 2
    assert {x["reason"] for x in out["rejected"]} >= {"duplicate row"}
    for aid in out["analyses"]:
        wait_done(client, ha, aid)
    c = _wait_case(client, ha, out["case_id"], "REVIEW")
    assert c["source"] == "batch_import" and c["complaint_ref"] == "NCRP-77" and c["crime_type"] == "task scam"
    r = client.post("/api/cases/import", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")}, headers=ha)
    assert r.status_code == 400 and r.json()["code"] == "invalid_csv"


# ----------------------------------------------------------------- watchlist and alerts
def test_watchlist_alert_is_urgent_when_funds_reach_a_vasp(client, tokens, fixtures_dir, evidence_dir, data_mode, monkeypatch):
    sc = install_world("synthetic_engine_tron_multihop.json", monkeypatch, data_mode)
    try:
        ha = auth(tokens["analyst"])
        # Analysing first stores the tier-C inferred deposit label the watcher can use.
        aid = client.post("/api/analyses", json={"address": sc["suspect"], "chain": "tron"}, headers=ha).json()["id"]
        wait_done(client, ha, aid)
        r = client.post("/api/watchlist", json={"address": sc["suspect"], "since": 0, "analysis_id": aid}, headers=ha)
        assert r.status_code == 201, r.text
        w = r.json()
        assert w["chain"] == "tron" and w["baseline_time"] == 0
        assert client.post("/api/watchlist", json={"address": sc["suspect"], "chain": "tron"}, headers=ha).json()["id"] == w["id"]

        # The watcher asks newer questions than the analysis did (history after each
        # transfer), so it runs against the synthetic fake API in record mode.
        from app.providers import base
        from tests.fake_apis import FakeAPIs

        data_mode("record")
        monkeypatch.setattr(base, "HTTP_TRANSPORT", FakeAPIs(sc).transport())
        res = client.post(f"/api/watchlist/{w['id']}/check", headers=ha).json()
        assert res["error"] is None, res
        assert res["created_alerts"], res
        alerts = client.get("/api/alerts", headers=ha).json()
        urgent = [a for a in alerts["items"] if a["urgent"]]
        assert urgent and urgent[0]["message"].startswith("Funds moving toward Betax") and urgent[0]["vasp_name"].startswith("Betax")
        assert alerts["unacknowledged_urgent"] >= 1
        # A second poll creates no duplicates.
        assert client.post(f"/api/watchlist/{w['id']}/check", headers=ha).json()["created_alerts"] == []
        acked = client.post(f"/api/alerts/{urgent[0]['id']}/ack", headers=ha).json()
        assert acked["acknowledged"] is True
        assert client.delete(f"/api/watchlist/{w['id']}", headers=ha).json()["active"] is False
        assert all(x["id"] != w["id"] for x in client.get("/api/watchlist", headers=ha).json()["items"])
    finally:
        remove_world()


def test_watch_requires_chain_for_ambiguous_evm(client, tokens):
    r = client.post("/api/watchlist", json={"address": "0x" + "ab" * 20}, headers=auth(tokens["analyst"]))
    assert r.status_code == 400 and r.json()["code"] == "chain_required"


# ----------------------------------------------------------------- directory and labels
def test_entities_and_tier_d_labels(client, tokens):
    ha = auth(tokens["analyst"])
    r = client.get("/api/entities?limit=5", headers=ha).json()
    assert r["total"] > 0 and all(e["is_vasp"] for e in r["items"])
    assert all(e["sahyog_onboarded"] is None or e["verified_source_url"] for e in r["items"])
    addr = "0x" + "c4" * 20
    r = client.post("/api/labels", json={"chain": "ethereum", "address": addr, "entity_name": "Analyst Test Exchange", "entity_type": "CEX", "role": "DEPOSIT", "note": "from seized phone"}, headers=ha)
    assert r.status_code == 201 and r.json()["tier"] == "D" and r.json()["reliability"] == 0.5
    got = client.get(f"/api/labels?address={addr}&chain=ethereum", headers=ha).json()
    assert got["labels"][0]["source"] == "analyst" and got["labels"][0]["tier"] == "D"
    assert client.post("/api/labels", json={"chain": "ethereum", "address": "0x12", "entity_name": "x", "entity_type": "CEX"}, headers=ha).status_code == 400
    assert client.post("/api/labels", json={"chain": "ethereum", "address": addr, "entity_name": "x", "entity_type": "BANK"}, headers=ha).status_code == 422


# ----------------------------------------------------------------- dashboard and audit
def test_dashboard_summary_and_audit_chain(client, tokens, hop0_world):
    ha = auth(tokens["analyst"])
    aid = client.post("/api/analyses", json={"address": hop0_world["suspect"], "chain": "ethereum"}, headers=ha).json()["id"]
    wait_done(client, ha, aid)
    s = client.get("/api/dashboard/summary", headers=ha).json()
    assert set(s["kpis"]) == {
        "open_cases", "analyses_this_week", "high_confidence_attributions", "median_time_to_attribution_seconds", "pending_approvals", "urgent_alerts",
    }
    assert s["kpis"]["analyses_this_week"] >= 1 and s["kpis"]["high_confidence_attributions"] >= 1
    assert any(t["entity_name"].startswith("Acmex") for t in s["top_vasps"])
    assert s["recent_analyses"][0]["id"]
    v = client.get("/api/audit/verify", headers=auth(tokens["admin"])).json()
    assert v["valid"] is True and v["rows_checked"] > 10
    with session_scope() as db:
        assert db.execute(select(Analysis).where(Analysis.id == aid)).scalar_one().status == "done"
        assert db.execute(select(Entity).limit(1)).scalar_one()
