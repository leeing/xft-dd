"""CLI for Web enrichment and Web cache import."""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from xft.cli.common import csv
from xft.web import load_web_cache_to_duckdb, run_web_enrichment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Web enrichment commands")
    sub = parser.add_subparsers(dest="command", required=True)

    enrich = sub.add_parser("enrich", help="run Web enrichment for one company")
    enrich.add_argument("company_name")
    enrich.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    enrich.add_argument("--scenario", default="config/scenarios/sales_recommendation")
    enrich.add_argument("--web-config")
    enrich.add_argument("--web-extract-llm-config")
    enrich.add_argument("--dimensions-config")
    enrich.add_argument("--evidence-policy")
    enrich.add_argument("--output-root")
    enrich.add_argument("--only-dimensions", help="comma-separated dimension ids")
    enrich.add_argument("--providers", help="comma-separated provider names")
    enrich.add_argument("--run-id")
    enrich.add_argument("--refresh", action="store_true")
    enrich.add_argument("--reuse-search", action="store_true", default=True)
    enrich.add_argument("--no-reuse-search", action="store_false", dest="reuse_search")
    enrich.add_argument("--refresh-search", action="store_true")
    enrich.add_argument("--refresh-fetch", action="store_true")
    enrich.add_argument("--refresh-extraction", action="store_true")
    enrich.add_argument("--extract-only", action="store_true")
    enrich.add_argument("--source-run-id")
    enrich.add_argument("--force-dimensions", action="store_true")
    enrich.add_argument("--no-llm-extraction", action="store_true")
    enrich.add_argument("--no-fetch", action="store_true")
    enrich.add_argument("--no-etl", action="store_true")

    import_cmd = sub.add_parser("import", help="load data/web cache into DuckDB")
    import_cmd.add_argument("--input", default="data/web")
    import_cmd.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    import_cmd.add_argument("--rebuild", action="store_true")
    return parser


async def _enrich(args: argparse.Namespace) -> int:
    result = await run_web_enrichment(
        company_name=args.company_name,
        warehouse_db=args.warehouse,
        scenario_path=args.scenario,
        web_config_path=args.web_config,
        web_extract_llm_config_path=args.web_extract_llm_config,
        dimensions_config_path=args.dimensions_config,
        evidence_policy_path=args.evidence_policy,
        output_root=args.output_root,
        only_dimensions=csv(args.only_dimensions),
        providers=csv(args.providers),
        run_id=args.run_id,
        load_to_duckdb=not args.no_etl,
        refresh=args.refresh,
        force_dimensions=args.force_dimensions,
        use_llm_extraction=not args.no_llm_extraction,
        fetch_pages=False if args.no_fetch else None,
        reuse_search=args.reuse_search,
        refresh_search=args.refresh_search,
        refresh_fetch=args.refresh_fetch,
        refresh_extraction=args.refresh_extraction,
        extract_only=args.extract_only,
        source_run_id=args.source_run_id,
    )
    sys.stdout.write(f"status: {result.status}\n")
    sys.stdout.write(f"web_run_id: {result.web_run_id}\n")
    sys.stdout.write(f"output_dir: {result.output_dir}\n")
    sys.stdout.write(f"queries: {result.queries}\n")
    sys.stdout.write(f"results: {result.results}\n")
    sys.stdout.write(f"evidence: {result.evidence}\n")
    sys.stdout.write(f"duckdb_loaded: {result.duckdb_loaded}\n")
    if result.error:
        sys.stderr.write(f"error: {result.error}\n")
    return 0 if result.status in ("success", "partial", "skipped") else 1


def _import(args: argparse.Namespace) -> int:
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


async def _main_async(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "enrich":
        return await _enrich(args)
    if args.command == "import":
        return _import(args)
    return 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))
