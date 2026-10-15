"""Per-analysis execution context (budget counters, evidence list, hidden labels).

Stored in a ContextVar so every provider call made while an analysis runs is
attributed to it, including calls made from concurrently gathered coroutines.
"""
from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from app.errors import AppError


class BudgetExceeded(AppError):
    status_code = 200
    code = "budget_exhausted"


@dataclass
class AnalysisContext:
    analysis_id: str | None = None
    api_call_budget: int = 600
    api_calls: int = 0
    evidence_dir: Path | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_keys: set[str] = field(default_factory=set)
    hidden_label_addresses: set[tuple[str, str]] = field(default_factory=set)
    use_inferred_labels: bool = True
    errors: list[dict[str, Any]] = field(default_factory=list)
    memo: dict[str, Any] = field(default_factory=dict)
    # Watchlist polls must see fresh data, so they skip the 10-minute response cache.
    bypass_cache: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def charge_call(self) -> None:
        with self._lock:
            if self.api_calls >= self.api_call_budget:
                raise BudgetExceeded(
                    f"API call budget of {self.api_call_budget} reached", code="budget_exhausted"
                )
            self.api_calls += 1

    @property
    def budget_left(self) -> int:
        return max(0, self.api_call_budget - self.api_calls)

    def add_evidence(self, key: str, item: dict[str, Any]) -> None:
        with self._lock:
            if key in self.evidence_keys:
                return
            self.evidence_keys.add(key)
            self.evidence.append(item)


_current: contextvars.ContextVar[AnalysisContext | None] = contextvars.ContextVar("analysis_ctx", default=None)


def current() -> AnalysisContext | None:
    return _current.get()


@contextmanager
def use_context(ctx: AnalysisContext) -> Iterator[AnalysisContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
