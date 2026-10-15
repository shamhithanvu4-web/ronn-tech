# Data sources

Labels come only from the ingestion scripts below; nothing is typed from memory. Every label
stores its source, source URL, reliability tier and retrieval time, and every ingestion run is
recorded in `label_source_runs` (source, URL, version/commit, content SHA-256, counts).

## Label sources

| Source | What | Tier (reliability) | Licence | Refresh |
|---|---|---|---|---|
| [0xB10C/ofac-sanctioned-digital-currency-addresses](https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses) (`lists` branch) | OFAC SDN digital-currency addresses, all assets | A (0.95), SANCTIONED only | MIT (LICENSE on the `main` branch, © 2021 0xB10C) | `python scripts/ingest_labels.py --source ofac` |
| [dawsbot/eth-labels](https://github.com/dawsbot/eth-labels) | Etherscan-derived account labels; exchange and service categories (plus scam/phishing/sanctions for risk) | B (0.80) | MIT (© 2022 Dawson Botsford) | `--source eth_labels` |
| [DefiLlama CEX adapters](https://github.com/DefiLlama/DefiLlama-Adapters) via `https://api.llama.fi/protocols` (category CEX) | Exchange proof-of-reserve wallets that exchanges themselves disclose | A (0.95) | No licence file in the repository root at commit `505fe9a`; public repository — confirm terms before redistribution | `--source defillama_cex` |
| [Ransomwhere](https://ransomwhe.re) — `ransomwhere.json` from the latest Zenodo record (e.g. [zenodo.org/records/13999026](https://zenodo.org/records/13999026)) | Ransomware payment addresses | B (0.80), RANSOMWARE | Creative Commons Attribution 4.0 International | `--source ransomwhere [--file ransomwhere.json]` |
| Tronscan account tags (live, cached) | Public TRON address tags for high-activity nodes | B (0.80) | Tronscan API terms | runtime |
| WalletExplorer address lookup (live, cached) | Historic Bitcoin wallet clusters | D (0.50) | WalletExplorer terms; no documented API contract — best effort | runtime |
| Chainalysis sanctions screening API (live, only with a key) | Sanctions screening of the suspect | A (0.95), SANCTIONED | Chainalysis terms | runtime |
| Runtime inference | Deposit addresses (sweep test) and Bitcoin common-input clusters | C (0.60–0.85), with evidence hashes | — | each analysis |
| Analysts (`POST /api/labels`) | Manually added labels | D (0.50) until verified | — | UI / API |

### Committed seed

`backend/data/seed/labels_seed.csv` is committed so the app runs without re-ingesting. The
current seed was built from git checkouts of the upstream repositories (this build
environment could not reach `api.llama.fi` or Zenodo):

| Source | Commit | Labels |
|---|---|---|
| ofac (`lists` branch) | `6ae2472` (2026-10-01) | 1,050 |
| eth_labels | `d9b21ae` (2026-09-25) | 36,593 |
| defillama_cex | `505fe9a` (2026-10-15) | 4,998 |
| ransomwhere | not ingested (Zenodo unreachable from the build environment) | 0 |

Rebuild everything and re-export the seed on a machine with internet access:

```
cd backend
python scripts/ingest_labels.py --source all --export-seed
```

Restricted networks can ingest from local clones, e.g.
`git clone --depth 1 --branch lists https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses ofac`
then `python scripts/ingest_labels.py --source ofac --from-dir ofac`.

## Chain data providers

| Chain | Provider | Notes |
|---|---|---|
| Bitcoin | Blockstream Esplora `https://blockstream.info/api` (fallback `https://mempool.space/api`) | no key; throttled to 2 req/s |
| Ethereum (1), Polygon (137), Arbitrum (42161) | Etherscan API V2 `https://api.etherscan.io/v2/api` | free tier 3 req/s; `chainid` and `apikey` always sent |
| BNB Smart Chain (56) | Etherscan API V2 | "Paid Tier Only" per Etherscan's supported-chains page; detected and shown as *requires paid plan*, never faked |
| TRON | TronGrid `https://api.trongrid.io`, header `TRON-PRO-API-KEY` | USDT-TRC20 is the priority asset |
| Solana | Helius enhanced transactions `https://api-mainnet.helius-rpc.com/v0/addresses/{a}/transactions` | enabled only when `HELIUS_API_KEY` is set (or in replay mode) |
| Native-coin USD | CoinGecko simple price | current price; labelled "approximate, current price" |

## Token contracts

`backend/config/tokens.yaml` lists USDT and USDC per chain **by contract address**, each with
the explorer or issuer page it was checked against (`verified_url`, `verified_at`). Entries
that could not be checked from the build environment are marked `verified: false` with a
`verify_url`; the team must confirm them before relying on them.

## Legal provisions

`backend/config/legal.yaml` offers legal bases to the officer. Only the disclosure default
("Section 94, Bharatiya Nagarik Suraksha Sanhita, 2023") was given in the specification; the
others were located through public secondary sources and are marked unverified. **Legal
provisions to be confirmed by the investigating officer.**
