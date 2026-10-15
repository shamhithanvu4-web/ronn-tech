"""Address validation (SPEC §6.1). Segwit vectors are copied from BIP-350
(https://github.com/bitcoin/bips/blob/master/bip-0350.mediawiki, "Test vectors")."""
from __future__ import annotations

import csv
import hashlib
import os

import base58
import pytest

from app.config import SEED_CSV
from app.engine.validate import decode_segwit, encode_segwit, tron_hex_to_base58, validate_address
from app.errors import InvalidAddress

BIP350_VALID = [
    ("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "0014751e76e8199196d454941c45d1b3a323f1433bd6"),
    ("tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7", "00201863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262"),
    ("bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kt5nd6y", "5128751e76e8199196d454941c45d1b3a323f1433bd6751e76e8199196d454941c45d1b3a323f1433bd6"),
    ("BC1SW50QGDZ25J", "6002751e"),
    ("bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs", "5210751e76e8199196d454941c45d1b3a323"),
    ("tb1qqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesrxh6hy", "0020000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433"),
    ("tb1pqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesf3hn0c", "5120000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433"),
    ("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0", "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"),
]

BIP350_INVALID = [
    "tc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq5zuyut",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",
    "tb1z0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqglt7rf",
    "BC1S0XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ54WELL",
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh",
    "tb1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq24jc47",
    "bc1p38j9r5y49hruaue7wxjce0updqjuyyx0kh56v8s25huc6995vvpql3jow4",
    "BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R",
    "bc1pw5dgrnzv",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v8n0nx0muaewav253zgeav",
    "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P",
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47Zagq",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf",
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vpggkg4j",
    "bc1gmk9yu",
]


def _script(version: int, program: list[int]) -> str:
    op = 0 if version == 0 else 0x50 + version
    return bytes([op, len(program)] + program).hex()


@pytest.mark.parametrize("addr,script", BIP350_VALID)
def test_bip350_valid_vectors(addr, script):
    hrp = addr[:2].lower()
    version, program, reason = decode_segwit(hrp, addr)
    assert version is not None, reason
    assert _script(version, program) == script
    assert encode_segwit(hrp, version, bytes(program)) == addr.lower()


@pytest.mark.parametrize("addr", BIP350_INVALID)
def test_bip350_invalid_vectors(addr):
    for hrp in ("bc", "tb"):
        version, _program, _reason = decode_segwit(hrp, addr)
        assert version is None


def test_mainnet_bech32_detection_and_reason():
    info = validate_address("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")
    assert info.family == "bitcoin" and info.kind == "p2wpkh" and info.chains == ["bitcoin"]
    assert validate_address("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0").kind == "p2tr"
    with pytest.raises(InvalidAddress, match="bech32m"):
        validate_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh")
    with pytest.raises(InvalidAddress, match="checksum"):
        validate_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5")


def _b58check(version: int, payload: bytes) -> str:
    raw = bytes([version]) + payload
    return base58.b58encode(raw + hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]).decode()


def test_base58check_bitcoin_generated():
    p2pkh = _b58check(0x00, os.urandom(20))
    p2sh = _b58check(0x05, os.urandom(20))
    assert validate_address(p2pkh).kind == "p2pkh"
    assert validate_address(p2sh).kind == "p2sh"
    tampered = p2pkh[:-1] + ("2" if p2pkh[-1] != "2" else "3")
    with pytest.raises(InvalidAddress, match="checksum"):
        validate_address(tampered)
    with pytest.raises(InvalidAddress, match="alphabet"):
        validate_address("1" + "0" * 30)


def test_evm_rules():
    info = validate_address("0x" + "aB" * 20)
    assert info.family == "evm" and info.address == "0x" + "ab" * 20
    assert set(info.chains) >= {"ethereum", "polygon", "arbitrum", "bsc"}
    assert validate_address("0x" + "11" * 20, "polygon").chains == ["polygon"]
    with pytest.raises(InvalidAddress, match="40 hex characters"):
        validate_address("0x" + "1" * 39)
    with pytest.raises(InvalidAddress, match="non-hex"):
        validate_address("0x" + "g" * 40)
    with pytest.raises(InvalidAddress, match="not bitcoin"):
        validate_address("0x" + "11" * 20, "bitcoin")


def test_tron_rules():
    addr = tron_hex_to_base58("41" + os.urandom(20).hex())
    assert addr.startswith("T") and len(addr) == 34
    assert validate_address(addr).family == "tron"
    with pytest.raises(InvalidAddress, match="34 characters"):
        validate_address(addr[:-1])
    bad = addr[:-1] + ("A" if addr[-1] != "A" else "B")
    with pytest.raises(InvalidAddress):
        validate_address(bad, "tron")


def test_solana_rules():
    addr = base58.b58encode(os.urandom(32)).decode()
    assert validate_address(addr, "solana").family == "solana"
    with pytest.raises(InvalidAddress, match="32 bytes"):
        validate_address(base58.b58encode(os.urandom(20)).decode(), "solana")


def test_empty_and_whitespace():
    with pytest.raises(InvalidAddress, match="empty"):
        validate_address("   ")
    with pytest.raises(InvalidAddress, match="whitespace"):
        validate_address("0x11 22")


def test_every_seed_address_validates():
    """Every real labelled address in the committed seed CSV must pass §6.1."""
    in_scope = {"bitcoin", "ethereum", "polygon", "arbitrum", "bsc", "tron", "solana"}
    checked = 0
    with SEED_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["chain"] not in in_scope:
                continue
            hint = "evm" if row["chain"] in {"ethereum", "polygon", "arbitrum", "bsc"} else row["chain"]
            info = validate_address(row["address"], hint)
            assert info.address == row["address"]
            checked += 1
    assert checked > 1000
