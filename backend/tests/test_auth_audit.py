"""Auth (JWT, bcrypt, roles, rate-limited login), hash-chained audit log, health endpoint."""
from __future__ import annotations

import time

import jwt
import pytest
from sqlalchemy import select

from app.audit import record, verify_chain
from app.config import get_settings
from app.db import session_scope
from app.models import AuditLog
from app.security import hash_password, verify_password
from tests.conftest import auth


def test_password_hashing():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    with pytest.raises(ValueError):
        hash_password("x" * 73)


def test_login_me_and_errors(client, seeded_users):
    r = client.post("/api/auth/login", json={"username": "analyst", "password": seeded_users["analyst"]})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "ANALYST" and body["expires_in"] == 3600
    claims = jwt.decode(body["access_token"], get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == "analyst" and claims["exp"] - claims["iat"] == 3600
    me = client.get("/api/auth/me", headers=auth(body["access_token"]))
    assert me.json()["username"] == "analyst"
    bad = client.post("/api/auth/login", json={"username": "analyst", "password": "nope"})
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_credentials"
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").json()["code"] == "token_missing"
    garbage = client.get("/api/auth/me", headers=auth("not-a-jwt"))
    assert garbage.status_code == 401 and garbage.json()["code"] == "token_invalid"


def test_expired_token_rejected(client):
    now = int(time.time())
    token = jwt.encode({"sub": "analyst", "iat": now - 7200, "exp": now - 3600}, get_settings().jwt_secret, algorithm="HS256")
    r = client.get("/api/auth/me", headers=auth(token))
    assert r.status_code == 401 and r.json()["code"] == "token_expired"


def test_login_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "ratelimited", "password": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "ratelimited", "password": "x"})
    assert r.status_code == 429 and r.json()["code"] == "login_rate_limited"


def test_health_lists_every_provider(client, tokens):
    r = client.get("/api/health", headers=auth(tokens["analyst"]))
    assert r.status_code == 200
    data = r.json()
    chains = {p["chain"]: p for p in data["providers"]}
    assert {"bitcoin", "ethereum", "polygon", "arbitrum", "bsc", "tron", "solana"} <= set(chains)
    assert chains["bsc"]["plan"] == "requires paid plan"
    assert data["data_mode"] == "replay"
    assert client.get("/api/health").status_code == 401


def test_audit_chain_detects_tampering(client, tokens):
    with session_scope() as db:
        record(db, "tester", "unit_test", "x", "1", {"n": 1})
        record(db, "tester", "unit_test", "x", "2", {"n": 2})
    r = client.get("/api/audit/verify", headers=auth(tokens["supervisor"]))
    assert r.json()["valid"] is True and r.json()["rows_checked"] >= 2
    with session_scope() as db:
        row = db.execute(select(AuditLog).where(AuditLog.object_id == "1", AuditLog.action == "unit_test")).scalar_one()
        original = dict(row.details)
        row.details = {"n": 999}
    with session_scope() as db:
        result = verify_chain(db)
        assert result["valid"] is False and "modified" in result["reason"]
        row = db.execute(select(AuditLog).where(AuditLog.object_id == "1", AuditLog.action == "unit_test")).scalar_one()
        row.details = original
    with session_scope() as db:
        assert verify_chain(db)["valid"] is True


def test_logins_are_audited(client, seeded_users):
    client.post("/api/auth/login", json={"username": "supervisor", "password": seeded_users["supervisor"]})
    with session_scope() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action == "login", AuditLog.actor == "supervisor")).scalars().all()
        assert rows


def test_error_shape_for_unknown_route(client, tokens):
    r = client.get("/api/does-not-exist", headers=auth(tokens["analyst"]))
    assert r.status_code == 404 and set(r.json()) == {"detail", "code"}
