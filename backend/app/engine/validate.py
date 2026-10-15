"""Address validation and chain detection (SPEC §6.1).

- Bitcoin: base58check (1..., 3...) or bech32 / bech32m (bc1...) per BIP-173 / BIP-350.
- EVM: ^0x[0-9a-fA-F]{40}$ (could be any EVM chain).
- TRON: base58check, starts with T, 34 characters, version byte 0x41.
- Solana: base58 that decodes to exactly 32 bytes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import base58

from app.errors import InvalidAddress

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
B58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")

# ------------------------------------------------------------------ BIP-173/350
BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32_CONST = 1
BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_decode(bech: str) -> tuple[str | None, list[int] | None, str | None, str]:
    """Return (hrp, data, encoding, reason). encoding is 'bech32' or 'bech32m'."""
    if any(ord(x) < 33 or ord(x) > 126 for x in bech):
        return None, None, None, "contains characters outside the printable ASCII range"
    if bech.lower() != bech and bech.upper() != bech:
        return None, None, None, "mixes upper and lower case"
    bech = bech.lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech) or len(bech) > 90:
        return None, None, None, "has an invalid separator position or length"
    if not all(x in BECH32_CHARSET for x in bech[pos + 1 :]):
        bad = sorted({x for x in bech[pos + 1 :] if x not in BECH32_CHARSET})
        return None, None, None, f"contains characters not in the bech32 alphabet: {''.join(bad)}"
    hrp = bech[:pos]
    data = [BECH32_CHARSET.find(x) for x in bech[pos + 1 :]]
    const = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if const == BECH32_CONST:
        enc = "bech32"
    elif const == BECH32M_CONST:
        enc = "bech32m"
    else:
        return None, None, None, "bech32/bech32m checksum is invalid"
    return hrp, data[:-6], enc, ""


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool = True) -> list[int] | None:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def decode_segwit(hrp: str, addr: str) -> tuple[int | None, list[int] | None, str]:
    hrpgot, data, enc, reason = _bech32_decode(addr)
    if hrpgot is None or data is None:
        return None, None, reason
    if hrpgot != hrp:
        return None, None, f"has human-readable part '{hrpgot}', expected '{hrp}'"
    if not data:
        return None, None, "has no witness data"
    decoded = _convertbits(data[1:], 5, 8, False)
    if decoded is None or len(decoded) < 2 or len(decoded) > 40:
        return None, None, "has an invalid witness program length"
    version = data[0]
    if version > 16:
        return None, None, "has an invalid witness version"
    if version == 0 and len(decoded) not in (20, 32):
        return None, None, "is witness v0 but the program is not 20 or 32 bytes"
    if version == 0 and enc != "bech32":
        return None, None, "is witness v0 but uses bech32m (v0 must use bech32, BIP-350)"
    if version != 0 and enc != "bech32m":
        return None, None, f"is witness v{version} but uses bech32 (v1+ must use bech32m, BIP-350)"
    return version, decoded, ""


def encode_segwit(hrp: str, witver: int, witprog: bytes) -> str:
    """Reference encoder (used by tests to round-trip BIP-173/350 vectors)."""
    data = [witver] + (_convertbits(list(witprog), 8, 5) or [])
    const = BECH32_CONST if witver == 0 else BECH32M_CONST
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0] * 6) ^ const
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(BECH32_CHARSET[d] for d in data + checksum)


# ------------------------------------------------------------------ base58check
def _b58check_decode(addr: str) -> tuple[bytes | None, str]:
    bad = sorted({c for c in addr if c not in B58_ALPHABET})
    if bad:
        return None, f"contains characters not in the base58 alphabet: {''.join(bad)}"
    try:
        raw = base58.b58decode(addr)
    except ValueError:
        return None, "is not valid base58"
    if len(raw) < 5:
        return None, "is too short for base58check"
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        return None, "base58check checksum mismatch"
    return payload, ""


def tron_hex_to_base58(hex_addr: str) -> str:
    """Convert a TRON hex address (41... or 0x...) to its T... base58check form."""
    h = hex_addr.lower()
    if h.startswith("0x"):
        h = "41" + h[2:]
    if len(h) == 40:
        h = "41" + h
    payload = bytes.fromhex(h)
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode("ascii")


# ------------------------------------------------------------------ public API
@dataclass
class AddressInfo:
    address: str          # normalised form (EVM lowercase)
    family: str           # bitcoin | evm | tron | solana
    chains: list[str]     # candidate chains to query
    kind: str             # p2pkh | p2sh | p2wpkh | p2wsh | p2tr | segwit | evm | tron | solana
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"address": self.address, "family": self.family, "chains": self.chains, "kind": self.kind, "notes": self.notes}


def _bitcoin(addr: str) -> AddressInfo:
    if addr.lower().startswith("bc1"):
        version, prog, reason = decode_segwit("bc", addr)
        if version is None or prog is None:
            raise InvalidAddress(f"Bitcoin bech32 address {reason}.")
        if version == 0:
            kind = "p2wpkh" if len(prog) == 20 else "p2wsh"
        elif version == 1 and len(prog) == 32:
            kind = "p2tr"
        else:
            kind = "segwit"
        return AddressInfo(addr.lower(), "bitcoin", ["bitcoin"], kind)
    payload, reason = _b58check_decode(addr)
    if payload is None:
        raise InvalidAddress(f"Bitcoin address {reason}.")
    if len(payload) != 21:
        raise InvalidAddress(f"Bitcoin base58 address decodes to {len(payload)} bytes; expected 21.")
    if payload[0] == 0x00:
        return AddressInfo(addr, "bitcoin", ["bitcoin"], "p2pkh")
    if payload[0] == 0x05:
        return AddressInfo(addr, "bitcoin", ["bitcoin"], "p2sh")
    raise InvalidAddress(f"Bitcoin base58 version byte 0x{payload[0]:02x} is not mainnet P2PKH (0x00) or P2SH (0x05).")


def _evm(addr: str, evm_chains: list[str]) -> AddressInfo:
    if not EVM_RE.match(addr):
        body = addr[2:]
        if len(body) != 40:
            raise InvalidAddress(f"EVM address must be 0x followed by 40 hex characters (got {len(body)}).")
        bad = sorted({c for c in body if c not in "0123456789abcdefABCDEF"})
        raise InvalidAddress(f"EVM address contains non-hex characters: {''.join(bad)}.")
    return AddressInfo(addr.lower(), "evm", list(evm_chains), "evm")


def _tron(addr: str) -> AddressInfo:
    if len(addr) != 34:
        raise InvalidAddress(f"TRON address must be 34 characters (got {len(addr)}).")
    payload, reason = _b58check_decode(addr)
    if payload is None:
        raise InvalidAddress(f"TRON address {reason}.")
    if len(payload) != 21 or payload[0] != 0x41:
        raise InvalidAddress("TRON address must decode to 21 bytes starting with version byte 0x41.")
    return AddressInfo(addr, "tron", ["tron"], "tron")


def _solana(addr: str) -> AddressInfo:
    bad = sorted({c for c in addr if c not in B58_ALPHABET})
    if bad:
        raise InvalidAddress(f"Solana address contains characters not in the base58 alphabet: {''.join(bad)}.")
    try:
        raw = base58.b58decode(addr)
    except ValueError as exc:
        raise InvalidAddress("Solana address is not valid base58.") from exc
    if len(raw) != 32:
        raise InvalidAddress(f"Solana address must decode to exactly 32 bytes (got {len(raw)}).")
    return AddressInfo(addr, "solana", ["solana"], "solana")


def _detect(addr: str, evm_chains: list[str]) -> AddressInfo:
    """Detect the family from the address shape, then validate it."""
    if addr[:2].lower() == "0x":
        return _evm(addr, evm_chains)
    if addr.lower().startswith("bc1"):
        return _bitcoin(addr)
    if addr[0] == "T" and len(addr) == 34:
        try:
            return _tron(addr)
        except InvalidAddress as tron_exc:
            try:
                return _solana(addr)
            except InvalidAddress:
                raise tron_exc from None
    if addr[0] in "13" and 25 <= len(addr) <= 35:
        try:
            return _bitcoin(addr)
        except InvalidAddress as btc_exc:
            try:
                return _solana(addr)
            except InvalidAddress:
                raise btc_exc from None
    if addr[0] == "T":
        try:
            return _solana(addr)
        except InvalidAddress:
            raise InvalidAddress(f"TRON address must be 34 characters (got {len(addr)}).") from None
    return _solana(addr)


CHAIN_TO_FAMILY = {
    "bitcoin": "bitcoin",
    "ethereum": "evm",
    "polygon": "evm",
    "arbitrum": "evm",
    "bsc": "evm",
    "evm": "evm",
    "tron": "tron",
    "solana": "solana",
}


def validate_address(address: str, chain_hint: str | None = None, evm_chains: list[str] | None = None) -> AddressInfo:
    """Validate an address and detect its chain family. Raises InvalidAddress with a precise reason."""
    if evm_chains is None:
        from app.config import evm_chains as _evm_chains

        evm_chains = _evm_chains()
    if address is None:
        raise InvalidAddress("Address is empty.")
    addr = address.strip()
    if not addr:
        raise InvalidAddress("Address is empty.")
    if any(c.isspace() for c in addr):
        raise InvalidAddress("Address contains whitespace.")

    hint = (chain_hint or "").strip().lower() or None
    if hint and hint not in CHAIN_TO_FAMILY:
        raise InvalidAddress(f"Unknown chain hint '{chain_hint}'. Use one of: {', '.join(sorted(CHAIN_TO_FAMILY))}.")

    try:
        info = _detect(addr, evm_chains)
    except InvalidAddress as detect_exc:
        if not hint:
            raise
        # With a chain hint, report the precise reason for that chain's format.
        fam = CHAIN_TO_FAMILY[hint]
        if fam == "evm" and addr[:2].lower() != "0x":
            raise InvalidAddress("EVM address must be 0x followed by 40 hex characters.") from detect_exc
        validators = {"evm": lambda a: _evm(a, evm_chains), "bitcoin": _bitcoin, "tron": _tron, "solana": _solana}
        info = validators[fam](addr)

    if hint:
        fam = CHAIN_TO_FAMILY[hint]
        if fam != info.family:
            raise InvalidAddress(f"Address looks like a {info.family} address, not {hint}.")
        if info.family == "evm" and hint != "evm":
            info.chains = [hint]
            info.notes.append(f"Restricted to {hint} by chain hint.")
    return info


def detect_family(address: str) -> str | None:
    """Best-effort family detection without raising (used by the UI chip)."""
    try:
        return validate_address(address).family
    except InvalidAddress:
        return None
