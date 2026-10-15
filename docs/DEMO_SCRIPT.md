# Demo script (5 minutes, stakeholders)

**Before the demo** (once, on a machine with internet and API keys):

```
cd backend
python scripts/build_ground_truth.py      # finds the five real demo cases
python scripts/record_fixtures.py         # records them; writes docs/DEMO_CASES.md
```

**Demo machine** (offline is fine): set `DATA_MODE=replay` in `backend/.env`, then start the
backend (`python scripts/seed_users.py && uvicorn app.main:app`) and the frontend
(`npm run dev`). Keep two browser windows: one signed in as `analyst`, one as `supervisor`.

| # | Step | Where | What to say / show |
|---|---|---|---|
| 1 | **Simulated SAHYOG case arrives** | terminal: `python scripts/simulate_sahyog_case.py --ref SAHYOG-DEMO-0001` | The body is signed with HMAC-SHA256; the response says `"simulated": true`. SAHYOG has no public API, so this adapter is a documented simulation. |
| 2 | **Auto analysis runs** | Dashboard → Cases → the new case (tagged *Simulated*) | One analysis per wallet started automatically; the timeline shows the inbound event and the status moving to REVIEW. |
| 3 | **Nearest VASP with evidence** | click the wallet → result page | Headline: *Nearest VASP · Confidence · Tier · hops · via inferred deposit address*. Attribution tab: factor breakdown R·H·√V·T, every evidence path with tx hashes and explorer links, "What would raise this?". |
| 4 | **Graph** | Graph tab | Suspect (navy, ringed) → hops → inferred deposit address (blue, dashed) → exchange hot wallet (blue). Click a node: labels, source, retrieval time, transfers. |
| 5 | **Risk and typologies** | Risk & typologies tab | Risk level with reasons; any typologies with their evidence transactions. |
| 6 | **Report generated and hash verified** | Generate report → then Sources & integrity → Verify a report | Upload the downloaded PDF: *Verified*. Change one byte (or upload another file): *Failed*. The report carries the SHA-256 of the analysis and of every raw provider response, and a Section 63 BSA certificate template. |
| 7 | **Request drafted** | Draft request (analyst) | Disclosure defaults to Section 94 BNSS; freeze has no default. "Legal provisions to be confirmed by the investigating officer." Routing shows *Manual routing required* unless an ADMIN has recorded SAHYOG onboarding with a source. |
| 8 | **Supervisor approves** | supervisor window → the request | The analyst cannot approve; a supervisor who drafted cannot approve their own draft. |
| 9 | **Simulated dispatch** | Dispatch to SAHYOG | Status *Queued to SAHYOG (simulated)*; the outbox payload and its SHA-256 are stored and audit-logged. |
| 10 | **Watchlist alert** | result page → Watch wallet → *All transfers since the analysis window start*; Alerts → Check now | An urgent alert: "Funds moving toward <VASP> now". In live mode the poller runs every 5 minutes. |
| 11 | **Methodology with measured accuracy** | Methodology | Scoring formula, heuristics with sources, label coverage, limitations, and the evaluation numbers exactly as measured by `eval/run_eval.py`. |

Close with: attribution is an investigative lead, not proof of ownership; the VASP's KYC
response is what establishes who controls the account.
