"""Evaluate attribution accuracy against eval/ground_truth.json (SPEC §14).

    cd backend
    DATA_MODE=record python eval/run_eval.py --label baseline   # first run: live data, saved to eval/fixtures
    DATA_MODE=replay python eval/run_eval.py --label baseline   # later runs: offline, same answers

For every positive, the deposit address found during ground-truth building is hidden from
the label store, so the engine has to infer it. Live label lookups are disabled so runs are
reproducible. Analyses run in a separate database (eval/eval.db) and never persist inferred
labels.

Reports top-1 and top-3 accuracy, precision at the High tier, coverage (share of positives
with a candidate >= 50), false-positive rate on negative controls, and p50/p95 runtime,
exactly as measured. Writes eval/results.json, eval/results.md and the results block of
docs/EVALUATION.md; each run is appended to eval/history.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
os.environ.setdefault("RONN_FIXTURES_DIR", str(EVAL_DIR / "fixtures"))
if "--use-app-db" not in sys.argv:
    os.environ["DATABASE_URL"] = f"sqlite:///{(EVAL_DIR / 'eval.db').as_posix()}"
os.environ.setdefault("ENABLE_BACKGROUND_TASKS", "false")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import SEED_CSV, config_sha256, get_settings  # noqa: E402
from app.db import init_db, iso, session_scope, utcnow  # noqa: E402
from app.engine.evaluation import evaluate, markdown, pct  # noqa: E402
from app.labels.store import label_counts, load_seed_csv  # noqa: E402

GT_PATH = EVAL_DIR / "ground_truth.json"
RESULTS_JSON = EVAL_DIR / "results.json"
RESULTS_MD = EVAL_DIR / "results.md"
HISTORY = EVAL_DIR / "history.json"
DOC = BACKEND_DIR.parent / "docs" / "EVALUATION.md"
BEGIN, END = "<!-- EVAL:BEGIN -->", "<!-- EVAL:END -->"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", default="run")
    p.add_argument("--limit", type=int, default=None, help="evaluate only the first N positives and N negatives")
    p.add_argument("--use-app-db", action="store_true", help="use DATABASE_URL from .env instead of eval/eval.db")
    args = p.parse_args(argv)
    if not GT_PATH.exists():
        print(f"{GT_PATH.relative_to(BACKEND_DIR)} not found. Build it first (needs API keys):\n  python scripts/build_ground_truth.py", file=sys.stderr)
        return 2
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    settings = get_settings()
    init_db()
    with session_scope() as db:
        if not label_counts(db):
            load_seed_csv(db, SEED_CSV)
    res = evaluate(gt, args.limit)
    out = {
        "label": args.label,
        "generated_at": iso(utcnow()),
        "data_mode": settings.data_mode,
        "ground_truth_generated_at": gt.get("generated_at"),
        "scoring_config_sha256": config_sha256("scoring.yaml"),
        "note": f"{res['metrics']['positives']} positives from sweep-verified deposit addresses (hidden during the run); "
        f"{res['metrics']['negatives']} label-verified negative controls.",
        **res,
    }
    RESULTS_JSON.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    md = markdown(out)
    RESULTS_MD.write_text("# Evaluation results\n\n" + md + "\n", encoding="utf-8")
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    history.append({k: out[k] for k in ("label", "generated_at", "data_mode", "scoring_config_sha256", "metrics")})
    HISTORY.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    if DOC.exists():
        text = DOC.read_text(encoding="utf-8")
        if BEGIN in text and END in text:
            hist = ["", "### Run history (before/after weight changes)", "", "| Label | When | scoring.yaml | Top-1 | Top-3 | Precision@High | Coverage | FP rate |", "|---|---|---|---|---|---|---|---|"]
            for h in history[-12:]:
                m = h["metrics"]
                hist.append(f"| {h['label']} | {h['generated_at']} | `{h['scoring_config_sha256'][:10]}` | {pct(m['top1_accuracy'])} | {pct(m['top3_accuracy'])} | {pct(m['precision_at_high'])} | {pct(m['coverage'])} | {pct(m['false_positive_rate'])} |")
            block = f"{BEGIN}\n{md}\n" + "\n".join(hist) + f"\n{END}"
            DOC.write_text(text[: text.index(BEGIN)] + block + text[text.index(END) + len(END):], encoding="utf-8")
    print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
