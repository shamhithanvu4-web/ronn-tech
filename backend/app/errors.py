"""Uniform API errors: every error body is {"detail": "...", "code": "..."} (SPEC §11)."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, detail: str, code: str | None = None, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class InvalidAddress(AppError):
    status_code = 400
    code = "invalid_address"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ProviderError(AppError):
    """Upstream data provider failed (HTTP 502)."""

    status_code = 502
    code = "upstream_provider_error"


class PlanRequired(AppError):
    """The chain needs a paid provider plan (HTTP 503)."""

    status_code = 503
    code = "chain_requires_paid_plan"


class ReplayMiss(ProviderError):
    """DATA_MODE=replay and no recorded fixture exists for the request."""

    code = "replay_fixture_missing"


class KeyMissing(AppError):
    status_code = 503
    code = "provider_key_missing"
