"""Request bodies for the REST API (Pydantic v2). Responses are plain dicts built by
the routers so every field stays traceable to the stored record."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.cases import CASE_STATUSES, CRIME_TYPES
from app.models.labels import ENTITY_TYPES, LABEL_ROLES
from app.models.requests import REQUEST_TYPES


class AnalysisIn(BaseModel):
    address: str = Field(min_length=1, max_length=128)
    chain: str | None = Field(default=None, max_length=24)
    case_id: int | None = None
    max_hops: int | None = Field(default=None, ge=1, le=5)
    include_funding: bool = True
    window_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("chain")
    @classmethod
    def _blank_chain(cls, v: str | None) -> str | None:
        v = (v or "").strip().lower()
        return v or None


class CaseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    complaint_ref: str | None = Field(default=None, max_length=64)
    fir_no: str | None = Field(default=None, max_length=64)
    agency: str | None = Field(default=None, max_length=128)
    assigned_officer: str | None = Field(default=None, max_length=128)
    crime_type: str = "other"
    window_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("crime_type")
    @classmethod
    def _crime(cls, v: str) -> str:
        v = (v or "other").strip().lower()
        if v not in CRIME_TYPES:
            raise ValueError(f"crime_type must be one of: {', '.join(CRIME_TYPES)}")
        return v


class CasePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    complaint_ref: str | None = Field(default=None, max_length=64)
    fir_no: str | None = Field(default=None, max_length=64)
    agency: str | None = Field(default=None, max_length=128)
    assigned_officer: str | None = Field(default=None, max_length=128)
    crime_type: str | None = None
    status: str | None = None
    window_days: int | None = Field(default=None, ge=1, le=3650)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("crime_type")
    @classmethod
    def _crime(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in CRIME_TYPES:
            raise ValueError(f"crime_type must be one of: {', '.join(CRIME_TYPES)}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if v not in CASE_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(CASE_STATUSES)}")
        return v


class CaseWalletIn(BaseModel):
    address: str = Field(min_length=1, max_length=128)
    chain_hint: str | None = Field(default=None, max_length=24)
    complaint_ref: str | None = Field(default=None, max_length=64)
    analyse: bool = True
    analysis_id: str | None = Field(default=None, max_length=36)


class RequestIn(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=36)
    request_type: Literal["INFORMATION_DISCLOSURE", "FREEZE_HOLD"]
    legal_basis_id: str | None = Field(default=None, max_length=32)
    notes: str = Field(default="", max_length=4000)
    entity_id: int | None = None

    @field_validator("request_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in REQUEST_TYPES:
            raise ValueError(f"request_type must be one of: {', '.join(REQUEST_TYPES)}")
        return v


class RequestAction(BaseModel):
    note: str = Field(default="", max_length=2000)


class EntityPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    jurisdiction: str | None = Field(default=None, max_length=64)
    fiu_ind_registered: bool | None = None
    sahyog_onboarded: bool | None = None
    le_request_channel: str | None = Field(default=None, max_length=2000)
    verification_status: Literal["unverified", "verified", "disputed"] | None = None
    verified_source_url: str | None = Field(default=None, max_length=1000)
    clear: list[Literal["fiu_ind_registered", "sahyog_onboarded", "le_request_channel", "jurisdiction"]] = []

    @field_validator("verified_source_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("verified_source_url must be an http(s) URL")
        return v


class LabelIn(BaseModel):
    chain: str = Field(min_length=1, max_length=24)
    address: str = Field(min_length=1, max_length=128)
    entity_name: str = Field(min_length=1, max_length=160)
    entity_type: str
    role: str = "UNKNOWN"
    source_url: str | None = Field(default=None, max_length=1000)
    note: str = Field(default="", max_length=2000)

    @field_validator("entity_type")
    @classmethod
    def _etype(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of: {', '.join(ENTITY_TYPES)}")
        return v

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        v = (v or "UNKNOWN").strip().upper()
        if v not in LABEL_ROLES:
            raise ValueError(f"role must be one of: {', '.join(LABEL_ROLES)}")
        return v


class WatchIn(BaseModel):
    address: str = Field(min_length=1, max_length=128)
    chain: str | None = Field(default=None, max_length=24)
    case_id: int | None = None
    analysis_id: str | None = Field(default=None, max_length=36)
    note: str = Field(default="", max_length=1000)
    # Unix seconds. Transfers after this time count as "new"; default is now. Set it in
    # the past to alert on movements since, e.g., the date of the complaint.
    since: int | None = Field(default=None, ge=0)
