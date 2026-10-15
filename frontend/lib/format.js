// Formatting helpers. Times are stored in UTC and displayed in IST (Asia/Kolkata).

export function truncate(value, head = 6, tail = 4) {
  if (!value) return "";
  const s = String(value);
  if (s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

const IST_FMT =
  typeof Intl !== "undefined"
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : null;

function toDate(v) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return new Date(v * 1000); // unix seconds
  const s = String(v);
  // Backend ISO strings are UTC and end in "Z"; tolerate a missing suffix.
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function ist(v) {
  const d = toDate(v);
  if (!d || !IST_FMT) return "—";
  return `${IST_FMT.format(d)} IST`;
}

export function utc(v) {
  const d = toDate(v);
  if (!d) return "—";
  return `${d.toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

export function relative(v) {
  const d = toDate(v);
  if (!d) return "—";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function amount(v, maxFrac = 6) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Math.abs(n) >= 1000) return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  if (Math.abs(n) >= 1) return n.toLocaleString("en-IN", { maximumFractionDigits: 4 });
  return n.toLocaleString("en-IN", { maximumFractionDigits: maxFrac });
}

export function usd(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: n >= 100 ? 0 : 2 })}`;
}

export function pct(v, digits = 0) {
  if (v === null || v === undefined) return "—";
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

export function confidence(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(0);
}

export function hops(n) {
  if (n === null || n === undefined) return "—";
  return `${n} hop${n === 1 ? "" : "s"}`;
}

export const STAGES = [
  ["validating", "Validating"],
  ["fetching", "Fetching"],
  ["building_graph", "Building graph"],
  ["inferring_deposits", "Inferring deposits"],
  ["scoring", "Scoring"],
  ["done", "Done"],
];

export const CHAIN_NAMES = {
  bitcoin: "Bitcoin",
  ethereum: "Ethereum",
  polygon: "Polygon",
  arbitrum: "Arbitrum",
  bsc: "BNB Smart Chain",
  tron: "TRON",
  solana: "Solana",
};

export function chainName(c) {
  return CHAIN_NAMES[c] || c || "—";
}

export function titleCase(s) {
  if (!s) return "";
  return String(s)
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

const ENTITY_TYPES = {
  CEX: "CEX",
  CUSTODIAL_WALLET: "Custodial wallet",
  INSTANT_SWAP: "Instant swap",
  OTC_P2P: "OTC / P2P",
  PAYMENT_PROCESSOR: "Payment processor",
  MIXER: "Mixer",
  BRIDGE: "Bridge",
  DEX: "DEX",
  DARKNET_MARKET: "Darknet market",
  RANSOMWARE: "Ransomware",
  SCAM: "Scam",
  SANCTIONED: "Sanctioned",
  GAMBLING: "Gambling",
  UNKNOWN_SERVICE: "Unknown service",
};

export function entityType(t) {
  return ENTITY_TYPES[t] || titleCase(t);
}

const CHAIN_STATUS = {
  active: "Active",
  inactive: "No activity",
  requires_paid_plan: "Requires paid plan",
  key_missing: "API key missing",
  not_recorded: "Not recorded",
  disabled: "Disabled",
  error: "Error",
};

export function chainStatus(s) {
  return CHAIN_STATUS[s] || titleCase(s);
}
