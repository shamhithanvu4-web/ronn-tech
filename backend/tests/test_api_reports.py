"""M3: analysis, graph, report and verify endpoints; evidence hashing; tamper detection."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.conftest import auth
from tests.world import install_world, remove_world, wait_done


@pytest.fixture()
def done_analysis(client, tokens, fixtures_dir, evidence_dir, data_mode, monkeypatch):
    install_world("synthetic_engine_deposit_hop0.json", monkeypatch, data_mode)
    h = auth(tokens["analyst"])
    from tests.helpers import load

    suspect = load("synthetic_engine_deposit_hop0.json")["suspect"]
    r = client.post("/api/analyses", json={"address": suspect, "chain": "ethereum"}, headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    body = wait_done(client, h, r.json()["id"])
    yield body
    remove_world()


def test_analysis_result_and_hash(client, tokens, done_analysis):
    a = done_analysis
    assert a["status"] == "done", a
    nv = a["result"]["summary"]["nearest_vasp"]
    assert nv["entity_name"].startswith("Acmex") and nv["confidence"] == 85.0 and nv["tier"] == "High" and nv["hops"] == 0
    assert a["result_hash_valid"] is True and len(a["result_sha256"]) == 64
    assert "graph" not in a["result"] and a["result"]["graph_stats"]["nodes"] >= 2
    ent = a["entities"][str(nv["entity_id"])]
    assert ent["verification_status"] == "unverified" and ent["sahyog_onboarded"] is None
    # Every evidence path hop traces to a transaction hash.
    for c in a["result"]["candidates"]:
        for p in c["paths"]:
            for hop in p["hops_detail"]:
                assert all(t["tx_hash"] for t in hop["transfers"])
    lst = client.get("/api/analyses?limit=5", headers=auth(tokens["analyst"])).json()["items"]
    assert any(x["id"] == a["id"] for x in lst)


def test_graph_is_cytoscape(client, tokens, done_analysis):
    r = client.get(f"/api/analyses/{done_analysis['id']}/graph", headers=auth(tokens["analyst"]))
    assert r.status_code == 200
    els = r.json()["elements"]
    nodes = [e for e in els if e["group"] == "nodes"]
    edges = [e for e in els if e["group"] == "edges"]
    assert any(n["data"]["role"] == "suspect" for n in nodes)
    suspect = next(n for n in nodes if n["data"]["role"] == "suspect")
    assert suspect["data"]["inferred"] == "deposit" and "inferred_deposit" in suspect["classes"]
    assert any(e["data"]["kind"] == "sweep" for e in edges)
    assert edges and all(e["data"]["width"] >= 1 for e in edges)


def test_pdf_report_verifies_and_one_byte_change_fails(client, tokens, done_analysis):
    h = auth(tokens["analyst"])
    r = client.get(f"/api/analyses/{done_analysis['id']}/report.pdf", headers=h)
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    digest = r.headers["X-Report-SHA256"]
    pdf = r.content
    if os.environ.get("RONN_DUMP_REPORTS"):  # manual inspection: write the PDF out
        Path(os.environ["RONN_DUMP_REPORTS"], "analysis-report.pdf").write_bytes(pdf)
    assert digest.encode() in pdf  # embedded in metadata

    ok = client.post("/api/reports/verify", files={"file": ("r.pdf", pdf, "application/pdf")}, headers=h).json()
    assert ok["status"] == "verified" and ok["valid"] is True and ok["embedded_content_sha256"] == digest

    i = len(pdf) // 2
    tampered = pdf[:i] + bytes([pdf[i] ^ 0x01]) + pdf[i + 1 :]
    bad = client.post("/api/reports/verify", files={"file": ("r.pdf", tampered, "application/pdf")}, headers=h).json()
    assert bad["valid"] is False and bad["status"] == "tampered"

    j = pdf.find(digest.encode())
    tampered2 = pdf[:j] + (b"0" if pdf[j : j + 1] != b"0" else b"1") + pdf[j + 1 :]
    bad2 = client.post("/api/reports/verify", files={"file": ("r.pdf", tampered2, "application/pdf")}, headers=h).json()
    assert bad2["valid"] is False


def test_json_report_verifies_and_tampering_fails(client, tokens, done_analysis):
    h = auth(tokens["analyst"])
    r = client.get(f"/api/analyses/{done_analysis['id']}/report.json", headers=h)
    assert r.status_code == 200
    raw = r.content
    doc = json.loads(raw)
    assert doc["integrity"]["content_sha256"] == r.headers["X-Report-SHA256"]
    assert doc["integrity"]["analysis_sha256"] == done_analysis["result_sha256"]
    assert doc["integrity"]["raw_responses"] and all(len(x["sha256"]) == 64 for x in doc["integrity"]["raw_responses"])
    assert "not proof of ownership" in doc["report"]["disclaimer"]
    assert doc["report"]["certificate"]["note"] == "Legal provisions to be confirmed by the investigating officer."

    ok = client.post("/api/reports/verify", files={"file": ("r.json", raw, "application/json")}, headers=h).json()
    assert ok["status"] == "verified" and ok["valid"] is True

    # One byte changed anywhere fails verification.
    k = raw.find(b'"attribution_confidence": 85.0') + len(b'"attribution_confidence": ')
    one = raw[:k] + b"9" + raw[k + 1 :]
    bad = client.post("/api/reports/verify", files={"file": ("r.json", one, "application/json")}, headers=h).json()
    assert bad["valid"] is False and bad["status"] == "tampered"
    ws = raw.replace(b"\n", b"\r\n", 1)
    re = client.post("/api/reports/verify", files={"file": ("r.json", ws, "application/json")}, headers=h).json()
    assert re["valid"] is False and re["status"] == "reformatted"


def test_verify_rejects_garbage_and_unknown(client, tokens):
    h = auth(tokens["analyst"])
    r = client.post("/api/reports/verify", files={"file": ("x.txt", b"hello", "text/plain")}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "unrecognised_report"
    r = client.post("/api/reports/verify", files={"file": ("x.pdf", b"", "application/pdf")}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "empty_upload"
    r = client.post("/api/reports/verify", files={"file": ("x.pdf", b"%PDF-1.4 nothing", "application/pdf")}, headers=h)
    assert r.json()["status"] == "unknown" and r.json()["valid"] is False


def test_invalid_address_is_400(client, tokens):
    r = client.post("/api/analyses", json={"address": "0x1234"}, headers=auth(tokens["analyst"]))
    assert r.status_code == 400 and r.json()["code"] == "invalid_address" and r.json()["detail"]


def test_unknown_and_unfinished_analysis(client, tokens):
    h = auth(tokens["analyst"])
    assert client.get("/api/analyses/does-not-exist", headers=h).status_code == 404
    from app.engine import jobs

    aid = jobs.create_analysis("0x" + "22" * 20, "analyst", chain="ethereum")  # never submitted
    r = client.get(f"/api/analyses/{aid}/report.pdf", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "analysis_not_done"


def test_endpoints_require_auth(client):
    assert client.post("/api/analyses", json={"address": "x"}).status_code == 401
    assert client.post("/api/reports/verify", files={"file": ("a", b"x")}).status_code == 401


def test_audit_chain_records_reports(client, tokens, done_analysis):
    h = auth(tokens["admin"])
    client.get(f"/api/analyses/{done_analysis['id']}/report.json", headers=h)
    v = client.get("/api/audit/verify", headers=h).json()
    assert v["valid"] is True
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import AuditLog

    with session_scope() as db:
        actions = set(db.execute(select(AuditLog.action)).scalars())
    assert {"analysis_requested", "analysis_completed", "report_generated_json", "report_downloaded"} <= actions
