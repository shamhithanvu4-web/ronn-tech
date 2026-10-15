"""Graph traversal (SPEC §6.2, §6.3).

Forward BFS from the suspect to max_hops (default 3, max 5); backward 2 hops for
funding-source VASPs; hop-0 checks on the suspect itself. Per node: follow the top 5
counterparties by value plus any counterparty receiving >= 10 % of the node's outflow;
skip dust (< $10) and never follow zero-value token transfers (flagged as possible
address poisoning). Terminal nodes: labelled VASP / MIXER / BRIDGE / SANCTIONED (and
DEX), or unlabelled high-activity services (> 5,000 txs or > 200 counterparties in the
window). Budget: 150 nodes or 600 API calls; when exhausted the result is "partial".
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from app.config import chain_config, scoring_config
from app.engine.btc_clustering import cluster_by_common_input, likely_change_output
from app.engine.deposit_inference import infer_deposit
from app.engine.graph_builder import FlowGraph
from app.engine.types import DataSource, LabelLookup, NodeInfo, node_id
from app.errors import AppError, PlanRequired
from app.labels.store import inferred_hit
from app.providers.base import Transfer
from app.providers.context import BudgetExceeded, current

ACCOUNT_FAMILIES = ("evm", "tron", "solana")


@dataclass
class TraversalConfig:
    max_hops: int = 3
    backward_hops: int = 2
    include_funding: bool = True
    window_days: int = 90
    window_start: int | None = None
    top_k: int = 5
    min_share: Decimal = Decimal("0.10")
    dust_usd: Decimal = Decimal("10")
    high_activity_tx: int = 5000
    high_activity_cps: int = 200
    node_budget: int = 150
    page_limit: int = 1000
    terminal_types: tuple[str, ...] = ("CEX", "CUSTODIAL_WALLET", "INSTANT_SWAP", "OTC_P2P", "PAYMENT_PROCESSOR", "MIXER", "BRIDGE", "SANCTIONED", "DEX")
    concurrency: int = 4

    @classmethod
    def from_config(cls, **overrides: Any) -> "TraversalConfig":
        t = scoring_config()["traversal"]
        cfg = cls(
            max_hops=int(t.get("default_max_hops", 3)),
            backward_hops=int(t.get("backward_hops", 2)),
            window_days=int(t.get("window_days", 90)),
            top_k=int(t.get("top_counterparties", 5)),
            min_share=Decimal(str(t.get("min_outflow_share", 0.10))),
            dust_usd=Decimal(str(t.get("dust_usd", 10))),
            high_activity_tx=int(t.get("high_activity_tx_count", 5000)),
            high_activity_cps=int(t.get("high_activity_counterparties", 200)),
            node_budget=int(t.get("node_budget", 150)),
            page_limit=int(t.get("transfers_page_limit", 1000)),
            terminal_types=tuple(t.get("terminal_entity_types") or cls.terminal_types),
        )
        limit = int(t.get("max_hops_limit", 5))
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        cfg.max_hops = max(1, min(int(cfg.max_hops), limit))
        return cfg


@dataclass
class TraversalResult:
    chain: str
    suspect: str
    window_start: int | None
    window_end: int | None
    partial: bool = False
    partial_reasons: list[str] = field(default_factory=list)
    poisoning: list[dict[str, Any]] = field(default_factory=list)
    coinjoins: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    unfollowed: dict[str, dict[str, Any]] = field(default_factory=dict)


def family_of(chain: str) -> str:
    return chain_config(chain).get("family", "evm")


class Traverser:
    def __init__(
        self,
        ds: DataSource,
        labels: LabelLookup,
        cfg: TraversalConfig,
        graph: FlowGraph,
        nodes: dict[str, NodeInfo],
        progress: Callable[[str, int], None] | None = None,
    ) -> None:
        self.ds = ds
        self.labels = labels
        self.cfg = cfg
        self.graph = graph
        self.nodes = nodes
        self.progress = progress or (lambda _stage, _pct: None)
        self.budget_hit = False

    # ------------------------------------------------------------------ helpers
    def _dust_native(self, chain: str) -> Decimal:
        try:
            return Decimal(str(chain_config(chain).get("dust_native") or "0"))
        except KeyError:
            return Decimal(0)

    def is_dust(self, t: Transfer) -> bool:
        usd = t.usd_dec
        if usd is not None:
            return usd < self.cfg.dust_usd
        if t.token_contract is None:
            return t.amount_dec < self._dust_native(t.chain)
        return True  # out-of-scope token without a price: never followed

    def eligible_out(self, info: NodeInfo, since: int | None, until: int | None) -> list[Transfer]:
        return [
            t
            for t in info.transfers
            if t.from_addr == info.address
            and t.to_addr
            and t.to_addr != info.address
            and t.in_scope
            and t.amount_dec > 0
            and not self.is_dust(t)
            and (since is None or t.block_time >= since)
            and (until is None or t.block_time <= until)
        ]

    def eligible_in(self, info: NodeInfo, since: int | None, until: int | None) -> list[Transfer]:
        return [
            t
            for t in info.transfers
            if t.to_addr == info.address
            and t.from_addr
            and t.from_addr != info.address
            and t.in_scope
            and t.amount_dec > 0
            and not self.is_dust(t)
            and (since is None or t.block_time >= since)
            and (until is None or t.block_time <= until)
        ]

    @staticmethod
    def group_value(transfers: list[Transfer], key: Callable[[Transfer], str]) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for t in transfers:
            g = groups.setdefault(key(t), {"usd": Decimal(0), "usd_known": True, "amount": defaultdict(Decimal), "transfers": []})
            if t.usd_dec is None:
                g["usd_known"] = False
            else:
                g["usd"] += t.usd_dec
            g["amount"][t.asset] += t.amount_dec
            g["transfers"].append(t)
        return groups

    @staticmethod
    def share(groups: dict[str, dict[str, Any]], target: str) -> Decimal:
        if target not in groups:
            return Decimal(0)
        if all(g["usd_known"] for g in groups.values()):
            total = sum((g["usd"] for g in groups.values()), Decimal(0))
            return (groups[target]["usd"] / total) if total > 0 else Decimal(0)
        tgt = groups[target]["amount"]
        asset = max(tgt, key=lambda a: tgt[a])
        total = sum((g["amount"].get(asset, Decimal(0)) for g in groups.values()), Decimal(0))
        return (tgt[asset] / total) if total > 0 else Decimal(0)

    def classify(self, info: NodeInfo) -> None:
        info.labels = self.labels.lookup(info.chain, info.address)
        if info.labels:
            terminal = [h for h in info.labels if h.entity_type in self.cfg.terminal_types]
            info.attribution = terminal[0] if terminal else info.labels[0]
            if terminal:
                info.terminal_reason = f"labelled {terminal[0].entity_type}"

    def _note_partial(self, res: TraversalResult, reason: str) -> None:
        res.partial = True
        if reason not in res.partial_reasons:
            res.partial_reasons.append(reason)

    async def fetch(self, info: NodeInfo, res: TraversalResult, since: int | None, until: int | None) -> bool:
        if info.fetched:
            return True
        if self.budget_hit:
            self._note_partial(res, "API call budget exhausted")
            return False
        try:
            info.transfers = await self.ds.get_transfers(
                info.chain, info.address, since, until, self.cfg.page_limit,
                start_block=info.entry_block if family_of(info.chain) == "evm" else None,
            )
            if info.chain == "bitcoin":
                info.raw_txs = await self.ds.get_raw_txs(info.chain, info.address, since, until, self.cfg.page_limit)
            info.fetched = True
            return True
        except BudgetExceeded:
            self.budget_hit = True
            self._note_partial(res, "API call budget exhausted")
            return False
        except PlanRequired:
            raise
        except AppError as exc:
            info.fetch_error = f"{exc.code}: {exc.detail}"
            res.errors.append({"node": info.id, "code": exc.code, "detail": exc.detail})
            self._note_partial(res, "provider errors on some nodes")
            return False

    async def check_activity(self, info: NodeInfo, res: TraversalResult, window_end: int | None) -> None:
        """Flag unlabelled high-activity services (possible VASPs)."""
        since = info.entry_time if info.direction != "suspect" else res.window_start
        rows = [t for t in info.transfers if (since is None or t.block_time >= since) and (window_end is None or t.block_time <= window_end)]
        cps = {t.to_addr if t.from_addr == info.address else t.from_addr for t in rows}
        txs = {t.tx_hash for t in rows}
        est = len(txs)
        truncated = len(info.transfers) >= self.cfg.page_limit
        if truncated and rows:
            span = max(t.block_time for t in rows) - min(t.block_time for t in rows)
            full = (window_end or max(t.block_time for t in rows)) - (since or min(t.block_time for t in rows))
            if span > 0 and full > span:
                est = int(len(txs) * full / span)
            info.history_truncated = True
        if info.chain == "bitcoin" and truncated:
            try:
                stats = await self.ds.get_address_stats(info.chain, info.address)
                if stats and stats.get("tx_count"):
                    est = max(est, int(stats["tx_count"]))
            except AppError:
                pass
        info.activity = {
            "tx_count_window": len(txs),
            "tx_count_estimate": est,
            "counterparties_window": len(cps),
            "history_truncated": truncated,
        }
        if est > self.cfg.high_activity_tx or len(cps) > self.cfg.high_activity_cps:
            info.high_activity = True
            if info.direction != "suspect":
                info.terminal_reason = "unlabelled high-activity service (possible VASP)"
            hit = await self.labels.live_lookup(info.chain, info.address)
            if hit is not None:
                info.labels = [hit] + info.labels
                info.attribution = hit
                if hit.entity_type in self.cfg.terminal_types:
                    info.terminal_reason = f"labelled {hit.entity_type} ({hit.source})"

    def record_poisoning(self, info: NodeInfo, res: TraversalResult) -> None:
        for t in info.transfers:
            zero = t.amount_dec == 0
            fake = not t.in_scope and t.asset.upper() in ("USDT", "USDC", "USD₮", "USD₮0")
            if (zero or fake) and t.to_addr and t.from_addr:
                res.poisoning.append(
                    {
                        "node": info.id,
                        "chain": t.chain,
                        "tx_hash": t.tx_hash,
                        "from": t.from_addr,
                        "to": t.to_addr,
                        "asset": t.asset,
                        "token_contract": t.token_contract,
                        "amount": t.amount,
                        "time": t.block_time,
                        "reason": "zero-value transfer" if zero else "token with a stablecoin symbol but an unknown contract",
                    }
                )

    def infer(self, info: NodeInfo, res: TraversalResult, incoming: list[Transfer] | None) -> None:
        """Deposit inference (account chains) or common-input clustering (Bitcoin)."""
        if info.attribution is not None and info.attribution.entity_type in self.cfg.terminal_types:
            return
        fam = family_of(info.chain)
        if fam in ACCOUNT_FAMILIES:
            dep = infer_deposit(info.chain, info.address, info.transfers, incoming, self.labels)
            if dep is not None:
                info.deposit = dep
                hit = inferred_hit(
                    chain=info.chain,
                    address=info.address,
                    entity_id=dep.entity_id,
                    entity_name=dep.entity_name,
                    entity_type=dep.entity_type,
                    role="DEPOSIT",
                    source="inferred_deposit",
                    reliability=dep.reliability,
                    evidence=dep.to_dict()["evidence"] | {"corroborations": dep.corroborations},
                )
                info.attribution = hit
                info.labels = [hit] + info.labels
                info.terminal_reason = f"inferred deposit address of {dep.entity_name}"
                self.labels.remember(hit)
                for t in info.transfers:
                    if t.tx_hash in dep.sweep_txs and t.from_addr == info.address and t.to_addr in dep.hot_wallets:
                        self.graph.add_transfer(t, kind="sweep")
                    if t.tx_hash in dep.gas_topup_txs and t.to_addr == info.address:
                        self.graph.add_transfer(t, kind="gas")
                for hw in dep.hot_wallets:
                    nid = node_id(info.chain, hw)
                    if nid not in self.nodes:
                        hw_info = NodeInfo(chain=info.chain, address=hw, hop=info.hop + 1, direction="sweep_target")
                        self.classify(hw_info)
                        hw_info.terminal_reason = "exchange wallet receiving the sweep"
                        self.nodes[nid] = hw_info
        elif info.chain == "bitcoin":
            cl, coinjoins = cluster_by_common_input(info.address, info.raw_txs, self.labels)
            res.coinjoins.extend(c for c in coinjoins if c not in res.coinjoins)
            if cl is not None:
                info.cluster = cl
                hit = inferred_hit(
                    chain="bitcoin",
                    address=info.address,
                    entity_id=cl.entity_id,
                    entity_name=cl.entity_name,
                    entity_type=cl.entity_type,
                    role="UNKNOWN",
                    source="inferred_btc_cluster",
                    reliability=cl.reliability,
                    evidence=cl.to_dict()["evidence"] | {"co_input_addresses": cl.co_input_addresses},
                )
                info.attribution = hit
                info.labels = [hit] + info.labels
                info.terminal_reason = f"common-input cluster of {cl.entity_name}"
                self.labels.remember(hit)

    def select_counterparties(self, info: NodeInfo, outs: list[Transfer]) -> tuple[list[str], dict[str, Any]]:
        groups = self.group_value(outs, key=lambda t: t.to_addr)
        ranked = sorted(groups, key=lambda a: (groups[a]["usd"], sum(groups[a]["amount"].values())), reverse=True)
        chosen = list(ranked[: self.cfg.top_k])
        for a in ranked[self.cfg.top_k :]:
            if self.share(groups, a) >= self.cfg.min_share:
                chosen.append(a)
        if info.chain == "bitcoin":
            for tx in info.raw_txs:
                if any(i.address == info.address for i in tx.inputs):
                    change = likely_change_output(tx, info.address)
                    if change and change != info.address and change in groups and change not in chosen:
                        chosen.append(change)
        skipped = [a for a in ranked if a not in chosen]
        unfollowed = {
            "counterparties": len(skipped),
            "share": format(sum((self.share(groups, a) for a in skipped), Decimal(0)).quantize(Decimal("0.0001")), "f"),
        }
        return chosen, unfollowed

    # ------------------------------------------------------------------ forward
    async def run_forward(self, chain: str, suspect: NodeInfo, res: TraversalResult) -> None:
        sem = asyncio.Semaphore(self.cfg.concurrency)
        level = [suspect]
        hop = 0
        while level:
            if hop >= self.cfg.max_hops:
                # Leaves: fetch only to run deposit inference / clustering.
                await asyncio.gather(*(self._leaf(n, res, sem) for n in level))
                break
            next_level: dict[str, NodeInfo] = {}
            results = await asyncio.gather(*(self._expand(n, res, sem) for n in level))
            for children in results:
                for c in children:
                    next_level.setdefault(c.id, c)
            level = sorted(next_level.values(), key=lambda n: n.address)
            hop += 1
            self.progress("fetching", min(60, 20 + hop * 12))

    async def _leaf(self, info: NodeInfo, res: TraversalResult, sem: asyncio.Semaphore) -> None:
        if info.terminal_reason:
            return
        async with sem:
            ok = await self.fetch(info, res, info.entry_time, res.window_end)
        if not ok:
            return
        self.record_poisoning(info, res)
        await self.check_activity(info, res, res.window_end)
        if not info.terminal_reason:
            self.infer(info, res, self._incoming_path(info))

    def _incoming_path(self, info: NodeInfo) -> list[Transfer]:
        out = []
        for u, _v, d in self.graph.g.in_edges(info.id, data=True):
            if d["kind"] != "flow":
                continue
            out.append(
                Transfer(
                    chain=d["chain"], tx_hash=d["tx_hash"], block_time=d["time"], from_addr=self.graph.g.nodes[u]["address"],
                    to_addr=info.address, asset=d["asset"], token_contract=d["token_contract"], amount=format(d["amount"], "f"),
                    usd_estimate=format(d["usd"], "f") if d["usd"] is not None else None, is_internal=False,
                    raw_ref=d["raw_ref"], block_number=d["block"],
                )
            )
        return out

    async def _expand(self, info: NodeInfo, res: TraversalResult, sem: asyncio.Semaphore) -> list[NodeInfo]:
        if info.terminal_reason:
            return []
        since = res.window_start if info.direction == "suspect" else info.entry_time
        async with sem:
            ok = await self.fetch(info, res, None if info.direction == "suspect" else since, res.window_end)
        if not ok:
            return []
        self.record_poisoning(info, res)
        await self.check_activity(info, res, res.window_end)
        if info.terminal_reason:
            return []
        if info.direction != "suspect":
            self.infer(info, res, self._incoming_path(info))
            if info.terminal_reason:
                return []
        outs = self.eligible_out(info, since, res.window_end)
        if not outs:
            return []
        chosen, unfollowed = self.select_counterparties(info, outs)
        info.expanded = True
        if unfollowed["counterparties"]:
            res.unfollowed[info.id] = unfollowed
        children: list[NodeInfo] = []
        for cp in chosen:
            nid = node_id(info.chain, cp)
            cp_transfers = sorted((t for t in outs if t.to_addr == cp), key=lambda t: t.block_time)
            if nid not in self.nodes:
                if len(self.nodes) >= self.cfg.node_budget:
                    self._note_partial(res, f"node budget of {self.cfg.node_budget} reached")
                    continue
                child = NodeInfo(
                    chain=info.chain,
                    address=cp,
                    hop=info.hop + 1,
                    direction="forward",
                    entry_time=cp_transfers[0].block_time,
                    entry_block=cp_transfers[0].block_number,
                    entry_txs=[t.tx_hash for t in cp_transfers][:20],
                )
                self.classify(child)
                self.nodes[nid] = child
                children.append(child)
            for t in cp_transfers:
                self.graph.add_transfer(t, kind="flow")
        return children

    # ------------------------------------------------------------------ backward
    async def run_backward(self, suspect: NodeInfo, res: TraversalResult) -> None:
        level = [suspect]
        for hop in range(1, self.cfg.backward_hops + 1):
            next_level: list[NodeInfo] = []
            for info in level:
                if info is not suspect:
                    if info.terminal_reason or (info.attribution and info.attribution.is_vasp):
                        continue
                    if not await self.fetch(info, res, None, info.entry_time):
                        continue
                until = res.window_end if info is suspect else info.entry_time
                ins = self.eligible_in(info, None, until)
                if not ins:
                    continue
                groups = self.group_value(ins, key=lambda t: t.from_addr)
                ranked = sorted(groups, key=lambda a: (groups[a]["usd"], sum(groups[a]["amount"].values())), reverse=True)
                chosen = ranked[: self.cfg.top_k] + [a for a in ranked[self.cfg.top_k :] if self.share(groups, a) >= self.cfg.min_share]
                for sender in chosen:
                    nid = node_id(info.chain, sender)
                    txs = sorted(groups[sender]["transfers"], key=lambda t: t.block_time)
                    for t in txs:
                        self.graph.add_transfer(t, kind="funding")
                    if nid in self.nodes:
                        continue
                    if len(self.nodes) >= self.cfg.node_budget:
                        self._note_partial(res, f"node budget of {self.cfg.node_budget} reached")
                        continue
                    child = NodeInfo(
                        chain=info.chain,
                        address=sender,
                        hop=hop,
                        direction="backward",
                        entry_time=txs[-1].block_time,
                        entry_txs=[t.tx_hash for t in txs][:20],
                    )
                    self.classify(child)
                    self.nodes[nid] = child
                    next_level.append(child)
            level = next_level
            if not level:
                break

    # ------------------------------------------------------------------ entry
    async def run(self, chain: str, address: str, suspect_transfers: list[Transfer]) -> TraversalResult:
        in_scope = [t for t in suspect_transfers if t.in_scope and t.amount_dec > 0]
        times = sorted(t.block_time for t in in_scope)
        start = self.cfg.window_start if self.cfg.window_start is not None else (times[0] if times else None)
        end = start + self.cfg.window_days * 86400 if start is not None else None
        res = TraversalResult(chain=chain, suspect=address, window_start=start, window_end=end)

        suspect = NodeInfo(chain=chain, address=address, hop=0, direction="suspect", entry_time=start)
        suspect.transfers = suspect_transfers
        suspect.fetched = True
        if chain == "bitcoin":
            try:
                suspect.raw_txs = await self.ds.get_raw_txs(chain, address, None, None, self.cfg.page_limit)
            except BudgetExceeded:
                self.budget_hit = True
        if len(suspect_transfers) >= self.cfg.page_limit:
            suspect.history_truncated = True
            self._note_partial(res, "suspect history longer than one page; earliest transfers may be missing")
        self.nodes[suspect.id] = suspect
        self.graph.add_node(suspect)

        # Hop-0 checks: (a) is the suspect labelled? (b) is it itself a deposit address?
        self.classify(suspect)
        if suspect.attribution is not None and suspect.attribution.entity_type in self.cfg.terminal_types:
            suspect.terminal_reason = f"suspect itself is labelled {suspect.attribution.entity_type}"
        else:
            suspect.terminal_reason = None
            window_in = [t for t in self.eligible_in(suspect, start, end)]
            self.infer(suspect, res, window_in or None)
            if suspect.terminal_reason:
                suspect.terminal_reason = "suspect is " + suspect.terminal_reason
        self.record_poisoning(suspect, res)

        self.progress("building_graph", 25)
        await self.run_forward(chain, suspect, res)
        if self.cfg.include_funding:
            await self.run_backward(suspect, res)
        ctx = current()
        if ctx is not None and ctx.api_calls >= ctx.api_call_budget:
            self._note_partial(res, "API call budget exhausted")
        return res
