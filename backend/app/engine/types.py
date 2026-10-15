"""Engine data structures shared by traversal, inference, scoring and typologies."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from app.labels.store import LabelHit
from app.providers.base import RawTx, Transfer

NodeKey = tuple[str, str]  # (chain, address)


def node_id(chain: str, address: str) -> str:
    return f"{chain}:{address}"


class DataSource(Protocol):
    async def get_transfers(
        self, chain: str, address: str, since: int | None, until: int | None, limit: int, start_block: int | None = None
    ) -> list[Transfer]: ...

    async def get_raw_txs(self, chain: str, address: str, since: int | None, until: int | None, limit: int) -> list[RawTx]: ...

    async def get_address_stats(self, chain: str, address: str) -> dict[str, Any] | None: ...


class LabelLookup(Protocol):
    def lookup(self, chain: str, address: str) -> list[LabelHit]: ...

    def remember(self, hit: LabelHit) -> None: ...

    async def live_lookup(self, chain: str, address: str) -> LabelHit | None: ...


@dataclass
class DepositInference:
    chain: str
    deposit_address: str
    entity_id: int
    entity_name: str
    entity_type: str
    hot_wallets: list[str]
    reliability: float
    corroborations: list[str]
    deposit_txs: list[str]
    sweep_txs: list[str]
    gas_topup_txs: list[str]
    swept_amount: str
    deposited_amount: str
    asset: str
    linked_wallets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "deposit_address": self.deposit_address,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "hot_wallets": self.hot_wallets,
            "reliability": self.reliability,
            "corroborations": self.corroborations,
            "evidence": {
                "deposit_txs": self.deposit_txs,
                "sweep_txs": self.sweep_txs,
                "gas_topup_txs": self.gas_topup_txs,
            },
            "asset": self.asset,
            "deposited_amount": self.deposited_amount,
            "swept_amount": self.swept_amount,
            "linked_wallets": self.linked_wallets,
        }


@dataclass
class ClusterInference:
    chain: str
    address: str
    entity_id: int
    entity_name: str
    entity_type: str
    reliability: float
    co_input_addresses: list[str]
    evidence_txs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "address": self.address,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "reliability": self.reliability,
            "co_input_addresses": self.co_input_addresses,
            "evidence": {"common_input_txs": self.evidence_txs},
        }


@dataclass
class NodeInfo:
    chain: str
    address: str
    hop: int
    direction: str  # suspect | forward | backward
    entry_time: int | None = None
    entry_block: int | None = None
    entry_txs: list[str] = field(default_factory=list)
    labels: list[LabelHit] = field(default_factory=list)
    attribution: LabelHit | None = None  # the hit used for scoring
    fetched: bool = False
    expanded: bool = False
    terminal_reason: str | None = None
    high_activity: bool = False
    activity: dict[str, Any] = field(default_factory=dict)
    transfers: list[Transfer] = field(default_factory=list)
    raw_txs: list[RawTx] = field(default_factory=list)
    deposit: DepositInference | None = None
    cluster: ClusterInference | None = None
    fetch_error: str | None = None
    history_truncated: bool = False

    @property
    def key(self) -> NodeKey:
        return (self.chain, self.address)

    @property
    def id(self) -> str:
        return node_id(self.chain, self.address)


@dataclass
class ChainCheck:
    chain: str
    status: str  # active | inactive | requires_paid_plan | key_missing | error | disabled
    detail: str | None = None
    transfers: int = 0
    first_seen: int | None = None
    last_seen: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "status": self.status,
            "detail": self.detail,
            "transfers": self.transfers,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


def value_of(t: Transfer) -> Decimal | None:
    return t.usd_dec


def dec_str(d: Decimal | None, q: str = "0.00000001") -> str | None:
    if d is None:
        return None
    return format(d.quantize(Decimal(q)).normalize(), "f")
