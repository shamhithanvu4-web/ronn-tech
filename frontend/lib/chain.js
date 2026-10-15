// Live chain detection for the address input (a hint only; the backend validates
// checksums authoritatively and returns a precise reason when an address is invalid).

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

export function base58Decode(s) {
  if (!s || /[^1-9A-HJ-NP-Za-km-z]/.test(s)) return null;
  const bytes = [0];
  for (const ch of s) {
    let carry = B58.indexOf(ch);
    for (let i = 0; i < bytes.length; i++) {
      carry += bytes[i] * 58;
      bytes[i] = carry & 0xff;
      carry >>= 8;
    }
    while (carry > 0) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  for (const ch of s) {
    if (ch !== "1") break;
    bytes.push(0);
  }
  return bytes.reverse();
}

const BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function polymod(values) {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const top = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) if ((top >>> i) & 1) chk ^= GEN[i];
  }
  return chk >>> 0;
}

function bech32Valid(addr) {
  const s = addr.toLowerCase();
  if (addr !== s && addr !== addr.toUpperCase()) return false;
  const pos = s.lastIndexOf("1");
  if (pos < 1 || pos + 7 > s.length || s.length > 90) return false;
  const hrp = s.slice(0, pos);
  if (hrp !== "bc") return false;
  const data = [];
  for (const c of s.slice(pos + 1)) {
    const d = BECH32.indexOf(c);
    if (d < 0) return false;
    data.push(d);
  }
  const expanded = [...[...hrp].map((c) => c.charCodeAt(0) >> 5), 0, ...[...hrp].map((c) => c.charCodeAt(0) & 31)];
  const pm = polymod([...expanded, ...data]);
  const witver = data[0];
  // bech32 (v0) constant 1, bech32m (v1+) constant 0x2bc830a3 (BIP-173 / BIP-350)
  return (witver === 0 && pm === 1) || (witver > 0 && pm === 0x2bc830a3);
}

export function detectChain(raw) {
  const a = (raw || "").trim();
  if (!a) return { ok: false, label: "", family: null };
  if (/^0x/i.test(a)) {
    return /^0x[0-9a-fA-F]{40}$/.test(a)
      ? { ok: true, family: "evm", label: "EVM · Ethereum, Polygon, Arbitrum, BSC" }
      : { ok: false, family: "evm", label: "EVM address needs 0x + 40 hex characters" };
  }
  if (/^bc1/i.test(a)) {
    return bech32Valid(a)
      ? { ok: true, family: "bitcoin", label: "Bitcoin · bech32" }
      : { ok: false, family: "bitcoin", label: "Bitcoin bech32 checksum fails" };
  }
  const bytes = base58Decode(a);
  if (a[0] === "T" && a.length === 34 && bytes && bytes.length === 25 && bytes[0] === 0x41) {
    return { ok: true, family: "tron", label: "TRON" };
  }
  if ((a[0] === "1" || a[0] === "3") && bytes && bytes.length === 25 && (bytes[0] === 0x00 || bytes[0] === 0x05)) {
    return { ok: true, family: "bitcoin", label: a[0] === "1" ? "Bitcoin · P2PKH" : "Bitcoin · P2SH" };
  }
  if (bytes && bytes.length === 32) {
    return { ok: true, family: "solana", label: "Solana" };
  }
  return { ok: false, family: null, label: "Unrecognised address format" };
}

export const CHAIN_OPTIONS = [
  ["", "Auto-detect"],
  ["bitcoin", "Bitcoin"],
  ["ethereum", "Ethereum"],
  ["polygon", "Polygon"],
  ["arbitrum", "Arbitrum"],
  ["bsc", "BNB Smart Chain"],
  ["tron", "TRON"],
  ["solana", "Solana"],
];
