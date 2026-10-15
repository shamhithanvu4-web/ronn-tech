# Requirement traceability

Every requirement mapped to the module, endpoint and screen that satisfies it, with the tests
that exercise it. Status as of M6.

| Requirement | Module(s) | Endpoint(s) | Screen | Tests | Status |
|---|---|---|---|---|---|
| Auto-analyse suspect wallets reported on SAHYOG | `app/integrations/sahyog.py` (HMAC-verified inbound, **simulated**), `app/engine/jobs.py` | `POST /api/integrations/sahyog/cases` | `/cases/[id]` (source *SAHYOG · Simulated*, timeline) | `test_operations.py::test_sahyog_inbound_hmac_and_auto_analysis` | Done |
| Trace paths to the nearest CEX / custodial wallet / VASP receiving direct deposits | `app/engine/traversal.py`, `scoring.py` (`nearest_vasp`), `deposit_inference.py` | `POST /api/analyses`, `GET /api/analyses/{id}` | `/analyze`, `/analyses/[id]` headline and Attribution tab | `test_engine.py`, `test_api_reports.py` | Done |
| Multi-chain: Bitcoin, Ethereum, Tron, BNB Chain, Solana, Polygon, others | `app/providers/bitcoin_esplora.py`, `evm_etherscan.py` (Ethereum, Polygon, Arbitrum; BSC plan-gated), `tron_trongrid.py`, `solana_helius.py` | `GET /api/health` | Cross-chain mapping card; chain chip on `/analyze` | `test_providers.py`, `test_replay_chains.py`, `test_breadth.py` | Done (BSC shows *requires paid plan* on Etherscan's free tier) |
| Identify exchange clusters, hot wallets, deposit wallets, mixers/tumblers, DeFi bridges, cross-chain swap services | `app/labels/` (OFAC, eth-labels, DefiLlama CEX, Ransomwhere; live Tronscan / WalletExplorer / Chainalysis), `deposit_inference.py`, `btc_clustering.py`, `typologies.py` | `GET /api/labels`, `POST /api/labels`, `GET /api/entities` | `/entities`, graph node panel, Risk & typologies tab | `test_labels.py`, `test_engine.py` | Done |
| Integrate blockchain intelligence APIs and graph analytics | `app/providers/*`, `app/labels/sources/*`, `app/engine/graph_builder.py` (NetworkX MultiDiGraph) | `GET /api/analyses/{id}/graph` | Graph tab (Cytoscape) | `test_api_reports.py::test_graph_is_cytoscape` | Done |
| Automated tagging and confidence scoring for suspected VASPs | `app/engine/scoring.py`, `config/scoring.yaml`, tier-C labels persisted by `jobs.py` | `GET /api/analyses/{id}` | Attribution tab (factor breakdown, evidence paths, "What would raise this?") | `test_engine.py::test_scoring_primitives`, `test_multiple_paths_combine` | Done |
| Investigation-ready reports | `app/reports/` (ReportLab PDF + JSON, SHA-256 of analysis and raw responses, Section 63 BSA certificate template) | `GET /api/analyses/{id}/report.pdf`, `report.json`, `GET /api/requests/{id}/pdf`, `POST /api/reports/verify` | Generate report, Sources & integrity tab (verify upload), `/requests/[id]` PDF preview | `test_api_reports.py` (one-byte tamper fails) | Done |
| Route lawful disclosure/freeze requests to the correct VASP via SAHYOG | `app/lawful_requests/service.py` (routing ≥ 50, two-person approval), `app/integrations/sahyog.py` (outbox, **simulated**) | `POST /api/cases/{id}/requests`, `POST /api/requests/{id}/approve`, `/dispatch`, `/response` | `/requests/[id]` | `test_operations.py::test_request_lifecycle_two_person_rule_and_routing`, `test_same_person_cannot_approve` | Done |
| Fund-movement visualization, cross-chain mapping, risk scoring, typologies, high-risk alerting | `graph_builder.py`, `pipeline.py` (EVM mapping, breakpoints), `risk.py`, `typologies.py`, `app/alerts/watcher.py` | `GET /api/analyses/{id}/graph`, `GET|POST /api/watchlist`, `POST /api/watchlist/{id}/check`, `GET /api/alerts` | Graph tab, Risk & typologies tab, `/alerts`, nav badge | `test_engine.py`, `test_breadth.py::test_evm_cross_chain_mapping`, `test_operations.py::test_watchlist_alert_is_urgent_when_funds_reach_a_vasp` | Done |
| Dashboard for LEAs with case-based analytics and reporting | `app/api/dashboard.py`, `app/cases/service.py`, `app/api/cases.py` (CSV batch import) | `GET /api/dashboard/summary`, `GET|POST /api/cases`, `PATCH /api/cases/{id}`, `POST /api/cases/import` | `/dashboard`, `/cases`, `/cases/[id]` | `test_operations.py` (cases, import, dashboard) | Done |
| Scalable architecture for large-volume analysis | `app/db.py` (SQLite → PostgreSQL via `DATABASE_URL`), worker pool, provider cache and rate limits, budgets | — | — | `test_ratelimit.py` | Prototype done; scale path in `docs/ARCHITECTURE.md` |

## Supporting requirements

| Requirement | Module | Tests | Status |
|---|---|---|---|
| Roles ANALYST / SUPERVISOR / ADMIN, JWT (60 min, sessionStorage), bcrypt, rate-limited login, 401 → /login | `app/security.py`, `app/api/auth.py`, `frontend/lib/api.js`, `frontend/lib/auth.js` | `test_auth_audit.py` | Done |
| Hash-chained audit log (logins, analyses, report views/downloads, label changes, request draft/approve/dispatch) | `app/audit.py`, `GET /api/audit/verify` | `test_auth_audit.py::test_audit_chain_detects_tampering`, `test_api_reports.py::test_audit_chain_records_reports` | Done |
| Every raw provider response saved with SHA-256 and retrieval time | `app/providers/recorder.py`, `base.py` | `test_providers.py`, `test_replay_chains.py` | Done |
| live / record / replay data modes | `app/providers/base.py`, `recorder.py`, `scripts/record_fixtures.py` | `test_replay_chains.py`, `test_breadth.py` | Done (real demo fixtures need a run with API keys) |
| Uniform errors `{"detail","code"}` with 400/401/403/502/503 | `app/errors.py`, `app/main.py` | `test_auth_audit.py`, `test_api_reports.py` | Done |
| Evaluation harness and measured accuracy | `scripts/build_ground_truth.py`, `eval/run_eval.py`, `app/engine/groundtruth.py`, `evaluation.py` | `test_eval_groundtruth.py` | Harness done; numbers need a run with API keys |
| PRODUCT_NAME in one backend and one frontend constant | `backend/app/config.py`, `frontend/lib/product.js` | — | Done |
