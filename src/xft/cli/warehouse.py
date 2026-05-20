"""CLI for warehouse ETL tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xft.constants import DEFAULT_WAREHOUSE
from xft.warehouse import load_prophet_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="warehouse commands")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="load Prophet enterprise JSON into DuckDB")
    build.add_argument("--input", default="data", help="Prophet data root directory")
    build.add_argument("--output", default=DEFAULT_WAREHOUSE, help="DuckDB output path")
    build.add_argument("--append", action="store_true", help="append to existing tables instead of rebuilding")
    return parser


def _build(args: argparse.Namespace) -> int:
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        return _build(args)
    return 2
