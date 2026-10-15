"""Ingest label sources into the database and (optionally) refresh the seed CSV.

Usage (from backend/):
  python scripts/ingest_labels.py --source ofac
  python scripts/ingest_labels.py --source eth_labels
  python scripts/ingest_labels.py --source defillama_cex
  python scripts/ingest_labels.py --source ransomwhere [--file ransomwhere.json]
  python scripts/ingest_labels.py --source all --export-seed
  python scripts/ingest_labels.py --load-seed            # load data/seed/labels_seed.csv

Offline / restricted networks: clone the upstream repository and pass --from-dir:
  git clone --depth 1 --branch lists https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses ofac
  python scripts/ingest_labels.py --source ofac --from-dir ofac
Each run records provenance (label_source_runs) and prints a count summary.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from app.config import SEED_CSV
from app.db import init_db, session_scope
from app.labels.sources import BULK_SOURCES
from app.labels.store import export_seed_csv, label_counts, load_seed_csv, replace_source


def summarise(result) -> str:
    by_chain = Counter(r.chain for r in result.rows)
    by_type = Counter(r.entity_type for r in result.rows)
    entities = len({(r.entity_name, r.entity_type) for r in result.rows})
    lines = [
        f"[{result.source}] labels={len(result.rows)} entities={entities} version={result.version}",
        f"  by chain: {dict(sorted(by_chain.items()))}",
        f"  by type:  {dict(sorted(by_type.items()))}",
    ]
    notes = {k: v for k, v in (result.notes or {}).items() if not isinstance(v, list)}
    if notes:
        lines.append(f"  notes:    {notes}")
    listed = {k: v for k, v in (result.notes or {}).items() if isinstance(v, list)}
    for k, v in listed.items():
        lines.append(f"  {k} ({len(v)}): {', '.join(map(str, v[:30]))}{' ...' if len(v) > 30 else ''}")
    return "\n".join(lines)


def run_source(name: str, from_dir: Path | None, file: Path | None) -> int:
    module = importlib.import_module(f"app.labels.sources.{name}")
    result = module.fetch(from_dir=from_dir, file=file)
    print(summarise(result))
    if not result.rows:
        print(f"  WARNING: {name} produced no labels; existing labels for this source were kept.")
        return 0
    with session_scope() as db:
        run = replace_source(db, result)
        print(f"  stored: {run.label_count} labels, {run.entity_count} entities (run #{run.id})")
        return run.label_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=list(BULK_SOURCES) + ["all"])
    parser.add_argument("--from-dir", type=Path, help="local clone of the upstream repository")
    parser.add_argument("--file", type=Path, help="local copy of the upstream data file")
    parser.add_argument("--export-seed", action="store_true", help=f"write {SEED_CSV.name} from the database")
    parser.add_argument("--load-seed", action="store_true", help=f"load {SEED_CSV.name} into the database")
    args = parser.parse_args(argv)
    init_db()

    if args.load_seed:
        if not SEED_CSV.exists():
            print(f"Seed file not found: {SEED_CSV}", file=sys.stderr)
            return 1
        with session_scope() as db:
            counts = load_seed_csv(db, SEED_CSV)
        print(f"Loaded seed labels: {counts}")

    if args.source:
        names = list(BULK_SOURCES) if args.source == "all" else [args.source]
        if args.source == "all" and (args.from_dir or args.file):
            parser.error("--from-dir/--file apply to a single --source")
        failures = []
        for name in names:
            try:
                run_source(name, args.from_dir, args.file)
            except Exception as exc:  # noqa: BLE001 - report and continue with other sources
                failures.append(name)
                print(f"[{name}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        if failures and len(failures) == len(names):
            return 2

    if args.export_seed:
        with session_scope() as db:
            n = export_seed_csv(db, SEED_CSV)
        print(f"Wrote {n} labels to {SEED_CSV}")

    with session_scope() as db:
        print(f"Database label counts by source: {label_counts(db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
