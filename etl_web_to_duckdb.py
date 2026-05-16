"""Load cached Web enrichment artifacts from data/web into DuckDB."""

from __future__ import annotations

import argparse
import sys

from diligence.recommender.web import load_web_cache_to_duckdb


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="load data/web Web enrichment cache into DuckDB")
    parser.add_argument("--input", default="data/web")
    parser.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    parser.add_argument("--rebuild", action="store_true", help="clear web_* tables before loading")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        summary = load_web_cache_to_duckdb(input_root=args.input, warehouse_db=args.warehouse, rebuild=args.rebuild)
    except (OSError, RuntimeError, ValueError) as exc:
        sys.stderr.write(f"ETL failed: {exc}\n")
        return 1
    sys.stdout.write(f"input: {args.input}\n")
    sys.stdout.write(f"warehouse: {args.warehouse}\n")
    sys.stdout.write(f"runs: {summary.runs}\n")
    sys.stdout.write(f"queries: {summary.queries}\n")
    sys.stdout.write(f"results: {summary.results}\n")
    sys.stdout.write(f"evidence: {summary.evidence}\n")
    sys.stdout.write("table_rows:\n")
    for table, count in summary.table_rows.items():
        sys.stdout.write(f"  {table}: {count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

