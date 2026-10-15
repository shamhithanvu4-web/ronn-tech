"""Per-provider token-bucket rate limiter.

Loop-agnostic (works across threads and event loops): the bucket state is
guarded by a threading lock and waiting happens with asyncio.sleep outside it.
"""
from __future__ import annotations

import asyncio
import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else max(1.0, rate_per_sec))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    def try_acquire(self) -> float:
        """Take a token if available. Returns 0.0 on success, else seconds to wait."""
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            return (1.0 - self._tokens) / self.rate

    async def acquire(self) -> None:
        while True:
            wait = self.try_acquire()
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 5.0))


_buckets: dict[str, TokenBucket] = {}
_buckets_lock = threading.Lock()


def bucket_for(name: str, rate_per_sec: float) -> TokenBucket:
    with _buckets_lock:
        bucket = _buckets.get(name)
        if bucket is None:
            bucket = TokenBucket(rate_per_sec)
            _buckets[name] = bucket
        return bucket
