"""Helpers to install synthetic replay fixtures (tests only)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.providers.recorder import canonical_url, make_record, request_key, save_fixture

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def install(chain: str, url: str, params: dict[str, Any] | None, body: Any, method: str = "GET", json_body: Any = None) -> str:
    """Write one synthetic fixture exactly where the replay layer will look for it."""
    canon = canonical_url(url, params)
    key, canon_body = request_key(method, canon, json_body)
    text = body if isinstance(body, str) else json.dumps(body)
    save_fixture(chain, key, make_record(canon, method, canon_body, 200, text))
    return key


def install_set(name: str) -> int:
    """Install every fixture in tests/fixtures/<name>.json (a list of fixture specs)."""
    specs = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    for spec in specs:
        install(spec["chain"], spec["url"], spec.get("params"), spec["body"], spec.get("method", "GET"), spec.get("json_body"))
    return len(specs)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
