# Methodology

Attribution is probabilistic. The system never says a wallet "belongs to" anyone; it reports
a **likely deposit destination**, an **attribution confidence** (0–100) and the **evidence**
(transaction hashes, and labels with a named source and retrieval time). All parameters are in
`backend/config/scoring.yaml`.

## 1. Validation and chain detection (`app/engine/validate.py`)

| Family | Rule |
|---|---|
| Bitcoin | base58check (`1…`, `3…`), or bech32 / bech32m checksum (`bc1…`) implemented from BIP-173 / BIP-350 |
| EVM | `^0x[0-9a-fA-F]{40}$`; queried on every enabled EVM chain in parallel |
| TRON | base58check, version byte 0x41, starts with `T`, 34 characters |
| Solana | base58 that decodes to exactly 32 bytes |

Invalid input is rejected with HTTP 400 and a precise reason.

## 2. Hop-0 checks

1. Is the suspect itself labelled (VASP, mixer, bridge, sanctioned…)?
2. Is the suspect itself an exchange deposit address (sweep test below)? Victims are often
   handed a deposit address directly; this is the strongest result the system can produce.

## 3. Traversal (`app/engine/traversal.py`)

- Graph: NetworkX `MultiDiGraph`; nodes are chain-qualified addresses, edges are transfers
  (tx hash, asset, amount, USD estimate, time).
- Forward BFS to `max_hops` (default 3, maximum 5); backward 2 hops for **funding-source VASPs**.
- Window: from the suspect's first in-scope transfer to +90 days (per-case override).
- Per node: the top 5 counterparties by value plus any counterparty receiving ≥ 10 % of the
  node's outflow. Bitcoin change detection only decides which output to follow.
- Dust (< USD 10) is skipped; zero-value token transfers and tokens with a stablecoin symbol
  but an unknown contract are never followed and are flagged as possible address poisoning.
  Tokens are matched by **contract address** (`config/tokens.yaml`), never by symbol.
- Terminal nodes: labelled VASP, mixer, bridge, DEX or sanctioned address; unlabelled
  addresses with > 5,000 transactions or > 200 counterparties in the window
  ("unlabelled high-activity service (possible VASP)").
- Budget: 150 nodes or 600 API calls; when exhausted the result is marked **partial**.

## 4. Deposit-address inference (EVM, TRON) — `app/engine/deposit_inference.py`

Source: F. Victor, *Address Clustering Heuristics for Ethereum*, Financial Cryptography 2020
(Deposit Address Reuse).

| Signal | Rule | Role |
|---|---|---|
| Sweep | within τ of receiving, D forwards ≥ (1 − α) of that asset amount to an address labelled E; α = 0.01, τ = 3,200 blocks on Ethereum (≈ 10.7 h), 11 h elsewhere | required |
| Gas top-up | an address of E sent native coin to D shortly (≤ 24 h) before a token sweep | corroboration |
| Exclusivity | D's outgoing counterparties are only addresses of E, at most 3 | corroboration |

Reliability of the inferred (tier C) label: sweep only 0.70; one corroboration 0.80; both 0.85.
α is applied as a relative tolerance (the paper uses an absolute ETH difference) so it also
works for stablecoins. Other wallets that sent to the same D are listed as **linked wallets
(shared exchange deposit address)**.

## 5. Bitcoin clustering — `app/engine/btc_clustering.py`

- Common-input ownership (Nakamoto 2008 §10; Meiklejohn et al., *A Fistful of Bitcoins*,
  IMC 2013): if a transaction spends inputs from D and from an exchange-labelled address, D
  joins that exchange's cluster (reliability 0.85).
- Transactions with ≥ 5 equal-value outputs are treated as CoinJoin-like: skipped, and
  flagged as `MIXER_EXPOSURE`.

## 6. Scoring — `app/engine/scoring.py`

For each path p from the suspect to an address labelled with candidate entity E:

```
path_score(p) = R × H(hops) × √V × T
C(E)          = 1 − Π (1 − path_score(p))          shown on a 0–100 scale
```

| Factor | Definition |
|---|---|
| R | reliability of the label reached: tier A 0.95 (exchange proof-of-reserve wallets, OFAC SDN); B 0.80 (Etherscan-derived, Tronscan tags, Ransomwhere); C 0.60–0.85 (runtime inference); D 0.50 (WalletExplorer, analyst labels) |
| H | hop decay {0: 1.00, 1: 1.00, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40} |
| V | proportional (haircut) value share: product over hops of amount sent to the next hop ÷ that node's outflow in the window (Möser, Böhme & Breuker, *Towards Risk Scoring of Bitcoin Transactions*, FC 2014) |
| T | 1.0 if every hop is within 72 h of the previous one; 0.8 within 30 days; otherwise 0.6 |

Tiers: **High** ≥ 75 · **Medium** 50–74 · **Low** 25–49 · **Weak** < 25 (shown, never routable).
**Nearest VASP** = the VASP candidate with the fewest hops among those with confidence ≥ 50;
ties go to the higher confidence. For each of the top 3 candidates the UI and report show the
factor breakdown, every evidence path and a "What would raise this?" note.

Weights change only after an evaluation run; before/after metrics are recorded in
`docs/EVALUATION.md`.

## 7. Typologies — `app/engine/typologies.py`

Rule-based, each with evidence transaction hashes; red-flag framing follows FATF,
*Virtual Assets Red Flag Indicators of Money Laundering and Terrorist Financing* (2020).

| Code | Rule |
|---|---|
| PEEL_CHAIN | ≥ 3 consecutive transactions with exactly 2 outputs, one < 10 % of the input, the other continuing |
| FAN_OUT / FAN_IN | ≥ 10 recipients / senders within 24 h |
| RAPID_PASS_THROUGH | ≥ 90 % of received value leaves within 1 h |
| MIXER_EXPOSURE | path touches a mixer label or a CoinJoin-like transaction |
| CROSS_CHAIN_BREAKPOINT | path reaches a bridge or instant-swap service |
| STRUCTURING | ≥ 5 near-identical (± 2 %) round amounts within 24 h |
| ADDRESS_POISONING | zero-value or dust transfers from look-alike addresses sharing the first 4 and last 4 characters with a real counterparty |

## 8. Wallet risk — `app/engine/risk.py`

Severe: direct sanctioned exposure. High: direct mixer, darknet, ransomware or scam exposure.
Medium: indirect exposure within 2 hops with ≥ 10 % value share, or 2+ typologies. Low:
everything else. Every level is shown with its reasons and evidence.

## 9. Cross-chain

- The same EVM address active on several EVM chains is one key holder; the result shows the
  per-chain status (active, no activity, requires paid plan, key missing).
- Instant-swap services are centralised VASPs and are attribution candidates.
- Decentralised bridges are breakpoints: the report gives the bridge, amount and time and
  recommends follow-up on the destination chain.

## 10. Limitations

- Labels come from public datasets and can be incomplete, stale or wrong. Unverified
  entities are shown as unverified; FIU-IND registration, SAHYOG onboarding and request
  channels are unknown until an ADMIN records them with a source URL.
- Inference is heuristic. Exchanges can reuse, share or rotate addresses; a sweep into an
  exchange's hot wallet can also be a payment processor or an OTC desk using that exchange.
- USD values use the **current** price (CoinGecko), not the price at transfer time; they are
  labelled "approximate, current price".
- Traversal is bounded (hops, top-k counterparties, nodes, API calls) and uses one page of
  history per address on free API tiers; very active wallets can be truncated. Results say
  when they are partial.
- Bridges, DEX swaps and privacy tools break the trail on the source chain.
- The evaluation builds positives with the same sweep heuristic the engine uses, so it
  measures traversal and scoring, not the heuristic's own precision against KYC truth.
- Attribution is an investigative lead, not proof of ownership.
