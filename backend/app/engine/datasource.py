"""Production data source (providers) and label lookup (DB + live sources) for the engine."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.labels.store import EntityResolver, LabelHit, LabelStore, name_key, persist_runtime_label
from app.models import Entity
from app.providers import make_provider
from app.providers.base import RawTx, Transfer


class ProviderDataSource:
    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._memo: dict[tuple, list[Transfer]] = {}
        self._raw_memo: dict[tuple, list[RawTx]] = {}

    def provider(self, chain: str) -> Any:
        if chain not in self._providers:
            self._providers[chain] = make_provider(chain)
        return self._providers[chain]

    async def get_transfers(
        self, chain: str, address: str, since: int | None, until: int | None, limit: int, start_block: int | None = None
    ) -> list[Transfer]:
        key = (chain, address, since, until, limit, start_block)
        if key not in self._memo:
            self._memo[key] = await self.provider(chain).get_transfers(address, since, until, limit, start_block=start_block)
        return self._memo[key]

    async def get_raw_txs(self, chain: str, address: str, since: int | None, until: int | None, limit: int) -> list[RawTx]:
        prov = self.provider(chain)
        if not hasattr(prov, "get_address_txs"):
            return []
        key = (chain, address, since, until, limit)
        if key not in self._raw_memo:
            self._raw_memo[key] = await prov.get_address_txs(address, since, until, limit)
        return self._raw_memo[key]

    async def get_address_stats(self, chain: str, address: str) -> dict[str, Any] | None:
        prov = self.provider(chain)
        if hasattr(prov, "get_address_stats"):
            return await prov.get_address_stats(address)
        return None


class ProductionLabels:
    """LabelStore + live, cached lookups (Tronscan tags, WalletExplorer, Chainalysis)."""

    LIVE_LIMIT = 12

    def __init__(self, db: Session, enable_live: bool = True):
        self.db = db
        self.store = LabelStore(db)
        self.enable_live = enable_live
        self.live_used = 0

    def lookup(self, chain: str, address: str) -> list[LabelHit]:
        return self.store.lookup(chain, address)

    def remember(self, hit: LabelHit) -> None:
        self.store.remember(hit)

    def _resolve_entity(self, name: str, etype: str | None) -> Entity:
        resolver = EntityResolver(self.db)
        if etype is None:
            matches = sorted(resolver.find_by_name(name), key=lambda e: (not e.is_vasp, e.id))
            if matches:
                return matches[0]
            etype = "UNKNOWN_SERVICE"
        ent = resolver.get(name, etype)
        self.db.commit()
        return ent

    async def live_lookup(self, chain: str, address: str) -> LabelHit | None:
        if not self.enable_live or self.live_used >= self.LIVE_LIMIT:
            return None
        self.live_used += 1
        found: dict[str, Any] | None = None
        if chain == "tron":
            from app.labels.sources import tronscan_tags

            found = await tronscan_tags.lookup(address)
        elif chain == "bitcoin":
            from app.labels.sources import walletexplorer

            found = await walletexplorer.lookup(address)
        if found is None:
            return None
        ent = self._resolve_entity(found["entity_name"], found.get("entity_type"))
        label = persist_runtime_label(
            self.db,
            chain,
            address,
            ent.id,
            found["role"],
            found["source"],
            found["reliability"],
            found["tier"],
            found["evidence"],
            source_url=found.get("source_url"),
        )
        hit = LabelHit(
            label_id=label.id,
            chain=chain,
            address=label.address,
            entity_id=ent.id,
            entity_name=ent.name,
            entity_type=ent.entity_type,
            is_vasp=ent.is_vasp,
            role=label.role,
            source=label.source,
            source_url=label.source_url,
            reliability=label.reliability,
            tier=label.tier,
            retrieved_at=label.retrieved_at.isoformat(timespec="seconds") + "Z",
            evidence=label.evidence,
        )
        self.remember(hit)
        return hit

    async def sanctions_screen(self, chain: str, address: str) -> LabelHit | None:
        from app.labels.sources import chainalysis_sanctions

        if not self.enable_live:
            return None
        found = await chainalysis_sanctions.lookup(chain, address)
        if not found:
            return None
        ent = self._resolve_entity(found["entity_name"], "SANCTIONED")
        label = persist_runtime_label(
            self.db, chain, address, ent.id, "UNKNOWN", found["source"], found["reliability"], found["tier"], found["evidence"],
            source_url=found.get("source_url"),
        )
        hit = LabelHit(
            label_id=label.id, chain=chain, address=label.address, entity_id=ent.id, entity_name=ent.name,
            entity_type=ent.entity_type, is_vasp=False, role="UNKNOWN", source=label.source,
            source_url=label.source_url, reliability=label.reliability, tier=label.tier,
            retrieved_at=label.retrieved_at.isoformat(timespec="seconds") + "Z", evidence=label.evidence,
        )
        self.remember(hit)
        return hit


def entity_key(name: str) -> str:
    return name_key(name)
