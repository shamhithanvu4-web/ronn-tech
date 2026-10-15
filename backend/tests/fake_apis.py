"""A fake Etherscan / TronGrid / Esplora / Helius / CoinGecko served through httpx.MockTransport,
answering from a synthetic scenario (tests/fixtures/synthetic_engine_*.json). Used to record
synthetic replay fixtures in tests; never used outside tests."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx

T0 = 1_700_000_000
PRICES = {"ethereum": 2000, "bitcoin": 50000, "tron": 0.25, "polygon-ecosystem-token": 0.5, "solana": 100}
CHAIN_IDS = {"1": "ethereum", "137": "polygon", "42161": "arbitrum", "56": "bsc"}
TOKEN_DECIMALS = 6


def _json(data: Any) -> httpx.Response:
    return httpx.Response(200, text=json.dumps(data), headers={"content-type": "application/json"})


class FakeAPIs:
    def __init__(self, scenario: dict[str, Any]):
        self.s = scenario
        self.requests: list[str] = []

    # ---------------------------------------------------------------- etherscan
    def etherscan(self, q: dict[str, str]) -> httpx.Response:
        chain = CHAIN_IDS.get(q.get("chainid", ""), "?")
        if q.get("module") == "block":
            ts = int(q["timestamp"])
            blk = 18_000_000 + (ts - T0) // 12 + (1 if q.get("closest") == "after" else 0)
            return _json({"status": "1", "message": "OK", "result": str(blk)})
        addr = q["address"].lower()
        start, end = int(q.get("startblock", 0)), int(q.get("endblock", 99_999_999))
        action = q["action"]
        rows = [
            t for t in self.s.get("transfers", [])
            if t["chain"] == chain and addr in (t["from"].lower(), t["to"].lower())
            and start <= (t["block_number"] or 0) <= end
        ]
        if action == "txlist":
            rows = [t for t in rows if t["token_contract"] is None]
            out = [
                {"hash": t["tx_hash"], "timeStamp": str(t["block_time"]), "blockNumber": str(t["block_number"]),
                 "from": t["from"], "to": t["to"], "value": str(int(Decimal(t["amount"]) * 10**18)),
                 "isError": "0", "txreceipt_status": "1"}
                for t in rows
            ]
        elif action == "tokentx":
            rows = [t for t in rows if t["token_contract"] is not None]
            out = [
                {"hash": t["tx_hash"], "timeStamp": str(t["block_time"]), "blockNumber": str(t["block_number"]),
                 "from": t["from"], "to": t["to"], "value": str(int(Decimal(t["amount"]) * 10**TOKEN_DECIMALS)),
                 "contractAddress": t["token_contract"], "tokenSymbol": t["asset"], "tokenDecimal": str(TOKEN_DECIMALS)}
                for t in rows
            ]
        else:
            out = []
        out.sort(key=lambda r: int(r["blockNumber"]), reverse=True)
        page, offset = int(q.get("page", 1)), int(q.get("offset", 1000))
        out = out[(page - 1) * offset : page * offset]
        if not out:
            return _json({"status": "0", "message": "No transactions found", "result": []})
        return _json({"status": "1", "message": "OK", "result": out})

    # ---------------------------------------------------------------- trongrid
    def trongrid(self, path: str, q: dict[str, str]) -> httpx.Response:
        parts = path.strip("/").split("/")
        addr = parts[2]
        lo = int(q.get("min_timestamp", 0)) // 1000
        hi = int(q.get("max_timestamp", 10**13)) // 1000
        rows = [t for t in self.s.get("transfers", []) if t["chain"] == "tron" and addr in (t["from"], t["to"]) and lo <= t["block_time"] <= hi]
        if path.endswith("/trc20"):
            contract = q.get("contract_address")
            data = [
                {"transaction_id": t["tx_hash"], "token_info": {"symbol": t["asset"], "address": t["token_contract"], "decimals": TOKEN_DECIMALS, "name": t["asset"]},
                 "block_timestamp": t["block_time"] * 1000, "from": t["from"], "to": t["to"], "type": "Transfer",
                 "value": str(int(Decimal(t["amount"]) * 10**TOKEN_DECIMALS))}
                for t in rows if t["token_contract"] == contract
            ]
        else:
            data = []
        data.sort(key=lambda r: r["block_timestamp"], reverse=True)
        return _json({"success": True, "data": data, "meta": {"at": T0 * 1000, "page_size": len(data)}})

    # ---------------------------------------------------------------- esplora
    def _esplora_tx(self, tx: dict[str, Any]) -> dict[str, Any]:
        sats = lambda v: int(Decimal(v) * 100_000_000)  # noqa: E731
        total_in = sum(sats(i["value"]) for i in tx["inputs"])
        total_out = sum(sats(o["value"]) for o in tx["outputs"])
        return {
            "txid": tx["tx_id"],
            "status": {"confirmed": True, "block_time": tx["block_time"], "block_height": tx["block_height"]},
            "fee": total_in - total_out,
            "vin": [{"is_coinbase": False, "prevout": {"scriptpubkey_address": i["address"], "value": sats(i["value"])}} for i in tx["inputs"]],
            "vout": [{"scriptpubkey_address": o["address"], "value": sats(o["value"])} for o in tx["outputs"]],
        }

    def esplora(self, path: str) -> httpx.Response:
        parts = path.strip("/").split("/")
        if parts[1] == "address":
            addr = parts[2]
            txs = [t for t in self.s.get("raw_txs", []) if addr in {i["address"] for i in t["inputs"]} | {o["address"] for o in t["outputs"]}]
            if len(parts) == 3:
                return _json({"address": addr, "chain_stats": {"tx_count": len(txs)}})
            if len(parts) >= 5 and parts[4] == "chain":
                return _json([])
            txs.sort(key=lambda t: t["block_time"], reverse=True)
            return _json([self._esplora_tx(t) for t in txs])
        if parts[1] == "tx":
            tx = next(t for t in self.s.get("raw_txs", []) if t["tx_id"] == parts[2])
            return _json(self._esplora_tx(tx))
        return httpx.Response(404, text="not found")

    # ---------------------------------------------------------------- helius
    def helius(self, path: str, q: dict[str, str]) -> httpx.Response:
        addr = path.strip("/").split("/")[2]
        if q.get("before-signature"):
            return _json([])
        txs: dict[str, dict[str, Any]] = {}
        for t in self.s.get("transfers", []):
            if t["chain"] != "solana" or addr not in (t["from"], t["to"]):
                continue
            tx = txs.setdefault(t["tx_hash"], {"signature": t["tx_hash"], "timestamp": t["block_time"], "slot": t["block_number"] or 0,
                                               "nativeTransfers": [], "tokenTransfers": [], "transactionError": None})
            if t["token_contract"]:
                tx["tokenTransfers"].append({"fromUserAccount": t["from"], "toUserAccount": t["to"], "tokenAmount": float(t["amount"]), "mint": t["token_contract"]})
            else:
                tx["nativeTransfers"].append({"fromUserAccount": t["from"], "toUserAccount": t["to"], "amount": int(Decimal(t["amount"]) * 10**9)})
        return _json(sorted(txs.values(), key=lambda x: x["timestamp"], reverse=True))

    # ---------------------------------------------------------------- router
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(str(url))
        q = dict(url.params)
        host = url.host
        if host == "api.etherscan.io":
            return self.etherscan(q)
        if host == "api.trongrid.io":
            return self.trongrid(url.path, q)
        if host in ("blockstream.info", "mempool.space"):
            return self.esplora(url.path)
        if host == "api.coingecko.com":
            coin = q.get("ids")
            return _json({coin: {"usd": PRICES.get(coin, 1)}})
        if "helius" in host:
            return self.helius(url.path, q)
        return httpx.Response(404, text=f"unexpected host {host}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
