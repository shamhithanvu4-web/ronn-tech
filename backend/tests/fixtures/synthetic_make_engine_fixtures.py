"""Generates the synthetic mini-graph scenarios used by engine unit tests.

SYNTHETIC TEST DATA ONLY. Every address below is fabricated (repeated-byte keys)
and every transaction hash is a made-up label. Nothing here describes a real
wallet, exchange or transaction. Run from backend/:
    python tests/fixtures/synthetic_make_engine_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from app.engine.validate import encode_segwit, tron_hex_to_base58  # noqa: E402

T0 = 1_700_000_000
USDT_ETH = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def evm(byte: str) -> str:
    return "0x" + byte * 20


def tron(byte: str) -> str:
    return tron_hex_to_base58("41" + byte * 20)


def btc(byte: int) -> str:
    return encode_segwit("bc", 0, bytes([byte]) * 20)


def blk(t: int) -> int:
    return 18_000_000 + (t - T0) // 12


def eth_tx(tx, t, frm, to, amount, asset="ETH", usd=None, contract=None, in_scope=True):
    return {
        "chain": "ethereum", "tx_hash": tx, "block_time": t, "from": frm, "to": to, "asset": asset,
        "token_contract": contract, "amount": str(amount),
        "usd": (str(usd) if usd is not None else (str(amount) if contract else str(float(amount) * 2000))),
        "block_number": blk(t), "in_scope": in_scope,
    }


def usdt(tx, t, frm, to, amount, contract=USDT_ETH, in_scope=True, usd=True):
    return eth_tx(tx, t, frm, to, amount, asset="USDT", contract=contract, in_scope=in_scope, usd=(str(amount) if usd else None))


def trc20(tx, t, frm, to, amount):
    return {"chain": "tron", "tx_hash": tx, "block_time": t, "from": frm, "to": to, "asset": "USDT",
            "token_contract": USDT_TRON, "amount": str(amount), "usd": str(amount), "block_number": None, "in_scope": True}


def label(chain, address, eid, name, etype, rel, tier="A", role="HOT", source="synthetic"):
    return {"chain": chain, "address": address, "entity_id": eid, "entity_name": name, "entity_type": etype,
            "is_vasp": etype in ("CEX", "CUSTODIAL_WALLET", "INSTANT_SWAP", "OTC_P2P", "PAYMENT_PROCESSOR"),
            "role": role, "source": source, "reliability": rel, "tier": tier}


def raw(tx, t, inputs, outputs):
    return {"chain": "bitcoin", "tx_id": tx, "block_time": t, "block_height": 800_000 + (t - T0) // 600,
            "inputs": [{"address": a, "value": str(v)} for a, v in inputs],
            "outputs": [{"address": a, "value": str(v)} for a, v in outputs]}


scenarios = {}

# 1. Hop-0: the suspect is itself an exchange deposit address (sweep + gas top-up + exclusivity).
S, V, H = evm("5e"), evm("a1"), evm("40")
scenarios["synthetic_engine_deposit_hop0.json"] = {
    "chain": "ethereum", "suspect": S, "btc_price": None,
    "labels": [label("ethereum", H, 1, "Acmex (synthetic)", "CEX", 0.95)],
    "transfers": [
        usdt("syn-dep-1", T0, V, S, 1000),
        eth_tx("syn-gas-1", T0 + 1800, H, S, "0.004", usd="8.00"),
        usdt("syn-sweep-1", T0 + 3600, S, H, 1000),
    ],
}

# 1b. Sweep only (no gas top-up, two outgoing counterparties -> no exclusivity).
S2, O2 = evm("5f"), evm("77")
scenarios["synthetic_engine_deposit_sweep_only.json"] = {
    "chain": "ethereum", "suspect": S2,
    "labels": [label("ethereum", H, 1, "Acmex (synthetic)", "CEX", 0.95)],
    "transfers": [
        usdt("syn-dep-2", T0, V, S2, 1000),
        usdt("syn-sweep-2", T0 + 3600, S2, H, 995),
        usdt("syn-other-2", T0 + 7200, S2, O2, 50),
    ],
}

# 1c. Late sweep (outside tau = 3,200 blocks) -> no inference.
S3 = evm("60")
scenarios["synthetic_engine_deposit_late.json"] = {
    "chain": "ethereum", "suspect": S3,
    "labels": [label("ethereum", H, 1, "Acmex (synthetic)", "CEX", 0.95)],
    "transfers": [
        usdt("syn-dep-3", T0, V, S3, 1000),
        usdt("syn-sweep-3", T0 + 3201 * 12 + 60, S3, H, 1000),
    ],
}

# 2. TRON multi-hop: S -> A -> B, B sweeps to a labelled exchange wallet (B inferred deposit).
TS, TA, TB, TH, TV = tron("a1"), tron("b2"), tron("c3"), tron("d4"), tron("e5")
scenarios["synthetic_engine_tron_multihop.json"] = {
    "chain": "tron", "suspect": TS,
    "labels": [label("tron", TH, 2, "Betax (synthetic)", "CEX", 0.80, tier="B")],
    "transfers": [
        trc20("syn-t-1", T0, TV, TS, 2000),
        trc20("syn-t-2", T0 + 600, TS, TA, 2000),
        trc20("syn-t-3", T0 + 3600, TA, TB, 1990),
        trc20("syn-t-4", T0 + 7200, TB, TH, 1975),
    ],
}

# 3. Bitcoin common-input clustering: D co-spends with an exchange-labelled input.
BS, BD, BE, BZ, BV, BC = btc(0xA1), btc(0xB2), btc(0xC3), btc(0xD4), btc(0xF6), btc(0x17)
scenarios["synthetic_engine_btc_cluster.json"] = {
    "chain": "bitcoin", "suspect": BS, "btc_price": "50000",
    "labels": [label("bitcoin", BE, 3, "Gammax (synthetic)", "CEX", 0.95)],
    "raw_txs": [
        raw("syn-b-in", T0, [(BV, "1.001")], [(BS, "1.0")]),
        raw("syn-b-1", T0 + 600, [(BS, "1.0")], [(BD, "0.6"), (BC, "0.399")]),
        raw("syn-b-2", T0 + 1800, [(BD, "0.6"), (BE, "0.4")], [(BZ, "0.999")]),
    ],
}

# 4. Mixer and bridge breakpoint (no VASP).
MS, MX, BR = evm("61"), evm("71"), evm("72")
scenarios["synthetic_engine_mixer_bridge.json"] = {
    "chain": "ethereum", "suspect": MS,
    "labels": [
        label("ethereum", MX, 4, "Mixer (synthetic)", "MIXER", 0.80, tier="B", role="CONTRACT"),
        label("ethereum", BR, 5, "Bridge (synthetic)", "BRIDGE", 0.80, tier="B", role="CONTRACT"),
    ],
    "transfers": [
        eth_tx("syn-m-0", T0, V, MS, "10"),
        eth_tx("syn-m-1", T0 + 100, MS, MX, "5"),
        eth_tx("syn-m-2", T0 + 200, MS, BR, "5"),
    ],
}

# 5. Honest no-match.
NS, NA, NB = evm("62"), evm("73"), evm("74")
scenarios["synthetic_engine_nomatch.json"] = {
    "chain": "ethereum", "suspect": NS, "labels": [],
    "transfers": [
        usdt("syn-n-0", T0, V, NS, 5000),
        usdt("syn-n-1", T0 + 7200, NS, NA, 5000),
        usdt("syn-n-2", T0 + 20000, NA, NB, 5000),
    ],
}

# 6. Typologies on EVM: fan-out, structuring, rapid pass-through, address poisoning, fan-in, dust.
FS = evm("63")
recips = [evm(f"{0x80 + i:02x}") for i in range(10)]
R1, R1b = recips[0], evm("9a")
lookalike = "0x" + R1[2:6] + "ee" * 16 + R1[-4:]
fan_in_senders = [evm(f"{0xb0 + i:02x}") for i in range(10)]
tx = [usdt("syn-f-in", T0, V, FS, 10000)]
tx += [usdt(f"syn-f-out-{i}", T0 + 7200 + i * 600, FS, r, 1000) for i, r in enumerate(recips)]
tx += [usdt("syn-f-rapid", T0 + 7200 + 900, R1, R1b, 1000)]
tx += [usdt("syn-f-poison", T0 + 7300, lookalike, FS, 0)]
tx += [usdt("syn-f-dust", T0 + 7400, evm("9b"), FS, 2)]
tx += [usdt(f"syn-f-fanin-{i}", T0 + 100 + i * 60, s, FS, 25) for i, s in enumerate(fan_in_senders)]
scenarios["synthetic_engine_typologies.json"] = {"chain": "ethereum", "suspect": FS, "labels": [], "transfers": tx}

# 7. Bitcoin peel chain ending in a CoinJoin-like transaction.
PS, P1, P2, P3 = btc(0x21), btc(0x22), btc(0x23), btc(0x24)
x1, x2, x3 = btc(0x31), btc(0x32), btc(0x33)
cj_outs = [(btc(0x40 + i), "1.5") for i in range(5)]
scenarios["synthetic_engine_peel_coinjoin.json"] = {
    "chain": "bitcoin", "suspect": PS, "btc_price": "50000", "labels": [],
    "raw_txs": [
        raw("syn-p-in", T0, [(BV, "10.0001")], [(PS, "10.0")]),
        raw("syn-p-1", T0 + 600, [(PS, "10.0")], [(x1, "0.5"), (P1, "9.49")]),
        raw("syn-p-2", T0 + 1200, [(P1, "9.49")], [(x2, "0.4"), (P2, "9.08")]),
        raw("syn-p-3", T0 + 1800, [(P2, "9.08")], [(x3, "0.3"), (P3, "8.77")]),
        raw("syn-p-cj", T0 + 2400, [(P3, "8.77"), (btc(0x50), "1.0")], cj_outs + [(btc(0x51), "2.26")]),
    ],
}

# 8. Funding source: an exchange hot wallet withdraws to the suspect.
WS, W = evm("64"), evm("41")
scenarios["synthetic_engine_funding.json"] = {
    "chain": "ethereum", "suspect": WS,
    "labels": [label("ethereum", W, 6, "Deltax (synthetic)", "CEX", 0.95)],
    "transfers": [
        usdt("syn-w-1", T0, W, WS, 5000),
        usdt("syn-w-2", T0 + 86400 * 2, WS, NA, 5000),
    ],
}

# 9. Direct sanctioned exposure.
SS, SO = evm("65"), evm("42")
scenarios["synthetic_engine_sanctioned.json"] = {
    "chain": "ethereum", "suspect": SS,
    "labels": [label("ethereum", SO, 7, "Sanctioned (synthetic)", "SANCTIONED", 0.95, role="UNKNOWN")],
    "transfers": [usdt("syn-s-0", T0, V, SS, 3000), usdt("syn-s-1", T0 + 4000, SS, SO, 3000)],
}

# 10. Unlabelled high-activity counterparty (> 200 counterparties) -> possible VASP, terminal.
KS, K = evm("66"), evm("43")
k_rows = [usdt("syn-k-0", T0, V, KS, 4000), usdt("syn-k-1", T0 + 4000, KS, K, 4000)]
for i in range(205):
    k_rows.append(usdt(f"syn-k-cp-{i}", T0 + 5000 + i * 30, K, "0x" + f"{i:040x}", 15))
scenarios["synthetic_engine_high_activity.json"] = {"chain": "ethereum", "suspect": KS, "labels": [], "transfers": k_rows}

# 11. Multi-path combination: two independent paths to the same exchange.
CS, CA, CB, CH1, CH2 = evm("67"), evm("75"), evm("76"), evm("44"), evm("45")
scenarios["synthetic_engine_two_paths.json"] = {
    "chain": "ethereum", "suspect": CS,
    "labels": [
        label("ethereum", CH1, 8, "Epsilonx (synthetic)", "CEX", 0.95),
        label("ethereum", CH2, 8, "Epsilonx (synthetic)", "CEX", 0.95),
    ],
    "transfers": [
        usdt("syn-c-0", T0, V, CS, 2000),
        usdt("syn-c-1", T0 + 100, CS, CA, 1000),
        usdt("syn-c-2", T0 + 200, CS, CB, 1000),
        usdt("syn-c-3", T0 + 86400 * 5, CA, CH1, 1000),
        usdt("syn-c-4", T0 + 86400 * 40, CB, CH2, 1000),
    ],
}

# 12. Solana: S -> labelled exchange wallet (native SOL), 1 hop.
def sol(byte: int) -> str:
    import base58

    return base58.b58encode(bytes([byte]) * 32).decode()


def sol_tx(tx, t, frm, to, amount):
    return {"chain": "solana", "tx_hash": tx, "block_time": t, "from": frm, "to": to, "asset": "SOL", "token_contract": None,
            "amount": str(amount), "usd": str(float(amount) * 100), "block_number": 250_000_000 + (t - T0) * 2, "in_scope": True}


SS, SV, SH = sol(0x51), sol(0x52), sol(0x53)
scenarios["synthetic_engine_solana.json"] = {
    "chain": "solana", "suspect": SS,
    "labels": [label("solana", SH, 9, "Zetax (synthetic)", "CEX", 0.95)],
    "transfers": [
        sol_tx("syn-sol-1", T0, SV, SS, 50),
        sol_tx("syn-sol-2", T0 + 900, SS, SH, 49),
    ],
}

# 13. Cross-EVM: the same EOA is active on Ethereum and Polygon; the exit is on Polygon.
USDC_POLYGON = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
XS, XV, XH = evm("68"), evm("78"), evm("46")
x_poly = usdt("syn-x-2", T0 + 1200, XS, XH, 3000, contract=USDC_POLYGON)
x_poly.update({"chain": "polygon", "asset": "USDC"})
scenarios["synthetic_engine_cross_evm.json"] = {
    "chain": "ethereum", "suspect": XS,
    "labels": [label("polygon", XH, 10, "Thetax (synthetic)", "CEX", 0.95)],
    "transfers": [
        usdt("syn-x-1", T0, XV, XS, 3000),
        x_poly,
    ],
}

if __name__ == "__main__":
    for name, data in scenarios.items():
        data["_comment"] = "SYNTHETIC TEST DATA - fabricated addresses and transactions; see synthetic_make_engine_fixtures.py"
        (HERE / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print("wrote", name)
