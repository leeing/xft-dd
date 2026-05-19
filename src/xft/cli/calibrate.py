"""CLI for recommendation calibration."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from xft.cli.common import csv
from xft.constants import DEFAULT_SCENARIO, DEFAULT_WAREHOUSE
from xft.runtime.calibration import run_recommendation_calibration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run recommendation rule calibration")
    parser.add_argument("--warehouse", default=DEFAULT_WAREHOUSE)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--company-list", help="text file with one company name per line")
    parser.add_argument("--batch-id")
    parser.add_argument("--batch-output", default="recommendation_runs/calibration")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--labels", help="CSV file with business expected/acceptable modules")
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--with-web", action="store_true")
    parser.add_argument("--refresh-web", action="store_true")
    parser.add_argument("--web-config")
    parser.add_argument("--web-extract-llm-config")
    parser.add_argument("--web-providers", help="comma-separated web provider names")
    parser.add_argument("--no-web-fetch", action="store_true")
    parser.add_argument("--force-web-dimensions", action="store_true")
    parser.add_argument("--no-web-llm-extraction", action="store_true")
    return parser


def _load_company_names(args: argparse.Namespace) -> list[str]:
    if args.company_list:
        path = Path(args.company_list)
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    conn = duckdb.connect(args.warehouse, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT company_name
            FROM company_profile
            WHERE company_name IS NOT NULL AND company_name != ''
            ORDER BY profile_completeness DESC, employee_count DESC NULLS LAST, company_name
            LIMIT ?
            """,
            [args.limit],
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


async def _main_async(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        company_names = _load_company_names(args)
    except (OSError, duckdb.Error) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if not company_names:
        sys.stderr.write("error: no companies selected\n")
        return 2
    try:
        batch, report, json_path, md_path, review_path = await run_recommendation_calibration(
            company_names=company_names,
            warehouse_db=args.warehouse,
            scenario_path=args.scenario,
            batch_id=args.batch_id,
            batch_output=args.batch_output,
            limit=args.limit,
            use_llm=args.with_llm,
            with_web=args.with_web,
            refresh_web=args.refresh_web,
            web_config_path=args.web_config,
            web_extract_llm_config_path=args.web_extract_llm_config,
            web_providers=csv(args.web_providers),
            web_fetch_pages=False if args.no_web_fetch else None,
            web_force_dimensions=args.force_web_dimensions,
            web_use_llm_extraction=not args.no_web_llm_extraction,
            labels_path=args.labels,
        )
    except OSError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write(f"[{batch.status}] calibration batch: {batch.batch_dir}\n")
    sys.stdout.write(f"calibration_json: {json_path}\n")
    sys.stdout.write(f"calibration_md: {md_path}\n")
    sys.stdout.write(f"web_llm_review_samples: {review_path}\n")
    sys.stdout.write(f"companies: {report.company_count}, average_top_score: {report.average_top_score:.1f}\n")
    if report.with_web:
        sys.stdout.write(
            "web: "
            f"coverage={report.web_evidence_coverage:.1%}, "
            f"search={report.web_metrics.get('search_executed', 0)} executed/"
            f"{report.web_metrics.get('search_reused', 0)} reused, "
            f"extraction={report.web_metrics.get('extraction_executed', 0)} executed/"
            f"{report.web_metrics.get('extraction_reused', 0)} reused\n"
        )
    if report.labeled_count:
        sys.stdout.write(
            "labels: "
            f"{report.labeled_count}, "
            f"top1_accuracy: {report.top1_accuracy:.1%}, "
            f"acceptable_accuracy: {report.acceptable_accuracy:.1%}\n"
        )
    if report.issues:
        sys.stdout.write("issues:\n")
        for issue in report.issues:
            sys.stdout.write(f"  - [{issue.severity}] {issue.title}: {issue.detail}\n")
    return 0 if batch.status in ("success", "partial") else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))
