"""Test configuration: isolated SQLite database, replay mode, fixed secrets.

Environment variables set here take precedence over any developer backend/.env.
Synthetic data used by unit tests lives only in tests/fixtures/synthetic_*.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix="ronntech-tests-"))
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
        "DATA_MODE": "replay",
        "JWT_SECRET": "test-jwt-secret-not-for-production-0123456789",
        "SAHYOG_SHARED_SECRET": "test-sahyog-shared-secret",
        "SEED_ADMIN_PASSWORD": "admin-pass-12345",
        "SEED_SUPERVISOR_PASSWORD": "supervisor-pass-12345",
        "SEED_ANALYST_PASSWORD": "analyst-pass-12345",
        "ENABLE_BACKGROUND_TASKS": "false",
        "ETHERSCAN_API_KEY": "",
        "TRONGRID_API_KEY": "",
        "TRONSCAN_API_KEY": "",
        "HELIUS_API_KEY": "",
        "CHAINALYSIS_API_KEY": "",
        "FRONTEND_ORIGIN": "http://localhost:3000",
    }
)

import pytest  # noqa: E402

from app import config  # noqa: E402

config.reset_settings_cache()

from app.db import configure_engine, init_db  # noqa: E402

configure_engine(os.environ["DATABASE_URL"])
init_db()

FIXTURES = BACKEND / "tests" / "fixtures"


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture()
def fixtures_dir(tmp_path, monkeypatch) -> Path:
    """A private replay-fixtures directory for one test."""
    from app.providers import recorder

    d = tmp_path / "fixtures"
    d.mkdir()
    monkeypatch.setattr(recorder, "FIXTURES_DIR", d)
    return d


@pytest.fixture()
def evidence_dir(tmp_path, monkeypatch) -> Path:
    from app.providers import recorder

    d = tmp_path / "evidence"
    d.mkdir()
    monkeypatch.setattr(recorder, "EVIDENCE_DIR", d)
    return d


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    from app.providers import cache, prices
    from app.providers.base import reset_runtime_status
    from app.security import login_limiter

    reset_runtime_status()
    prices.clear_memo()
    login_limiter.reset()
    cache.clear()
    yield


@pytest.fixture()
def data_mode(monkeypatch):
    """Switch DATA_MODE for one test: data_mode('record')."""

    def _set(mode: str) -> None:
        monkeypatch.setenv("DATA_MODE", mode)
        config.reset_settings_cache()

    yield _set
    config.reset_settings_cache()


@pytest.fixture(scope="session")
def seeded_users():
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import User
    from app.security import hash_password

    accounts = [
        ("admin", "ADMIN", os.environ["SEED_ADMIN_PASSWORD"]),
        ("supervisor", "SUPERVISOR", os.environ["SEED_SUPERVISOR_PASSWORD"]),
        ("analyst", "ANALYST", os.environ["SEED_ANALYST_PASSWORD"]),
        ("analyst2", "ANALYST", "analyst2-pass-12345"),
        ("supervisor2", "SUPERVISOR", "supervisor2-pass-12345"),
    ]
    with session_scope() as db:
        for username, role, pw in accounts:
            if db.execute(select(User).where(User.username == username)).scalar_one_or_none() is None:
                db.add(User(username=username, role=role, full_name=username.title(), password_hash=hash_password(pw)))
    return {u: pw for u, _r, pw in accounts}


@pytest.fixture(scope="session")
def client(seeded_users):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def tokens(client, seeded_users):
    out = {}
    for username, pw in seeded_users.items():
        r = client.post("/api/auth/login", json={"username": username, "password": pw})
        assert r.status_code == 200, r.text
        out[username] = r.json()["access_token"]
    from app.security import login_limiter

    login_limiter.reset()
    return out


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TMP, ignore_errors=True)
