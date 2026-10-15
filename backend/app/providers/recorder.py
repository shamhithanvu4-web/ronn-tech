"""Raw-response recording: canonical request keys, fixtures (record/replay) and
per-analysis evidence files, each with SHA-256 and retrieval time (SPEC §4, §14).

API keys are never written anywhere: secret query parameters are removed from
the canonical URL before it is hashed or stored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.config import EVIDENCE_DIR, FIXTURES_DIR
from app.db import iso, utcnow
from app.providers.context import current
from app.util import canonical_json, sha256_hex

SECRET_PARAMS = {"apikey", "api-key", "api_key", "x-api-key", "key"}


@dataclass
class RecordedResponse:
    url: str
    method: str
    request_body: str | None
    status: int
    body: str
    content_sha256: str
    retrieved_at: str  # ISO-8601 UTC

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "request_body": self.request_body,
            "status": self.status,
            "retrieved_at": self.retrieved_at,
            "content_sha256": self.content_sha256,
            "body": self.body,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RecordedResponse":
        body = data["body"]
        return cls(
            url=data["url"],
            method=data.get("method", "GET"),
            request_body=data.get("request_body"),
            status=int(data.get("status", 200)),
            body=body,
            content_sha256=data.get("content_sha256") or sha256_hex(body),
            retrieved_at=data.get("retrieved_at") or "",
        )


def canonical_url(url: str, params: dict[str, Any] | None) -> str:
    """URL with sorted, secret-free query parameters."""
    clean = {k: v for k, v in (params or {}).items() if v is not None and k.lower() not in SECRET_PARAMS}
    if not clean:
        return url
    query = urlencode(sorted((k, str(v)) for k, v in clean.items()))
    return f"{url}?{query}"


def request_key(method: str, canon_url: str, json_body: Any | None) -> tuple[str, str | None]:
    """Return (sha256 key, canonical request body). GET requests hash the URL alone."""
    body = canonical_json(json_body) if json_body is not None else None
    material = canon_url if (method.upper() == "GET" and body is None) else f"{method.upper()} {canon_url}\n{body or ''}"
    return sha256_hex(material), body


def fixture_path(chain: str, key: str) -> Path:
    return FIXTURES_DIR / chain / f"{key}.json"


def load_fixture(chain: str, key: str) -> RecordedResponse | None:
    path = fixture_path(chain, key)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return RecordedResponse.from_json(json.load(fh))


def save_fixture(chain: str, key: str, rec: RecordedResponse) -> Path:
    path = fixture_path(chain, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(rec.to_json(), fh, ensure_ascii=False, indent=1)
    return path


def make_record(url: str, method: str, request_body: str | None, status: int, body: str) -> RecordedResponse:
    return RecordedResponse(
        url=url,
        method=method.upper(),
        request_body=request_body,
        status=status,
        body=body,
        content_sha256=sha256_hex(body),
        retrieved_at=iso(utcnow()) or "",
    )


def record_evidence(chain: str, provider: str, key: str, rec: RecordedResponse, origin: str) -> None:
    """Attach a raw response to the running analysis (if any) and save it to evidence/."""
    ctx = current()
    if ctx is None or ctx.analysis_id is None:
        return
    if key in ctx.evidence_keys:
        return
    rel_path = None
    if ctx.evidence_dir is not None:
        ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = ctx.evidence_dir / f"{chain}_{key}.json"
        if not path.exists():
            with path.open("w", encoding="utf-8") as fh:
                json.dump(rec.to_json(), fh, ensure_ascii=False)
        try:
            rel_path = path.relative_to(EVIDENCE_DIR).as_posix()
        except ValueError:
            rel_path = path.as_posix()
    ctx.add_evidence(
        key,
        {
            "key": key,
            "chain": chain,
            "provider": provider,
            "url": rec.url,
            "method": rec.method,
            "content_sha256": rec.content_sha256,
            "retrieved_at": rec.retrieved_at,
            "origin": origin,  # network | cache | fixture
            "path": rel_path,
        },
    )
