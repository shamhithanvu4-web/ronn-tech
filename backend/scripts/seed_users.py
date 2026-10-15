"""Create (or reset) the admin, supervisor and analyst accounts from backend/.env.

Requires SEED_ADMIN_PASSWORD, SEED_SUPERVISOR_PASSWORD and SEED_ANALYST_PASSWORD.
Also loads the committed label seed CSV into an empty database.
"""
from __future__ import annotations

import sys

import _bootstrap  # noqa: F401

from sqlalchemy import select

from app.audit import record
from app.config import SEED_CSV, get_settings
from app.db import init_db, session_scope
from app.labels.store import label_counts, load_seed_csv
from app.models import User
from app.security import hash_password

ACCOUNTS = [
    ("admin", "ADMIN", "SEED_ADMIN_PASSWORD", "Administrator"),
    ("supervisor", "SUPERVISOR", "SEED_SUPERVISOR_PASSWORD", "Supervising Officer"),
    ("analyst", "ANALYST", "SEED_ANALYST_PASSWORD", "Investigating Analyst"),
]
MIN_LENGTH = 10


def main() -> int:
    settings = get_settings()
    missing = [env for _u, _r, env, _n in ACCOUNTS if not getattr(settings, env)]
    if missing:
        print(
            "ERROR: missing password(s) in backend/.env: " + ", ".join(missing) + "\n"
            "Copy backend/.env.example to backend/.env and set each password (at least "
            f"{MIN_LENGTH} characters), then run this script again.",
            file=sys.stderr,
        )
        return 1
    weak = [env for _u, _r, env, _n in ACCOUNTS if len(getattr(settings, env)) < MIN_LENGTH]
    if weak:
        print(f"ERROR: password(s) shorter than {MIN_LENGTH} characters: {', '.join(weak)}", file=sys.stderr)
        return 1
    init_db()
    with session_scope() as db:
        for username, role, env, full_name in ACCOUNTS:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                user = User(username=username, role=role, full_name=full_name, password_hash="")
                db.add(user)
                action = "created"
            else:
                action = "updated"
            user.role = role
            user.active = True
            user.password_hash = hash_password(getattr(settings, env))
            db.flush()
            record(db, "system", "user_seeded", "user", user.id, {"username": username, "role": role}, commit=False)
            print(f"{action} {role.lower():10s} account '{username}'")
        if not label_counts(db) and SEED_CSV.exists():
            counts = load_seed_csv(db, SEED_CSV)
            print(f"Loaded label seed: {sum(counts.values())} labels {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
