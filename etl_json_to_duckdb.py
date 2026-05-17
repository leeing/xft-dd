"""Build a DuckDB enterprise warehouse from Prophet JSON packages.

Usage:
    uv run python etl_json_to_duckdb.py --input data --output cache/company_warehouse.duckdb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xft.warehouse import load_prophet_data


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="load Prophet enterprise JSON into DuckDB")
    parser.add_argument("--input", default="data", help="Prophet data root directory")
    parser.add_argument("--output", default="cache/company_warehouse.duckdb", help="DuckDB output path")
    parser.add_argument("--append", action="store_true", help="append to existing tables instead of rebuilding")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        summary = load_prophet_data(
            input_root=Path(args.input),
            output_db=Path(args.output),
            rebuild=not args.append,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.stderr.write(f"ETL failed: {exc}\n")
        return 1

    sys.stdout.write(f"input: {args.input}\n")
    sys.stdout.write(f"output: {args.output}\n")
    sys.stdout.write(f"companies: {summary.companies}\n")
    sys.stdout.write(f"raw_json_rows: {summary.raw_json_rows}\n")
    sys.stdout.write("import_status:\n")
    for status, count in sorted(summary.import_status_counts.items()):
        sys.stdout.write(f"  {status}: {count}\n")
    sys.stdout.write("table_rows:\n")
    for table, count in summary.table_rows.items():
        sys.stdout.write(f"  {table}: {count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
