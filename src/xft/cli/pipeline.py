"""CLI for the generic pipeline runtime."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from xft.cli.common import csv
from xft.runtime import PipelineRunRequest, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run an XFT scenario pipeline")
    parser.add_argument("pipeline", choices=["recommender", "diligence"])
    parser.add_argument("target", nargs="?", help="target company name")
    parser.add_argument("--company-list", help="text file with one company name per line")
    parser.add_argument("--batch-id")
    parser.add_argument("--batch-output", default="runtime_runs/batches")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--warehouse", default="cache/company_warehouse.duckdb")
    parser.add_argument("--scenario", default=None, help="scenario bundle directory or scenario.yaml")
    parser.add_argument("--write-scenario-resolved", nargs="?", const="")
    parser.add_argument("--config", help="diligence config directory or yaml")
    parser.add_argument("--output-dir")
    parser.add_argument("--run-id")
    parser.add_argument("--only", help="diligence dimensions to run, comma-separated")
    parser.add_argument("--skip", help="diligence dimensions to skip, comma-separated")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--with-web", action="store_true")
    parser.add_argument("--with-web-evidence", action="store_true")
    parser.add_argument("--refresh-web", action="store_true")
    parser.add_argument("--products-config", help="recommender products config")
    parser.add_argument("--dimensions-config", help="recommender dimensions config")
    parser.add_argument("--web-config", help="recommender web search config")
    parser.add_argument("--web-extract-llm-config", help="recommender web extraction LLM config")
    parser.add_argument("--scoring-policy", help="recommender scoring policy config")
    parser.add_argument("--evidence-policy", help="recommender evidence policy config")
    parser.add_argument("--web-providers", help="comma-separated web provider names")
    parser.add_argument("--no-web-fetch", action="store_true")
    parser.add_argument("--no-web-llm-extraction", action="store_true")
    return parser


def _options(args: argparse.Namespace) -> dict[str, object]:
    options = {
        "products_config_path": args.products_config,
        "dimensions_config_path": args.dimensions_config,
        "web_config_path": args.web_config,
        "web_extract_llm_config_path": args.web_extract_llm_config,
        "scoring_policy_path": args.scoring_policy,
        "evidence_policy_path": args.evidence_policy,
        "web_providers": csv(args.web_providers),
        "web_fetch_pages": False if args.no_web_fetch else None,
        "web_use_llm_extraction": not args.no_web_llm_extraction,
    }
    return {key: value for key, value in options.items() if value is not None}


async def _main_async(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.company_list:
        from xft.runtime.batch import PipelineBatchRequest, run_pipeline_batch

        company_names = [
            line.strip() for line in Path(args.company_list).read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        batch = await run_pipeline_batch(
            PipelineBatchRequest(
                pipeline=args.pipeline,
                targets=company_names,
                warehouse_db=args.warehouse,
                scenario_path=args.scenario,
                config_path=args.config,
                batch_id=args.batch_id,
                batch_output=args.batch_output,
                limit=args.limit,
                continue_on_error=not args.stop_on_error,
                use_llm=not args.no_llm,
                use_web=args.with_web,
                use_web_evidence=args.with_web_evidence,
                refresh_web=args.refresh_web,
                only_dimensions=csv(args.only),
                skip_dimensions=csv(args.skip),
                options=_options(args),
            )
        )
        sys.stdout.write(f"[{batch.status}] batch: {batch.batch_dir}\n")
        sys.stdout.write(f"batch_summary_json: {batch.summary_json}\n")
        sys.stdout.write(f"batch_quality_report: {batch.quality_md}\n")
        return 0 if batch.status in ("success", "partial") else 1
    if not args.target:
        sys.stderr.write("error: target or --company-list is required\n")
        return 2
    if args.write_scenario_resolved is not None:
        if not args.scenario:
            sys.stderr.write("error: --write-scenario-resolved requires --scenario\n")
            return 2
        from xft.core.scenario import load_scenario

        scenario = load_scenario(args.scenario)
        if scenario is None:
            sys.stderr.write(f"error: scenario not found: {args.scenario}\n")
            return 2
        if args.pipeline == "recommender":
            from xft.pipeline.recommender.config_loader import load_products_config, write_products_resolved_config

            products_config = load_products_config(args.products_config or args.scenario)
            resolved_path = write_products_resolved_config(
                scenario,
                products_config,
                args.write_scenario_resolved or None,
            )
        else:
            resolved_path = scenario.write_resolved_config(args.write_scenario_resolved or None)
        sys.stdout.write(f"scenario_resolved: {resolved_path}\n")
    result = await run_pipeline(
        PipelineRunRequest(
            pipeline=args.pipeline,
            target=args.target,
            warehouse_db=args.warehouse,
            scenario_path=args.scenario,
            config_path=args.config,
            output_dir=args.output_dir,
            run_id=args.run_id,
            use_llm=not args.no_llm,
            use_web=args.with_web,
            use_web_evidence=args.with_web_evidence,
            refresh_web=args.refresh_web,
            only_dimensions=csv(args.only),
            skip_dimensions=csv(args.skip),
            options=_options(args),
        )
    )
    sys.stdout.write(f"[{result.status}] {result.pipeline}:{result.target} -> {result.output_dir}\n")
    if result.report_path:
        sys.stdout.write(f"  report: {result.report_path}\n")
    if result.result_path:
        sys.stdout.write(f"  result: {result.result_path}\n")
    if result.error:
        sys.stderr.write(f"  error: {result.error}\n")
    return 0 if result.status in ("success", "partial", "skipped") else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))
