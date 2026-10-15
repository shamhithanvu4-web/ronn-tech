"""Provider interface, shared data types and the HTTP fetch pipeline.

fetch_json() applies, in order: replay fixtures (DATA_MODE=replay), the SQLite
cache, the per-analysis API-call budget, the per-provider token bucket, an async
httpx request (15 s timeout, up to 3 retries with backoff on 429/5xx), cache
write, fixture write (DATA_MODE=record) and evidence capture.
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Callable, Protocol, runtime_checkable

import httpx

from app.config import get_settings
from app.db import iso, utcnow
from app.errors import ProviderError, ReplayMiss
from app.providers import cache
from app.providers.context import current
from app.providers.ratelimit import bucket_for
from app.providers.recorder import (
    RecordedResponse,
    canonical_url,
    load_fixture,
    make_record,
    record_evidence,
    request_key,
    save_fixture,
)

TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 3
USER_AGENT = "RonnTech/1.0 (law-enforcement blockchain attribution prototype)"

# Tests may install an httpx.MockTransport here; production leaves it as None.
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None


@dataclass
class Transfer:
    """One value movement (SPEC §4)."""

    chain: str
    tx_hash: str
    block_time: int
    from_addr: str
    to_addr: str
    asset: str
    token_contract: str | None
    amount: str
    usd_estimate: str | None
    is_internal: bool
    raw_ref: str
    block_number: int | None = None
    in_scope: bool = True

    @property
    def amount_dec(self) -> Decimal:
        return Decimal(self.amount)

    @property
    def usd_dec(self) -> Decimal | None:
        return Decimal(self.usd_estimate) if self.usd_estimate is not None else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TxIO:
    address: str | None
    value: str  # native units as decimal string
    index: int = 0


@dataclass
class RawTx:
    chain: str
    tx_id: str
    block_time: int | None
    block_height: int | None
    inputs: list[TxIO] = field(default_factory=list)
    outputs: list[TxIO] = field(default_factory=list)
    fee: str | None = None
    raw_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderStatus:
    chain: str
    provider: str
    available: bool
    status: str  # ok | key_missing | requires_paid_plan | error | disabled | replay
    plan: str
    limits: str
    key_env: str | None
    key_present: bool
    last_error: str | None = None
    last_ok_at: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class ChainProvider(Protocol):
    chain: str

    async def get_transfers(
        self, address: str, since: int | None, until: int | None, limit: int
    ) -> list[Transfer]: ...

    async def get_tx(self, tx_id: str) -> RawTx: ...

    async def status(self) -> ProviderStatus: ...


# ---------------------------------------------------------------- status registry
_status_lock = threading.Lock()
_runtime_status: dict[str, dict[str, Any]] = {}


def note_ok(chain: str) -> None:
    with _status_lock:
        st = _runtime_status.setdefault(chain, {})
        st["last_ok_at"] = iso(utcnow())
        st.pop("plan_required", None)


def note_error(chain: str, message: str, plan_required: bool = False) -> None:
    with _status_lock:
        st = _runtime_status.setdefault(chain, {})
        st["last_error"] = message[:500]
        st["last_error_at"] = iso(utcnow())
        if plan_required:
            st["plan_required"] = True


def runtime_status(chain: str) -> dict[str, Any]:
    with _status_lock:
        return dict(_runtime_status.get(chain, {}))


def reset_runtime_status() -> None:
    with _status_lock:
        _runtime_status.clear()


# ---------------------------------------------------------------- fetch pipeline
@dataclass
class FetchResult:
    data: Any
    record: RecordedResponse
    key: str
    origin: str  # network | cache | fixture

    @property
    def ref(self) -> str:
        return f"sha256:{self.record.content_sha256}"


def _data_mode() -> str:
    return get_settings().data_mode


async def fetch_json(
    *,
    chain: str,
    provider: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json_body: Any | None = None,
    rate_per_sec: float = 2.0,
    limiter_name: str | None = None,
    retry_on: Callable[[Any], bool] | None = None,
    cacheable: Callable[[Any], bool] | None = None,
    ok_statuses: tuple[int, ...] = (200,),
) -> FetchResult:
    canon = canonical_url(url, params)
    key, canon_body = request_key(method, canon, json_body)
    mode = _data_mode()
    ctx = current()

    if mode == "replay":
        rec = load_fixture(chain, key)
        if rec is None:
            raise ReplayMiss(
                f"No recorded fixture for {method} {canon} (chain {chain}). "
                "Record it with DATA_MODE=record or scripts/record_fixtures.py.",
            )
        if ctx is not None:
            ctx.charge_call()
        data = _parse(rec, chain)
        record_evidence(chain, provider, key, rec, "fixture")
        return FetchResult(data=data, record=rec, key=key, origin="fixture")

    cached = None if (ctx is not None and ctx.bypass_cache) else cache.get(key)
    if cached is not None:
        data = _parse(cached, chain)
        record_evidence(chain, provider, key, cached, "cache")
        return FetchResult(data=data, record=cached, key=key, origin="cache")

    if ctx is not None:
        ctx.charge_call()

    bucket = bucket_for(limiter_name or provider, rate_per_sec)
    send_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    send_headers.update(headers or {})
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        await bucket.acquire()
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, follow_redirects=True, transport=HTTP_TRANSPORT
            ) as client:
                resp = await client.request(method, url, params=params, headers=send_headers, json=json_body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(_backoff(attempt))
                continue
            note_error(chain, f"{provider}: {type(exc).__name__}: {exc}")
            raise ProviderError(f"{provider} unreachable for {chain}: {type(exc).__name__}") from exc

        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = ProviderError(f"{provider} HTTP {resp.status_code}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(_backoff(attempt, resp.headers.get("Retry-After")))
                continue
            note_error(chain, f"{provider}: HTTP {resp.status_code}")
            raise ProviderError(f"{provider} returned HTTP {resp.status_code} for {chain} after retries")

        body = resp.text
        rec = make_record(canon, method, canon_body, resp.status_code, body)
        if resp.status_code not in ok_statuses:
            note_error(chain, f"{provider}: HTTP {resp.status_code}: {body[:200]}")
            raise ProviderError(f"{provider} returned HTTP {resp.status_code} for {chain}: {body[:200]}")
        data = _parse(rec, chain)
        if retry_on is not None and retry_on(data) and attempt < MAX_RETRIES:
            last_exc = ProviderError(f"{provider} asked to retry")
            await asyncio.sleep(_backoff(attempt))
            continue

        if cacheable is None or cacheable(data):
            cache.put(key, chain, rec, permanent=False)
        if mode == "record":
            # Semantic errors (e.g. "requires paid plan") are recorded too, so
            # replay reproduces the same behaviour offline.
            save_fixture(chain, key, rec)
        record_evidence(chain, provider, key, rec, "network")
        return FetchResult(data=data, record=rec, key=key, origin="network")

    raise ProviderError(f"{provider} failed for {chain}: {last_exc}")


def _parse(rec: RecordedResponse, chain: str) -> Any:
    try:
        return json.loads(rec.body) if rec.body else None
    except json.JSONDecodeError as exc:
        note_error(chain, f"non-JSON response from {rec.url}")
        raise ProviderError(f"Provider returned non-JSON content for {rec.url}") from exc


def _backoff(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(30.0, float(retry_after))
        except ValueError:
            pass
    return min(30.0, (2**attempt) + random.uniform(0, 0.5))
