"""Run configurable Web enrichment and cache the raw artifacts."""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from diligence.recommender.web import run_web_enrichment


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="run Web enrichment for one DuckDB company profile")
    parser.add_argument("company_name")
    parser.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    parser.add_argument("--scenario", help="scenario bundle directory or scenario.yaml")
    parser.add_argument("--web-config")
    parser.add_argument("--web-extract-llm-config")
    parser.add_argument("--dimensions-config")
    parser.add_argument("--output-root")
    parser.add_argument("--only-dimensions", help="comma-separated dimension ids")
    parser.add_argument("--providers", help="comma-separated provider names")
    parser.add_argument("--run-id")
    parser.add_argument("--refresh", action="store_true", help="ignore reusable web cache and create a fresh run")
    parser.add_argument(
        "--reuse-search",
        action="store_true",
        default=True,
        help="reuse cached provider search results",
    )
    parser.add_argument(
        "--no-reuse-search",
        action="store_false",
        dest="reuse_search",
        help="do not reuse cached provider search results",
    )
    parser.add_argument(
        "--refresh-search",
        action="store_true",
        help="force provider search even when query cache exists",
    )
    parser.add_argument("--refresh-fetch", action="store_true", help="reuse search but recrawl fetched pages")
    parser.add_argument(
        "--refresh-extraction",
        action="store_true",
        help="reuse search/fetch but rerun evidence extraction",
    )
    parser.add_argument("--extract-only", action="store_true", help="rerun evidence extraction from a cached web run")
    parser.add_argument("--source-run-id", help="source web_run_id for --extract-only or cache reuse")
    parser.add_argument(
        "--force-dimensions",
        action="store_true",
        help="search even when local JSON already supports a dimension",
    )
    parser.add_argument(
        "--no-llm-extraction",
        action="store_true",
        help="use deterministic evidence extraction fallback",
    )
    parser.add_argument("--no-fetch", action="store_true", help="do not crawl pages; use search snippets only")
    parser.add_argument("--no-etl", action="store_true", help="write data/web cache only, do not load DuckDB")
    return parser.parse_args()


async def _main() -> int:
    load_dotenv()
    args = _parse_args()
    result = await run_web_enrichment(
        company_name=args.company_name,
        warehouse_db=args.warehouse,
        scenario_path=args.scenario,
        web_config_path=args.web_config,
        web_extract_llm_config_path=args.web_extract_llm_config,
        dimensions_config_path=args.dimensions_config,
        output_root=args.output_root,
        only_dimensions=_csv(args.only_dimensions),
        providers=_csv(args.providers),
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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
