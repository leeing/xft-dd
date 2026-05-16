"""Run the local DuckDB-backed product recommender."""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from diligence.recommender import run_recommendation


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="run product recommendation from DuckDB company_profile")
    parser.add_argument("company_name")
    parser.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    parser.add_argument("--products-config", default="config/recommender/products.yaml")
    parser.add_argument("--dimensions-config", default="config/recommender/analysis_dimensions.yaml")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-llm", action="store_true", help="use deterministic fallback matching without calling LLM")
    return parser.parse_args()


async def _main() -> int:
    load_dotenv()
    args = _parse_args()
    result = await run_recommendation(
        company_name=args.company_name,
        warehouse_db=args.warehouse,
        products_config_path=args.products_config,
        dimensions_config_path=args.dimensions_config,
        output_dir=args.output_dir,
        use_llm=not args.no_llm,
    )
    sys.stdout.write(f"status: {result.status}\n")
    sys.stdout.write(f"run_id: {result.run_id}\n")
    sys.stdout.write(f"output_dir: {result.output_dir}\n")
    if result.report_path:
        sys.stdout.write(f"report: {result.report_path}\n")
    if result.result_path:
        sys.stdout.write(f"result: {result.result_path}\n")
    if result.error:
        sys.stderr.write(f"error: {result.error}\n")
    return 0 if result.status in ("success", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
