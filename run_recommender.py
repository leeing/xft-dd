"""Run the local DuckDB-backed product recommender."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog
from dotenv import load_dotenv

from xft.pipeline.recommender import run_recommendation
from xft.pipeline.recommender.batch import BatchOptions, run_recommendation_batch


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="run product recommendation from DuckDB company_profile")
    parser.add_argument("company_name", nargs="?")
    parser.add_argument("--company-list", help="text file with one company name per line")
    parser.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    parser.add_argument("--scenario", help="scenario bundle directory or scenario.yaml")
    parser.add_argument("--products-config")
    parser.add_argument("--dimensions-config")
    parser.add_argument("--output-dir")
    parser.add_argument("--batch-id", help="batch id for --company-list runs")
    parser.add_argument("--batch-output", help="directory that contains batch folders")
    parser.add_argument("--limit", type=int, help="only run the first N companies")
    parser.add_argument("--skip-existing", action="store_true", help="skip companies with an existing result.json")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        default=True,
        help="continue when one company fails in batch mode",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_false",
        dest="continue_on_error",
        help="stop batch after the first failed company",
    )
    parser.add_argument("--no-llm", action="store_true", help="use deterministic fallback matching without calling LLM")
    parser.add_argument(
        "--with-web-evidence",
        action="store_true",
        help="merge cached DuckDB web_evidence into matching",
    )
    parser.add_argument("--with-web", action="store_true", help="search Web when cached web_evidence is missing")
    parser.add_argument("--refresh-web", action="store_true", help="ignore cached web_evidence and search Web again")
    parser.add_argument("--web-config")
    parser.add_argument("--web-extract-llm-config")
    parser.add_argument("--web-providers", help="comma-separated web provider names")
    parser.add_argument("--no-web-fetch", action="store_true", help="do not crawl pages during --with-web")
    parser.add_argument("--no-web-llm-extraction", action="store_true", help="use fallback Web evidence extraction")
    parser.add_argument("--verbose", action="store_true", help="show detailed structlog output")
    return parser.parse_args()


def _load_company_names(args: argparse.Namespace) -> list[str]:
    names: list[str] = []
    if args.company_name:
        names.append(str(args.company_name).strip())
    if args.company_list:
        path = Path(args.company_list)
        names.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    names = [name for name in names if name]
    if not names:
        msg = "company_name or --company-list is required\n"
        raise ValueError(msg)
    return names


async def _main() -> int:  # noqa: C901
    load_dotenv()
    args = _parse_args()

    if not args.verbose:
        import logging
        logging.basicConfig(level=logging.CRITICAL)
        structlog.configure(
            processors=[structlog.dev.ConsoleRenderer()],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    try:
        company_names = _load_company_names(args)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if args.company_list or len(company_names) > 1:
        batch_result = await run_recommendation_batch(
            company_names=company_names,
            batch_id=args.batch_id,
            batch_output=args.batch_output,
            limit=args.limit,
            skip_existing=args.skip_existing,
            options=BatchOptions(
                warehouse_db=args.warehouse,
                scenario_path=args.scenario,
                products_config_path=args.products_config,
                dimensions_config_path=args.dimensions_config,
                use_llm=not args.no_llm,
                use_web_evidence=args.with_web_evidence,
                with_web=args.with_web,
                refresh_web=args.refresh_web,
                web_config_path=args.web_config,
                web_extract_llm_config_path=args.web_extract_llm_config,
                web_providers=_csv(args.web_providers),
                web_fetch_pages=False if args.no_web_fetch else None,
                web_use_llm_extraction=not args.no_web_llm_extraction,
                continue_on_error=args.continue_on_error,
            ),
        )
        for row in batch_result.rows:
            sys.stdout.write(f"[{row['status']}] {row['company_name']} -> {row['output_dir']}\n")
            if row.get("report_path"):
                sys.stdout.write(f"  report: {row['report_path']}\n")
            if row.get("result_path"):
                sys.stdout.write(f"  result: {row['result_path']}\n")
            if row.get("error"):
                sys.stderr.write(f"  error: {row['error']}\n")
        sys.stdout.write(f"batch_dir: {batch_result.batch_dir}\n")
        sys.stdout.write(f"batch_summary_json: {batch_result.summary_json}\n")
        sys.stdout.write(f"batch_summary_csv: {batch_result.summary_csv}\n")
        sys.stdout.write(f"batch_quality_report: {batch_result.quality_md}\n")
        return 0 if batch_result.status in ("success", "partial") else 1

    exit_code = 0
    for company_name in company_names:
        single_result = await run_recommendation(
            company_name=company_name,
            warehouse_db=args.warehouse,
            scenario_path=args.scenario,
            products_config_path=args.products_config,
            dimensions_config_path=args.dimensions_config,
            output_dir=args.output_dir,
            use_llm=not args.no_llm,
            use_web_evidence=args.with_web_evidence,
            with_web=args.with_web,
            refresh_web=args.refresh_web,
            web_config_path=args.web_config,
            web_extract_llm_config_path=args.web_extract_llm_config,
            web_providers=_csv(args.web_providers),
            web_fetch_pages=False if args.no_web_fetch else None,
            web_use_llm_extraction=not args.no_web_llm_extraction,
        )
        sys.stdout.write(f"[{single_result.status}] {single_result.company_name} -> {single_result.output_dir}\n")
        if single_result.report_path:
            sys.stdout.write(f"  report: {single_result.report_path}\n")
        if single_result.result_path:
            sys.stdout.write(f"  result: {single_result.result_path}\n")
        if single_result.error:
            sys.stderr.write(f"  error: {single_result.error}\n")
        if single_result.status not in ("success", "partial"):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
