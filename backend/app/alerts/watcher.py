"""Watchlist polling (SPEC §7). Started from the FastAPI lifespan every
WATCHLIST_INTERVAL_SECONDS (default 300 s).

Any new outgoing in-scope transfer from a watched wallet creates an Alert. If the funds
reach a labelled VASP within 2 hops (directly, via a recipient that forwards to a
labelled VASP, or via a recipient that passes the deposit-address sweep test), the alert
is marked urgent: "Funds moving toward <VASP> now".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.audit import record
from app.config import scoring_config
from app.db import session_scope, utcnow
from app.engine.datasource import ProductionLabels, ProviderDataSource
from app.engine.deposit_inference import infer_deposit
from app.engine.graph_builder import FlowGraph
from app.engine.traversal import TraversalConfig, Traverser
from app.errors import AppError
from app.models import Alert, CaseEvent, WatchItem
from app.providers.base import Transfer
from app.providers.context import AnalysisContext, use_context
from app.util import short_addr

log = logging.getLogger("ronntech.watcher")
CALL_BUDGET_PER_ITEM = 40
SEEN_CAP = 1000


def _vasp_hit(labels: ProductionLabels, chain: str, address: str):
    hits = [h for h in labels.lookup(chain, address) if h.is_vasp]
    return hits[0] if hits else None


async def _trace_two_hops(t: Transfer, ds: ProviderDataSource, labels: ProductionLabels, trav: Traverser) -> dict[str, Any] | None:
    """Does this outgoing transfer reach a labelled VASP within 2 hops?"""
    hit = _vasp_hit(labels, t.chain, t.to_addr)
    if hit is not None:
        return {"hops": 1, "entity_id": hit.entity_id, "entity_name": hit.entity_name, "via": t.to_addr, "source": hit.source, "evidence_txs": [t.tx_hash]}
    try:
        rows = await ds.get_transfers(t.chain, t.to_addr, t.block_time, None, int(scoring_config()["traversal"].get("transfers_page_limit", 1000)), start_block=t.block_number)
    except AppError:
        return None
    dep = infer_deposit(t.chain, t.to_addr, rows, [t], labels)
    if dep is not None:
        return {
            "hops": 1,
            "entity_id": dep.entity_id,
            "entity_name": dep.entity_name,
            "via": t.to_addr,
            "source": "inferred_deposit",
            "evidence_txs": [t.tx_hash] + dep.sweep_txs[:3],
            "note": "recipient passes the deposit-address sweep test",
        }
    for nxt in sorted(rows, key=lambda x: x.block_time):
        if nxt.from_addr != t.to_addr or nxt.block_time < t.block_time or not nxt.in_scope or nxt.amount_dec <= 0 or trav.is_dust(nxt):
            continue
        hit = _vasp_hit(labels, nxt.chain, nxt.to_addr)
        if hit is not None:
            return {"hops": 2, "entity_id": hit.entity_id, "entity_name": hit.entity_name, "via": nxt.to_addr, "source": hit.source, "evidence_txs": [t.tx_hash, nxt.tx_hash]}
    return None


async def check_item(item_id: int) -> list[int]:
    """Poll one watch item; returns the ids of alerts created."""
    with session_scope() as db:
        item = db.get(WatchItem, item_id)
        if item is None or not item.active:
            return []
        chain, address, baseline = item.chain, item.address, item.baseline_time
        seen = set(item.seen_tx or [])
        case_id = item.case_id
    created: list[int] = []
    ctx = AnalysisContext(analysis_id=None, api_call_budget=CALL_BUDGET_PER_ITEM, bypass_cache=True)
    try:
        with use_context(ctx), session_scope() as ldb:
            labels = ProductionLabels(ldb, enable_live=False)
            ds = ProviderDataSource()
            trav = Traverser(ds, labels, TraversalConfig.from_config(), FlowGraph(), {})
            # The latest page of the wallet's history (newest first), filtered locally by the baseline.
            rows = await ds.get_transfers(chain, address, None, None, int(scoring_config()["traversal"].get("transfers_page_limit", 1000)))
            new = [
                t for t in sorted(rows, key=lambda x: (x.block_time, x.tx_hash))
                if t.from_addr == address and t.to_addr and t.to_addr != address and t.in_scope and t.amount_dec > 0
                and not trav.is_dust(t) and (baseline is None or t.block_time > baseline)
                and f"{t.tx_hash}:{t.to_addr}:{t.asset}" not in seen
            ]
            findings = []
            for t in new:
                reach = await _trace_two_hops(t, ds, labels, trav)
                findings.append((t, reach))
        with session_scope() as db:
            item = db.get(WatchItem, item_id)
            latest = baseline
            for t, reach in findings:
                key = f"{t.tx_hash}:{t.to_addr}:{t.asset}"
                seen.add(key)
                latest = max(latest or 0, t.block_time)
                urgent = reach is not None
                msg = (
                    f"Funds moving toward {reach['entity_name']} now: {t.amount} {t.asset} from {short_addr(address)} "
                    f"({reach['hops']} hop{'s' if reach['hops'] != 1 else ''})"
                    if urgent
                    else f"New outgoing transfer of {t.amount} {t.asset} from {short_addr(address)} to {short_addr(t.to_addr)}"
                )
                alert = Alert(
                    watch_id=item_id, chain=chain, address=address, tx_hash=t.tx_hash, to_addr=t.to_addr, asset=t.asset,
                    amount=t.amount, block_time=t.block_time, urgent=urgent,
                    vasp_entity_id=reach["entity_id"] if reach else None, vasp_name=reach["entity_name"] if reach else None,
                    message=msg, evidence={"transfer": t.to_dict(), "reach": reach},
                )
                db.add(alert)
                db.flush()
                created.append(alert.id)
                if case_id:
                    db.add(CaseEvent(case_id=case_id, actor="watchlist", kind="alert_urgent" if urgent else "alert", message=msg, data={"alert_id": alert.id}))
                record(db, "watchlist", "alert_created", "alert", alert.id, {"urgent": urgent, "tx_hash": t.tx_hash, "vasp": alert.vasp_name}, commit=False)
            item.seen_tx = sorted(seen)[-SEEN_CAP:]
            item.baseline_time = latest
            item.last_checked_at = utcnow()
            item.last_error = None
    except AppError as exc:
        with session_scope() as db:
            item = db.get(WatchItem, item_id)
            if item is not None:
                item.last_checked_at = utcnow()
                item.last_error = f"{exc.code}: {exc.detail}"[:500]
    return created


async def poll_async() -> int:
    with session_scope() as db:
        ids = list(db.execute(select(WatchItem.id).where(WatchItem.active.is_(True))).scalars())
    total = 0
    for i in ids:
        try:
            total += len(await check_item(i))
        except Exception:  # noqa: BLE001 - one bad item must not stop the others
            log.exception("watch item %s failed", i)
    return total


def poll_all() -> int:
    return asyncio.run(poll_async())


def check_now(item_id: int) -> list[int]:
    return asyncio.run(check_item(item_id))
