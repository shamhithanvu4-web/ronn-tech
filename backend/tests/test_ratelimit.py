from __future__ import annotations

import asyncio
import time

import pytest

from app.providers.ratelimit import TokenBucket


def test_bucket_allows_burst_then_throttles():
    b = TokenBucket(rate_per_sec=2)
    assert b.try_acquire() == 0.0
    assert b.try_acquire() == 0.0
    wait = b.try_acquire()
    assert 0 < wait <= 0.5


@pytest.mark.asyncio
async def test_bucket_paces_requests():
    b = TokenBucket(rate_per_sec=20, capacity=1)
    start = time.monotonic()
    for _ in range(5):
        await b.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 4 / 20 * 0.8


def test_bucket_rejects_bad_rate():
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_bucket_is_loop_agnostic():
    b = TokenBucket(rate_per_sec=100)
    asyncio.run(b.acquire())
    asyncio.run(b.acquire())
