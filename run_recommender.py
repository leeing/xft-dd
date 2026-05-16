"""Run the local DuckDB-backed product recommender."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from diligence.recommender import run_recommendation


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="run product recommendation from DuckDB company_profile")
    parser.add_argument("company_name", nargs="?")
    parser.add_argument("--company-list", help="text file with one company name per line")
    parser.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    parser.add_argument("--products-config", default="config/recommender/products.yaml")
    parser.add_argument("--dimensions-config", default="config/recommender/analysis_dimensions.yaml")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-llm", action="store_true", help="use deterministic fallback matching without calling LLM")
    parser.add_argument(
        "--with-web-evidence",
        action="store_true",
        help="merge cached DuckDB web_evidence into matching",
    )
    parser.add_argument("--with-web", action="store_true", help="search Web when cached web_evidence is missing")
    parser.add_argument("--refresh-web", action="store_true", help="ignore cached web_evidence and search Web again")
    parser.add_argument("--web-config", default="config/recommender/web_search.yaml")
    parser.add_argument("--web-extract-llm-config", default="config/recommender/web_extract_llm.yaml")
    parser.add_argument("--web-providers", help="comma-separated web provider names")
    parser.add_argument("--no-web-fetch", action="store_true", help="do not crawl pages during --with-web")
    parser.add_argument("--no-web-llm-extraction", action="store_true", help="use fallback Web evidence extraction")
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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _summarize_run(result: Any) -> dict[str, Any]:
    output_dir = Path(result.output_dir)
    profile = _read_json(output_dir / "profile.json")
    payload = _read_json(output_dir / "result.json")
    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), list) else []
    top = recommendations[0] if recommendations and isinstance(recommendations[0], dict) else {}
    return {
        "company_name": result.company_name,
        "status": result.status,
        "run_id": result.run_id,
        "output_dir": result.output_dir,
        "report_path": result.report_path or "",
        "result_path": result.result_path or "",
        "top_module_id": top.get("module_id", ""),
        "top_module_name": top.get("module_name", ""),
        "top_score": top.get("score", ""),
        "profile_completeness": profile.get("profile_completeness", payload.get("profile_completeness", "")),
        "needs_web_enrichment": payload.get("needs_web_enrichment", ""),
        "error": result.error or "",
    }


def _write_batch_summary(rows: list[dict[str, Any]], output_root: str) -> tuple[Path, Path]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    batch_id = datetime.now(UTC).strftime("batch_%Y%m%d_%H%M%S")
    json_path = root / f"{batch_id}_summary.json"
    csv_path = root / f"{batch_id}_summary.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    fieldnames = list(rows[0]) if rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


async def _main() -> int:
    load_dotenv()
    args = _parse_args()
    try:
        company_names = _load_company_names(args)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    rows: list[dict[str, Any]] = []
    exit_code = 0
    for company_name in company_names:
        result = await run_recommendation(
            company_name=company_name,
            warehouse_db=args.warehouse,
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
        rows.append(_summarize_run(result))
        sys.stdout.write(f"[{result.status}] {result.company_name} -> {result.output_dir}\n")
        if result.report_path:
            sys.stdout.write(f"  report: {result.report_path}\n")
        if result.result_path:
            sys.stdout.write(f"  result: {result.result_path}\n")
        if result.error:
            sys.stderr.write(f"  error: {result.error}\n")
        if result.status not in ("success", "partial"):
            exit_code = 1

    if args.company_list or len(rows) > 1:
        summary_root = args.output_dir or "recommendation_runs"
        json_path, csv_path = _write_batch_summary(rows, summary_root)
        sys.stdout.write(f"batch_summary_json: {json_path}\n")
        sys.stdout.write(f"batch_summary_csv: {csv_path}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
