# Evaluation

## Method

Ground truth is built from real chain data by `backend/scripts/build_ground_truth.py`
(logic in `app/engine/groundtruth.py`):

1. Sample tier A/B exchange hot wallets from the label store (tier A proof-of-reserve wallets
   first), spread across exchanges.
2. For each hot wallet, take recent unlabelled senders and keep those that pass the sweep test
   into that exchange: these are **deposit addresses**.
3. The wallets that funded a deposit address 1 hop upstream (and their funders, 2 hops
   upstream) are the **test suspects**; the exchange is the correct answer.
4. For Bitcoin, an address co-spent with an exchange-labelled input (common-input ownership)
   and a wallet that paid it form a positive case.
5. **Negative controls**: unlabelled wallets near the sample whose traced 3-hop graph reaches
   no labelled VASP (label-only check; inferred labels do not count).

`backend/eval/run_eval.py` (metrics in `app/engine/evaluation.py`) then analyses every case:

- the deposit address of each positive is **hidden** from the label store, so the engine must
  infer it;
- live label lookups are disabled and inferred labels are not persisted, so runs are
  reproducible; analyses run in a separate database (`eval/eval.db`);
- `DATA_MODE=record` saves every response under `eval/fixtures/`; `DATA_MODE=replay`
  reproduces the run offline.

Metrics: top-1 and top-3 accuracy (expected exchange among VASP candidates), precision at the
High tier (positives whose High nearest VASP is the expected exchange, over all High results
including negatives), coverage (share of positives with a candidate ≥ 50), false-positive rate
on negatives (share with a routable VASP), and p50/p95 runtime. Numbers are reported exactly as
measured; runs that errored are counted separately and excluded from the rates.

Known bias: positives are found with the same sweep heuristic the engine uses, so the
evaluation measures traversal, inference recall and scoring — not the heuristic's precision
against KYC truth, which only a VASP response can establish.

## How to run

```
cd backend
python scripts/build_ground_truth.py                 # needs ETHERSCAN_API_KEY (+ TRONGRID_API_KEY)
DATA_MODE=record python eval/run_eval.py --label baseline
DATA_MODE=replay python eval/run_eval.py --label baseline-replay   # offline check
```

On Windows PowerShell set the mode with `$env:DATA_MODE="record"` before the command.

Change weights in `config/scoring.yaml` only after a baseline run, then run again with a new
`--label`; the history table below records before/after metrics with the SHA-256 of the
scoring file used.

## Results

<!-- EVAL:BEGIN -->
Not run yet. The prototype was built in an environment without access to blockchain APIs,
so no accuracy numbers exist. Run the commands above with API keys set; this block is
replaced with the measured results and the run history.
<!-- EVAL:END -->
