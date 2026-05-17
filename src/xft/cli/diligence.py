"""CLI for the enterprise due diligence pipeline.

Usage:
    uv run xft diligence "企业名"
    uv run xft diligence "企业名" --only ip,tech_cert
    uv run xft diligence "企业名" --skip listing
    uv run xft diligence "企业名" --dry-run
    uv run xft diligence --batch companies.txt
    uv run xft diligence --batch companies.csv --name-column company_name
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog
from dotenv import load_dotenv

from xft.pipeline.diligence.config import AppConfig, Dimension, load_config, validate_dimension_ids
from xft.pipeline.diligence.nodes.init_node import make_run_id

load_dotenv()
log = structlog.get_logger(__name__)


def _normalize_metaso_target(target: str) -> str:
    """Match Metaso query target normalisation used by search_node."""
    return target.replace("(", "（").replace(")", "）")


def _write_dimension_query_preview(dim: Dimension, target: str) -> None:
    sys.stderr.write("    MiniMax Search:\n")
    for q in dim.minimax_queries:
        sys.stderr.write(f"      - {q.replace('{target}', target)}\n")
    if dim.metaso_queries:
        metaso_target = _normalize_metaso_target(target)
        sys.stderr.write(f"    Metaso ({dim.metaso_mode} mode, size={dim.metaso_search_size}):\n")
        for q in dim.metaso_queries:
            sys.stderr.write(f"      - {q.replace('{target}', metaso_target)}\n")


async def run_dry_run(
    target: str,
    config: AppConfig,
    only: list[str] | None,
    skip: list[str] | None,
) -> int:
    """Print dry-run preview without calling search APIs or AI."""
    dims = [d for d in config.dimensions if d.enabled]
    if only:
        if err := validate_dimension_ids(only, config.dimensions, label="--only"):
            sys.stderr.write(f"{err}\n")
            return 1
        dims = [d for d in dims if d.id in only]
    if skip:
        if err := validate_dimension_ids(skip, config.dimensions, label="--skip"):
            sys.stderr.write(f"{err}\n")
            return 1
        dims = [d for d in dims if d.id not in skip]
    if not dims:
        sys.stderr.write("error: no active dimensions after filtering\n")
        return 1

    sys.stderr.write(f"target: {target}\n")
    sys.stderr.write(
        f"active dimensions: {len(dims)}, "
        f"dimension concurrency: {config.dimension_concurrency}, "
        f"query concurrency per dimension: {config.query_concurrency_per_dimension}\n"
    )
    sys.stderr.write("--\n")
    for dim in sorted(dims, key=lambda d: d.order):
        sys.stderr.write(f"  [{dim.name}]\n")
        _write_dimension_query_preview(dim, target)
    sys.stderr.write("--\n")
    sys.stderr.write("dry-run complete, no external calls made\n")
    return 0


async def run_single(
    target: str,
    config_path: str,
    only: list[str] | None,
    skip: list[str] | None,
) -> int:
    """Execute single-company pipeline and return exit code."""
    config = load_config(config_path)
    all_dimension_names = {d.id: d.name for d in config.dimensions if d.enabled}
    dims = [d for d in config.dimensions if d.enabled]
    if only:
        if err := validate_dimension_ids(only, config.dimensions, label="--only"):
            sys.stderr.write(f"{err}\n")
            return 1
        dims = [d for d in dims if d.id in only]
    if skip:
        if err := validate_dimension_ids(skip, config.dimensions, label="--skip"):
            sys.stderr.write(f"{err}\n")
            return 1
        dims = [d for d in dims if d.id not in skip]
    if not dims:
        sys.stderr.write("error: no active dimensions after filtering\n")
        return 1
    config = config.model_copy(update={"dimensions": dims})

    from xft.pipeline.diligence.graph import run_company_graph

    run_id = make_run_id(target)
    output_dir = str(Path(config.runs_dir) / run_id)

    sys.stderr.write(f"target: {target}\n")
    sys.stderr.write(f"run_id: {run_id}\n")
    sys.stderr.write(
        f"active dimensions: {len(dims)}, "
        f"dimension concurrency: {config.dimension_concurrency}, "
        f"query concurrency per dimension: {config.query_concurrency_per_dimension}\n"
    )
    sys.stderr.write("--\n")

    result = await run_company_graph(
        target=target,
        config=config,
        output_dir=output_dir,
        run_id=run_id,
        config_path=config_path,
        all_dimension_names=all_dimension_names,
    )

    if result.required_failed:
        return 2
    if result.status == "failed":
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xft diligence", description="enterprise due diligence tool")
    parser.add_argument("target", nargs="?", default=None, help="target company name")
    parser.add_argument("--config", default="config")
    parser.add_argument("--only", help="run only these dimensions (comma-separated)")
    parser.add_argument("--skip", help="skip these dimensions (comma-separated)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--crawler-mode",
        action="store_true",
        help="build MiniMax/crawl4ai SQL cache only; no Metaso/LLM",
    )
    parser.add_argument("--batch", metavar="INPUT_FILE")
    parser.add_argument("--name-column", default="name")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-dir")
    parser.add_argument("--force-high-concurrency", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


_MAX_TARGET_LEN = 200


def _validate_args(args: argparse.Namespace) -> str | None:
    """Return error message if args are invalid, else None."""
    if args.batch and args.target:
        return "error: --batch and target name cannot be used together"
    if not args.batch:
        if not args.target or not args.target.strip():
            return "error: target company name cannot be empty"
        if len(args.target.strip()) > _MAX_TARGET_LEN:
            return (
                f"error: target company name too long "
                f"(max {_MAX_TARGET_LEN} characters, got {len(args.target.strip())})"
            )
    return None


async def _dispatch(args: argparse.Namespace, only: list[str] | None, skip: list[str] | None) -> int:
    """Dispatch to batch or single-company pipeline."""
    if args.batch:
        from xft.pipeline.diligence.batch import run_batch

        try:
            config = load_config(args.config)
        except (ValueError, FileNotFoundError, KeyError) as exc:
            sys.stderr.write(f"config error: {exc}\n")
            return 1
        return await run_batch(
            input_file=args.batch,
            config=config,
            config_path=args.config,
            only=only,
            skip=skip,
            dry_run=args.dry_run,
            crawler_mode=args.crawler_mode,
            resume=args.resume,
            batch_dir=args.batch_dir,
            force_high_concurrency=args.force_high_concurrency,
            verbose=args.verbose,
            name_column=args.name_column,
        )

    try:
        config = load_config(args.config)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 1

    if args.dry_run:
        return await run_dry_run(target=args.target, config=config, only=only, skip=skip)
    if args.crawler_mode:
        from xft.pipeline.diligence.crawler_mode import run_crawler_mode

        return await run_crawler_mode(target=args.target, config=config, only=only, skip=skip)
    return await run_single(target=args.target, config_path=args.config, only=only, skip=skip)


async def _main_async(argv: list[str] | None = None) -> int:
    """Main async entry point."""
    args = _parse_args(argv)

    err = _validate_args(args)
    if err:
        sys.stderr.write(f"{err}\n")
        return 1

    only = [x.strip() for x in args.only.split(",")] if args.only else None
    skip = [x.strip() for x in args.skip.split(",")] if args.skip else None
    return await _dispatch(args, only, skip)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
