# MASTER PROMPT — Ronn Tech · Automated Attribution of Unknown Crypto Wallets to the Nearest VASP
Blockchain intelligence platform for Indian law-enforcement agencies · I4C (CIS Division)

## A. How you must execute this prompt
You are a senior full-stack and blockchain-forensics engineer and the sole builder of this prototype.

1. First action: save this entire prompt, unchanged, as SPEC.md in the project root. Re-read the relevant sections of SPEC.md at the start of every milestone.
2. Write a plan of at most 12 lines. Then list anything in this prompt you believe is wrong, outdated or infeasible, each with a proposed fix. Continue immediately unless an issue truly blocks you.
3. If you can create files and run commands, build milestones M1 → M6 (section 15) in order without stopping. After each milestone:
   - run its checks and fix every failure;
   - commit as "M<n>: <summary>";
   - post a 5-line status: done, checks passed, next, what the user must supply.
   Never start the next milestone with failing checks. Never weaken a test to make it pass.
4. If you cannot create files or run commands:
   - deliver one milestone per reply;
   - give every file complete under a heading with its exact relative path;
   - never write placeholders, pseudo-code or "TODO: implement";
   - end each reply with: Reply "continue" for M<n+1>.
5. Missing API keys never block you. Build and test with replay fixtures and unit tests. List exactly which keys the user must add to backend/.env, with each sign-up URL.
6. PRODUCT_NAME = "Ronn Tech" lives in one backend constant and one frontend constant, so the team can rename it in two places.

## 1. Mission
Law-enforcement officers receive a suspect crypto wallet, often reported on the SAHYOG portal. They need the Virtual Asset Service Provider (exchange, custodial wallet, instant-swap service) that received the funds. Only that VASP holds KYC records and can freeze assets. Today, manual hop-by-hop tracing takes hours or days.

Given an unknown wallet, the system must return within minutes:
1. The nearest VASP that received deposits along the fund path, with a confidence score and the exact on-chain evidence path.
2. A risk classification of the wallet and its flows, with detected laundering typologies.
3. An investigation-ready report, plus a draft lawful request routed to the correct VASP and approved by a human before dispatch.

Non-negotiable principles:
- Attribution is probabilistic.
  - Never say a wallet "belongs to" anyone.
  - Use the terms "Likely deposit destination", "Attribution confidence" and "Evidence".
- Every claim in the UI or a report traces to a transaction hash, or to a label with a named source and retrieval time.
- Never invent data.
  - Never type exchange addresses, transaction hashes, VASP contact details or legal provisions from memory.
  - Labels come only from the ingestion scripts in section 5.
  - Unknown values are null, with verification_status = "unverified".
- Synthetic data is allowed only in unit tests, in files named tests/fixtures/synthetic_*. Demos use recorded real data (section 14).
- SAHYOG has no public API. Its integration is a documented, simulated adapter, labelled "Simulated" everywhere it appears.

## 2. Requirement traceability
Create docs/TRACEABILITY.md mapping every row below to the module, endpoint and screen that satisfies it. Keep it current every milestone.

| Requirement | Met by |
|---|---|
| Auto-analyse suspect wallets reported on SAHYOG | §9 inbound adapter → automatic analysis |
| Trace paths to the nearest CEX / custodial wallet / VASP receiving direct deposits | §6 engine, nearest-VASP rule |
| Multi-chain: Bitcoin, Ethereum, Tron, BNB Chain, Solana, Polygon, others | §4 providers |
| Identify exchange clusters, hot wallets, deposit wallets, mixers/tumblers, DeFi bridges, cross-chain swap services | §5 labels, §6.4, §6.5, §6.9 |
| Integrate blockchain intelligence APIs and graph analytics | §4, §5, NetworkX graph |
| Automated tagging and confidence scoring for suspected VASPs | §6.6 |
| Investigation-ready reports | §10 |
| Route lawful disclosure/freeze requests to the correct VASP via SAHYOG | §9 |
| Fund-movement visualization, cross-chain mapping, risk scoring, typologies, high-risk alerting | §6.7–6.9, §7, §12 |
| Dashboard for LEAs with case-based analytics and reporting | §7, §12 |
| Scalable architecture for large-volume analysis | §3, docs/ARCHITECTURE.md |

## 3. Stack and repository layout
Backend:
- Python 3.11+, FastAPI, uvicorn[standard], httpx (async).
- Pydantic v2 and pydantic-settings.
- SQLAlchemy 2 on SQLite by default; DATABASE_URL switches to PostgreSQL.
- NetworkX; ReportLab for PDFs (not WeasyPrint, which breaks on Windows); PyJWT, bcrypt, base58, PyYAML, python-multipart.
- pytest and pytest-asyncio.
- Ask before adding any other dependency.

Frontend:
- Next.js App Router in JavaScript (no TypeScript).
- package.json: "next": "^16", "react": "^19", "react-dom": "^19", "cytoscape": "^3"; scripts dev, build, start.
- Use cytoscape directly inside a client component, with no React wrapper.
- Plain CSS only: no Tailwind, UI kits or chart libraries (bars are drawn with CSS).
- No middleware or proxy file.

Everything must run on Windows, macOS and Linux.

```
backend/
  requirements.txt  .env.example  .gitignore (.env, data/*.db, evidence/, __pycache__)
  app/main.py  config.py  db.py  security.py  audit.py
  app/models/  app/schemas/  app/api/
  app/providers/   base.py bitcoin_esplora.py evm_etherscan.py tron_trongrid.py solana_helius.py recorder.py ratelimit.py cache.py
  app/labels/      store.py  sources/{ofac,eth_labels,defillama_cex,ransomwhere,tronscan_tags,walletexplorer,chainalysis_sanctions}.py
  app/engine/      validate.py graph_builder.py traversal.py deposit_inference.py btc_clustering.py scoring.py typologies.py risk.py pipeline.py
  app/cases/  app/lawful_requests/  app/integrations/sahyog.py  app/alerts/  app/reports/
  config/          scoring.yaml tokens.yaml chains.yaml legal.yaml
  scripts/         ingest_labels.py record_fixtures.py seed_users.py build_ground_truth.py simulate_sahyog_case.py
  eval/            run_eval.py results.md
  fixtures/        recorded real API responses (replay mode)
  evidence/        raw provider responses per analysis
  data/seed/       labels_seed.csv
  tests/
frontend/
  package.json  .env.local.example (NEXT_PUBLIC_API_URL=http://localhost:8000)
  app/  components/  lib/api.js  lib/format.js  lib/auth.js
docs/
  TRACEABILITY.md ARCHITECTURE.md METHODOLOGY.md DATA_SOURCES.md SAHYOG_INTEGRATION.md EVALUATION.md DEMO_SCRIPT.md
README.md  docker-compose.yml
```

backend/.env.example contains:
ETHERSCAN_API_KEY, TRONGRID_API_KEY, TRONSCAN_API_KEY, HELIUS_API_KEY, CHAINALYSIS_API_KEY, JWT_SECRET, SAHYOG_SHARED_SECRET, DATABASE_URL, DATA_MODE, SEED_ADMIN_PASSWORD, SEED_SUPERVISOR_PASSWORD, SEED_ANALYST_PASSWORD.

## 4. Chains and data providers
Every provider implements:
```python
class ChainProvider(Protocol):
    chain: str
    async def get_transfers(self, address: str, since: int | None, until: int | None, limit: int) -> list[Transfer]: ...
    async def get_tx(self, tx_id: str) -> RawTx: ...   # UTXO chains: full inputs and outputs
    async def status(self) -> ProviderStatus: ...      # available, plan limits, last error
```

Transfer is one row per value movement, with these fields:
chain, tx_hash, block_time (unix UTC), from_addr, to_addr, asset, token_contract (null for native), amount (Decimal as string), usd_estimate (nullable), is_internal, raw_ref.

All providers:
- use async httpx with a 15 s timeout;
- have a per-provider token-bucket rate limiter;
- retry up to 3 times with backoff on 429/5xx;
- cache responses in SQLite (10 min TTL live; permanent in replay);
- save every raw response through recorder.py with its SHA-256 and retrieval time.

config/chains.yaml holds each chain's explorer transaction-URL pattern.

| Chain | Provider | Calls | Notes |
|---|---|---|---|
| Bitcoin | Blockstream Esplora https://blockstream.info/api (fallback https://mempool.space/api) | /address/{a}/txs, /address/{a}/txs/chain/{last_txid}, /tx/{txid} | No key; throttle to 2 req/s |
| Ethereum (chainid 1), Polygon (137), Arbitrum (42161) | Etherscan API V2 https://api.etherscan.io/v2/api — always send chainid and apikey | module=account: txlist, tokentx, txlistinternal with page, offset, sort=desc, startblock, endblock | Free tier is 3 req/s. status "0" with message "No transactions found" means an empty result, not an error |
| BNB Smart Chain (56) | Same Etherscan adapter | Same | Not on Etherscan's free tier. Detect the "Free API access is not supported for this chain" error, show BSC as "requires paid plan" in /api/health and the UI, and never fake BSC data |
| TRON | TronGrid https://api.trongrid.io with header TRON-PRO-API-KEY | /v1/accounts/{a}/transactions/trc20?limit=200&only_confirmed=true&contract_address=… (fingerprint pagination); /v1/accounts/{a}/transactions | USDT-TRC20 is the priority asset for Indian fraud cases |
| Solana | Helius https://api-mainnet.helius-rpc.com/v0/addresses/{a}/transactions?api-key=… | Parsed transfers | Enabled only when HELIUS_API_KEY is set |

EVM addresses:
- An EVM address could be on any EVM chain. Query all enabled EVM chains in parallel and keep those with activity.
- The same address active on several EVM chains is one key holder. Show this as automatic cross-chain mapping.

Assets in scope: each chain's native coin, plus USDT and USDC.
- Filter tokens by contract address from config/tokens.yaml, never by symbol. Fake "USDT" tokens are used in address-poisoning scams.
- Verify each contract on the chain's official explorer token page, and store that URL beside the entry.

USD estimates:
- Stablecoins = 1.0.
- Native coins use CoinGecko's simple price endpoint (current price).
- Label these "approximate, current price" wherever they are shown.

## 5. Label intelligence layer
Tables:
- entities: id, name, entity_type, is_vasp, jurisdiction, fiu_ind_registered (true/false/null), sahyog_onboarded (true/false/null), le_request_channel (nullable), verification_status, verified_source_url, last_verified_at.
  - entity_type is one of: CEX, CUSTODIAL_WALLET, INSTANT_SWAP, OTC_P2P, PAYMENT_PROCESSOR, MIXER, BRIDGE, DEX, DARKNET_MARKET, RANSOMWARE, SCAM, SANCTIONED, GAMBLING, UNKNOWN_SERVICE.
- labels: chain, address, entity_id, role, source, source_url, reliability, retrieved_at, evidence (JSON).
  - address is lowercase for EVM and as-is for other chains.
  - role is one of: HOT, COLD, DEPOSIT, WITHDRAWAL, CONTRACT, OPERATOR, PAYMENT, UNKNOWN.

Reliability tiers (stored on each label and used by scoring):
- A = 0.95:
  - exchange self-published proof-of-reserve wallets (via DefiLlama CEX adapters, which cite each exchange's own disclosure);
  - OFAC SDN addresses (SANCTIONED only).
- B = 0.80: Etherscan-derived labels (dawsbot/eth-labels), Tronscan address tags, Ransomwhere (RANSOMWARE).
- C = 0.60–0.85: labels inferred at runtime by §6.4–6.5, stored with evidence transaction hashes.
- D = 0.50: WalletExplorer clusters (historic Bitcoin data) and analyst-added labels until verified.

Ingestion: scripts/ingest_labels.py --source <name>. Each run records provenance and prints a count summary.
- ofac: the JSON lists on the "lists" branch of github.com/0xB10C/ofac-sanctioned-digital-currency-addresses (all assets).
- eth_labels: the dataset files in github.com/dawsbot/eth-labels, keeping exchange and service categories only.
- defillama_cex:
  - fetch https://api.llama.fi/protocols and keep entries with category "CEX";
  - for each entry's "module", fetch https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/main/projects/<module>;
  - extract addresses per chain key (bitcoin, ethereum, tron, bsc, polygon, arbitrum, solana), validating each with §6.1;
  - source_url = the GitHub file URL; evidence = the protocol's methodology text.
- ransomwhere: ransomwhere.json from the latest Zenodo record linked at ransomwhe.re.
- Live, cached lookups (not bulk): Tronscan account tag, WalletExplorer address lookup, Chainalysis free sanctions API (only when its key is set).

Seed data and the VASP directory:
- Commit the generated data/seed/labels_seed.csv so the app runs without re-ingesting.
- Record each upstream licence in docs/DATA_SOURCES.md.
- Seed entities for the VASPs found in the labels. Leave fiu_ind_registered, sahyog_onboarded and le_request_channel null and unverified. An ADMIN fills them in through the UI, with a source URL.

## 6. Attribution engine
Each stage updates the analysis record's progress.

6.1 Validation and chain detection:
- Bitcoin: base58check (1…, 3…), or bech32/bech32m checksum (bc1…) implemented from BIP-173/BIP-350.
- EVM: ^0x[0-9a-fA-F]{40}$.
- TRON: base58check, starts with T, 34 characters.
- Solana: base58 that decodes to exactly 32 bytes.
- Reject invalid input with a precise reason.

6.2 Hop-0 checks:
- (a) Is the suspect itself labelled?
- (b) Run deposit inference (§6.4) on the suspect itself. Victims are often handed an exchange deposit address directly, and this is the strongest result the system can produce.

6.3 Graph and traversal. Use a NetworkX MultiDiGraph: nodes are addresses; edges are transfers carrying tx hash, asset, amount, usd and time.
- Forward (where funds went): BFS to max_hops (default 3, maximum 5).
- Backward (exchange withdrawals that funded the suspect): 2 hops, reported separately as "Funding-source VASPs".
- Time window: from the suspect's first in-scope transfer to +90 days, configurable per case.
- Expansion per node: follow the top 5 counterparties by value, plus any counterparty receiving ≥10% of the node's outflow.
- Filtering:
  - skip transfers below the dust threshold (default $10);
  - do not follow zero-value token transfers; flag them as possible address poisoning.
- Terminal nodes (not expanded):
  - labelled VASP, MIXER, BRIDGE or SANCTIONED;
  - more than 5,000 transactions or more than 200 distinct counterparties in the window, marked "unlabelled high-activity service (possible VASP)".
- Budget: 150 nodes or 600 API calls per analysis. When exhausted, score what exists and flag the result "partial".

6.4 Deposit-address inference (EVM and TRON). An unlabelled node D that received funds on a path is a deposit address of entity E when it passes the sweep test below.
- Sweep (required): within τ of receiving, D forwards at least (1 − α) of that asset amount to an address labelled E.
  - α = 0.01.
  - τ = 3,200 blocks on Ethereum (≈10.7 h); use 11 h wall-clock on other chains.
  - These are the Deposit Address Reuse parameters from Victor (2020).
- Gas top-up (corroboration): an address labelled E sent a small native-coin amount to D shortly before a token sweep.
- Exclusivity (corroboration): D's outgoing counterparties are only addresses of E, at most 3 distinct.

Reliability of the inferred label: sweep alone = 0.70; sweep plus one corroboration = 0.80; all three = 0.85.

Store the result as a tier-C label with its evidence hashes. Other wallets that sent to the same D are listed as "linked wallets (shared exchange deposit address)".

6.5 Bitcoin clustering:
- Common-input-ownership: if a transaction spends inputs from D and from an address labelled E, D joins E's cluster (reliability 0.85).
- Skip CoinJoin-like transactions (≥5 equal-value outputs) and flag MIXER_EXPOSURE instead.
- Change-output detection only decides which output to follow. It never assigns labels.

6.6 Scoring (all weights in config/scoring.yaml). For each path p from the suspect to an address labelled with candidate entity E:

  path_score(p) = R × H(hops) × √V × T

- R: reliability of the label reached.
- H: {0: 1.00, 1: 1.00, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40}.
- V: the proportional (haircut) share of the suspect's outflow reaching E along p. It is the product, over hops, of (amount sent to the next hop ÷ that node's total outflow in the window).
- T: 1.0 if every hop happened within 72 h of the previous one; 0.8 if within 30 days; otherwise 0.6.

Candidate confidence: C(E) = 1 − Π(1 − path_score(p)) over all paths to E, displayed on a 0–100 scale.

Confidence tiers:
- High: 75 or above.
- Medium: 50–74.
- Low: 25–49.
- Weak: below 25. Weak candidates are shown but never routable.

Nearest VASP: the is_vasp candidate with the fewest hops among those with confidence ≥50. Ties go to the higher confidence.

For each of the top 3 candidates, show its factor breakdown, every evidence path, and a "What would raise this?" note.

Change weights only after evaluation (§14), and record before/after metrics in docs/EVALUATION.md.

6.7 Typologies (rule-based, each with evidence hashes):
- PEEL_CHAIN: ≥3 consecutive transactions with exactly 2 outputs, one under 10% of the input, the other continuing the chain.
- FAN_OUT: ≥10 recipients within 24 h.
- FAN_IN: ≥10 senders within 24 h.
- RAPID_PASS_THROUGH: ≥90% of received value leaves within 1 h.
- MIXER_EXPOSURE: the path touches a MIXER label or a CoinJoin pattern.
- CROSS_CHAIN_BREAKPOINT: the path reaches a BRIDGE or INSTANT_SWAP.
- STRUCTURING: ≥5 near-identical round amounts within 24 h.
- ADDRESS_POISONING: zero-value or dust transfers from lookalike addresses that share the first 4 and last 4 characters with a real counterparty.

6.8 Wallet risk level (always show the reasons):
- Severe: direct SANCTIONED exposure.
- High: direct exposure to MIXER, DARKNET_MARKET, RANSOMWARE or SCAM.
- Medium: indirect exposure within 2 hops with ≥10% value share, or 2 or more typologies.
- Low: everything else.

6.9 Cross-chain:
- INSTANT_SWAP services are centralised VASPs, so they are attribution candidates.
- Decentralised bridges are breakpoints: report the bridge, amount and time, and recommend follow-up on the destination chain.

## 7. Cases, batch import, watchlist, alerts
Case fields:
- case_no (format RT-YYYY-NNNNNN), title, complaint_ref (e.g. NCRP acknowledgement number), fir_no, agency, assigned_officer.
- crime_type: investment fraud, task scam, sextortion, digital arrest, ransomware, darknet, terror financing, other.
- status: NEW → ANALYZING → REVIEW → REQUEST_DRAFTED → REQUEST_APPROVED → DISPATCHED_SIMULATED → RESPONSE_RECEIVED → CLOSED.
- Links to its wallets, analyses and requests, plus a timeline.

Batch import: a CSV with columns (address, chain_hint, complaint_ref) creates one case with one analysis per wallet. A background queue processes them with concurrency 2.

Watchlist: a background task started in the FastAPI lifespan polls watched wallets every 5 minutes.
- Any new outgoing transfer creates an Alert.
- If the funds reach a labelled VASP within 2 hops, the alert is marked urgent: "Funds moving toward <VASP> now".

## 8. Security, roles, audit
Roles:
- ANALYST: runs analyses and drafts requests.
- SUPERVISOR: approves requests.
- ADMIN: manages users and verifies entities.

scripts/seed_users.py creates admin, supervisor and analyst accounts from the env passwords. It fails with a clear message if a password is missing.

Auth:
- JWT valid for 60 minutes, sent as a Bearer header; the frontend keeps it in sessionStorage.
- bcrypt password hashes; rate-limited login.
- A 401 response redirects the user to /login.

Audit log:
- Records every login, analysis, report view/download, label change, and request draft/approve/dispatch.
- Each row stores the SHA-256 of the previous row plus its own content (a hash chain).
- GET /api/audit/verify checks the chain.

API keys never leave the server. CORS allows only the frontend origin.

## 9. Lawful requests and SAHYOG adapter (simulated)
Request types:
- INFORMATION_DISCLOSURE: KYC, linked bank/UPI accounts, deposit and withdrawal history of the deposit address, login IPs and devices, current balance.
- FREEZE_HOLD.

Legal basis:
- The officer chooses it from config/legal.yaml.
- Disclosure defaults to "Section 94, Bharatiya Nagarik Suraksha Sanhita, 2023". Freeze has no default; the officer must choose.
- The UI shows: "Legal provisions to be confirmed by the investigating officer."

Routing:
- The request goes to the nearest-VASP entity if its confidence is ≥50.
- If that entity's sahyog_onboarded is false or unknown, mark the request "Manual routing required".

Approval and dispatch:
- An ANALYST drafts; a different SUPERVISOR approves.
- Dispatch writes the payload to an outbox table and shows "Queued to SAHYOG (simulated)".

Inbound: POST /api/integrations/sahyog/cases accepts {sahyog_ref, agency, officer, complaint_ref, wallets: [{address, chain_hint}]}.
- It is verified by an HMAC-SHA256 X-Signature header over the raw body, using SAHYOG_SHARED_SECRET.
- It creates a case and runs the analyses automatically.
- scripts/simulate_sahyog_case.py signs and posts a sample case for demos.

docs/SAHYOG_INTEGRATION.md documents both payload schemas, the signature scheme, and a Mermaid sequence diagram.

## 10. Reports and evidence integrity
Each analysis and each request produces a PDF (ReportLab) and a JSON export. Sections:
- Cover: case number, generation time in IST, analyst, report SHA-256.
- Summary: nearest VASP, confidence, tier, hops, recommended action.
- Candidates with their factor breakdowns.
- Evidence paths, per hop: from, to, chain, asset, amount, UTC and IST time, tx hash, explorer link.
- Typologies and risk.
- Methodology and limitations.
- Data sources with retrieval times.
- Integrity: SHA-256 of the canonical analysis JSON and of every raw provider response.
- An electronic-record certificate template modelled on Section 63, Bharatiya Sakshya Adhiniyam, 2023, for the officer to complete.
- A disclaimer that attribution is an investigative lead, not proof of ownership.

POST /api/reports/verify recomputes and compares the hashes of an uploaded report.

Store all times in UTC. Display them in IST (Asia/Kolkata).

## 11. API (prefix /api; Bearer auth except login and the HMAC-signed SAHYOG inbound)
POST /auth/login · GET /auth/me
POST /analyses {address, chain?, case_id?, max_hops?, include_funding?} → {id, status}
GET /analyses/{id} · GET /analyses/{id}/graph (Cytoscape elements) · GET /analyses/{id}/report.pdf · GET /analyses/{id}/report.json
GET|POST /cases · GET|PATCH /cases/{id} · POST /cases/{id}/wallets · POST /cases/import
POST /cases/{id}/requests · POST /requests/{id}/approve · POST /requests/{id}/dispatch · GET /requests/{id}/pdf
GET /entities · PATCH /entities/{id} (ADMIN) · GET /labels?chain=&address= · POST /labels (tier D)
GET|POST /watchlist · DELETE /watchlist/{id} · GET /alerts
GET /dashboard/summary · GET /audit/verify · GET /health (per-provider status and plan limits)
POST /integrations/sahyog/cases · POST /reports/verify

Errors return {"detail": "...", "code": "..."} with these statuses:
- 400 for an invalid address;
- 401/403 for auth failures;
- 502 for an upstream provider failure;
- 503 when a chain needs a paid plan.

## 12. Frontend
Routing rules:
- Pages with dynamic segments read them with useParams().
- Any component calling useSearchParams() must be wrapped in <Suspense>, or next build fails.
- All API calls go through lib/api.js.

Pages:
- /login
- /dashboard:
  - KPI cards: open cases, analyses this week, high-confidence attributions, median time-to-attribution, pending approvals, urgent alerts;
  - top attributed VASPs as CSS bars;
  - recent analyses;
  - alert feed.
- /analyze:
  - address input with a live chain-detection chip;
  - options: max hops, include funding sources;
  - staged progress (Validating → Fetching → Building graph → Inferring deposits → Scoring → Done), polling every 1.5 s.
- /analyses/[id]:
  - headline card, e.g. "Nearest VASP: X · Confidence 87 · High · 1 hop · via inferred deposit address 0x…";
  - tabs:
    - Attribution: candidates, factor breakdown, evidence paths;
    - Graph: Cytoscape; clicking a node shows its labels, sources and transfers;
    - Risk & typologies;
    - Sources & integrity;
  - actions: add to case, generate report, draft request, watch wallet.
- /cases and /cases/[id]: wallets, analyses, requests, timeline.
- /requests/[id]: draft → approve → dispatch, with a PDF preview.
- /entities: the VASP directory with verification status; ADMIN can edit.
- /alerts
- /methodology: how scoring works, data sources, limitations, and the latest evaluation results.

Every page has loading, empty and error states.

Addresses and hashes:
- Truncate as 0x1234…abcd, with the full value on hover and a copy button.
- Explorer links open in a new tab.

Anything simulated carries a small "Simulated" tag.

## 13. Design system
Calm, premium, white and navy. Dense where the data needs it, never decorative.
```css
:root{
  --white:#FFFFFF; --bg:#F6F8FB; --border:#E3E9F2; --muted:#5C6B7F;
  --navy:#0B1F3A; --navy-2:#122A4D; --navy-3:#1E3A5F;
  --blue:#2563EB; --blue-2:#3B82F6; --tint:#EEF3FA;
  --risk-severe:#8B1E2D; --risk-high:#B42318; --risk-medium:#B54708; --risk-low:#475467;
  --node-mixer:#B42318; --node-bridge:#6941C6; --node-unknown:#98A2B3;
  --radius:14px; --max:1280px; --shadow:0 1px 2px rgba(11,31,58,.04);
}
```

Typography: fonts via next/font/google — Inter for the UI, JetBrains Mono for addresses, hashes, amounts and labels.

Page and navigation:
- Body background: a subtle linear gradient from var(--white) to var(--bg).
- Navbar: white with a 1px bottom border; the active link has a 2px blue underline.

Confidence and risk:
- Risk colours appear only as an 8px dot with text, or as an 8%-opacity tint behind a tag.
- Confidence is not risk. Confidence tiers are shown in navy: High solid, Medium outline, Low slate outline, Weak dashed.

Graph:
- The suspect node is navy, larger, with a ring.
- VASP nodes are blue. Inferred deposit addresses are blue with a dashed border.
- Mixer, bridge and unknown nodes use the node tokens.
- Edge width is proportional to log(USD value). Edge labels show amount and asset.

Components:
- Cards: white, 1px var(--border), var(--radius), 24–32px padding.
- Tables: no zebra stripes, 1px row dividers, right-aligned mono numbers with tabular figures.
- Buttons: primary is navy with white text; secondary is white with a navy border. No gradients.

Constraints:
- Light theme only.
- No glow, neon, glassmorphism, heavy shadows, emoji, or animation beyond 150 ms transitions.
- Works from 1280px down to 375px.

## 14. Demo mode, evaluation, documentation
DATA_MODE:
- live (default): calls the real APIs.
- record: saves raw responses to fixtures/<chain>/<sha256(url)>.json.
- replay: serves only from fixtures and works fully offline.

Demo cases come from scripts/build_ground_truth.py, using real addresses only:
1. A direct deposit to an inferred exchange deposit address on Ethereum.
2. A multi-hop USDT-TRC20 path to a labelled exchange.
3. A Bitcoin case resolved by common-input clustering.
4. A path ending at a mixer or bridge breakpoint.
5. An honest no-match.

Record each one and list it in the README with its expected result.

Evaluation (eval/run_eval.py writes eval/results.md, summarised in docs/EVALUATION.md):
- Ground truth:
  - Start from tier A/B exchange hot wallets and find addresses that sweep into them (deposit addresses).
  - The wallets that funded those deposit addresses 1–2 hops upstream are the test suspects; the exchange is the correct answer.
  - Hide the deposit addresses' labels during the run so the engine must infer them.
- Negative controls: active wallets with no labelled VASP within 3 hops.
- Report: top-1 and top-3 accuracy, precision at High tier, coverage (share with a candidate ≥50), false-positive rate on negatives, and p50/p95 runtime.
- Report the numbers exactly as measured.

Docs:
- README.md: overview, quick start, keys and where to get them, demo cases, screenshots.
- ARCHITECTURE.md: Mermaid component and data-flow diagrams, plus the production scale path (PostgreSQL, a job queue, a graph database, self-hosted nodes to remove rate limits).
- METHODOLOGY.md: every heuristic with its source, the scoring formula, and limitations.
- DATA_SOURCES.md: sources, licences, refresh commands.
- SAHYOG_INTEGRATION.md
- EVALUATION.md
- TRACEABILITY.md
- DEMO_SCRIPT.md, a 5-minute stakeholder walkthrough:
  1. Simulated SAHYOG case arrives.
  2. Auto analysis runs.
  3. Nearest VASP is shown with evidence.
  4. Graph.
  5. Risk and typologies.
  6. Report generated and its hash verified.
  7. Request drafted.
  8. Supervisor approves.
  9. Simulated dispatch.
  10. Watchlist alert.
  11. Methodology page with measured accuracy.

## 15. Milestones (each ends with passing checks and a commit)
M1 Foundations
- Build:
  - skeleton, config, DB, auth, seeded users, hash-chained audit log;
  - provider interface with Bitcoin, Ethereum and TRON adapters, rate limiting, caching, record/replay;
  - label schema and ingestion for ofac, eth_labels, defillama_cex and ransomwhere; the seed CSV.
- Checks:
  - pytest covers validators for every chain, adapters in replay mode, and ingestion counts > 0;
  - uvicorn app.main:app starts;
  - /api/health lists the providers.

M2 Engine
- Build: all of section 6, with a unit test per heuristic (synthetic mini-graphs) and one replay test per chain.
- Checks: pytest; python -m app.engine.pipeline <address> prints candidates with factor breakdowns.

M3 API and reports
- Build: the analysis, report and verify endpoints, and evidence hashing.
- Checks: API tests pass; the PDF opens; changing one byte of a report fails verification.

M4 Frontend
- Build: login, the analysis flow, the result page with graph, the methodology page, and the design system.
- Checks: npm run build with zero errors; a walkthrough of a replay demo case.

M5 Operations
- Build: cases, batch import, requests with two-person approval, the SAHYOG adapter in both directions, watchlist and alerts, dashboard.
- Checks:
  - tests for case and request state transitions, HMAC verification and approval rules;
  - the DEMO_SCRIPT flow works end to end.

M6 Breadth and proof
- Build:
  - Polygon, Arbitrum, BSC (plan-gated) and Solana adapters, plus EVM cross-chain mapping;
  - the evaluation harness, recorded demo cases, all docs, and docker-compose.yml.
- Checks: python eval/run_eval.py writes results (when keys exist); docker compose up serves the app.

## 16. Final definition of done
1. Backend: cd backend && pip install -r requirements.txt && python scripts/seed_users.py && uvicorn app.main:app --reload starts cleanly.
2. Frontend: cd frontend && npm install && npm run build && npm run dev serves every route.
3. With DATA_MODE=replay, all five demo cases produce their documented results offline.
4. DEMO_SCRIPT.md can be performed end to end without errors.
5. Every row in TRACEABILITY.md points to working code.
6. No secrets are committed.

When done, reply with:
- run instructions;
- the demo-case table;
- the evaluation metrics (or what's needed to produce them);
- known limitations;
- what the team must verify themselves: legal provisions, VASP contact and SAHYOG-onboarding data, and token contract addresses.
