"""Central configuration for the Ronn Tech backend.

PRODUCT_NAME is the single backend constant for the product name. The frontend
keeps its own copy in frontend/lib/product.js, so the team can rename the
product in exactly two places.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCT_NAME = "Ronn Tech"
METHODOLOGY_VERSION = "1.0.0"

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BACKEND_DIR / "config"
# RONN_FIXTURES_DIR lets evaluation runs and tests point replay at another fixture set.
FIXTURES_DIR = Path(os.environ.get("RONN_FIXTURES_DIR") or (BACKEND_DIR / "fixtures"))
EVIDENCE_DIR = BACKEND_DIR / "evidence"
DATA_DIR = BACKEND_DIR / "data"
SEED_DIR = DATA_DIR / "seed"
SEED_CSV = SEED_DIR / "labels_seed.csv"

DATA_MODES = ("live", "record", "replay")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    ETHERSCAN_API_KEY: str = ""
    TRONGRID_API_KEY: str = ""
    TRONSCAN_API_KEY: str = ""
    HELIUS_API_KEY: str = ""
    CHAINALYSIS_API_KEY: str = ""
    JWT_SECRET: str = ""
    SAHYOG_SHARED_SECRET: str = ""
    DATABASE_URL: str = ""
    DATA_MODE: str = "live"
    SEED_ADMIN_PASSWORD: str = ""
    SEED_SUPERVISOR_PASSWORD: str = ""
    SEED_ANALYST_PASSWORD: str = ""

    # Operational settings (optional; sensible defaults)
    FRONTEND_ORIGIN: str = "http://localhost:3000"
    JWT_TTL_MINUTES: int = 60
    WATCHLIST_INTERVAL_SECONDS: int = 300
    ENABLE_BACKGROUND_TASKS: bool = True
    ANALYSIS_WORKERS: int = 2

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(DATA_DIR / 'ronntech.db').as_posix()}"

    @property
    def data_mode(self) -> str:
        mode = (self.DATA_MODE or "live").strip().lower()
        if mode not in DATA_MODES:
            raise ValueError(f"DATA_MODE must be one of {DATA_MODES}, got {self.DATA_MODE!r}")
        return mode

    @property
    def jwt_secret(self) -> str:
        if self.JWT_SECRET:
            return self.JWT_SECRET
        return _ephemeral_jwt_secret()


@lru_cache(maxsize=1)
def _ephemeral_jwt_secret() -> str:
    warnings.warn(
        "JWT_SECRET is not set in backend/.env; using a random per-process secret. "
        "Sessions will not survive a restart.",
        stacklevel=2,
    )
    return secrets.token_urlsafe(48)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests and scripts that change environment variables at runtime."""
    get_settings.cache_clear()
    load_yaml.cache_clear()


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def config_sha256(name: str) -> str:
    return hashlib.sha256((CONFIG_DIR / name).read_bytes()).hexdigest()


def chains_config() -> dict[str, Any]:
    return load_yaml("chains.yaml")["chains"]


def chain_config(chain: str) -> dict[str, Any]:
    chains = chains_config()
    if chain not in chains:
        raise KeyError(f"Unknown chain {chain!r}")
    return chains[chain]


def evm_chains() -> list[str]:
    return [c for c, cfg in chains_config().items() if cfg.get("family") == "evm"]


def tokens_for(chain: str) -> list[dict[str, Any]]:
    return [t for t in load_yaml("tokens.yaml").get(chain, []) or [] if t.get("contract")]


def scoring_config() -> dict[str, Any]:
    return load_yaml("scoring.yaml")


def explorer_tx_url(chain: str, tx_hash: str) -> str | None:
    try:
        pattern = chain_config(chain).get("explorer_tx")
    except KeyError:
        return None
    return pattern.format(tx=tx_hash) if pattern else None


def explorer_address_url(chain: str, address: str) -> str | None:
    try:
        pattern = chain_config(chain).get("explorer_address")
    except KeyError:
        return None
    return pattern.format(address=address) if pattern else None


def env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")
