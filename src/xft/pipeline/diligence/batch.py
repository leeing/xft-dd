"""Batch orchestration layer.

Reuses run_company_graph() for each company. Never duplicates single-company logic.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import hashlib
import json
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import structlog

from xft.pipeline.diligence.config import AppConfig, Dimension, validate_dimension_ids
from xft.pipeline.diligence.graph import run_company_graph
from xft.pipeline.diligence.models import BatchRunMeta, CompanyRunResult, RunMeta
from xft.runtime.artifacts import write_delivery_manifest, write_failed_companies, write_json, write_quality_report

log = structlog.get_logger(__name__)

_CONCURRENCY_WARN_THRESHOLD = 30
_CONCURRENCY_HARD_LIMIT = 50


def parse_input_file(file_path: str, *, name_column: str = "name") -> list[str]:
    """Parse .txt or .csv; deduplicate and warn on duplicates (to stderr)."""
    path = Path(file_path)
    names: list[str] = []

    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                val = row.get(name_column, "").strip()
                if val:
                    names.append(val)
    else:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                names.append(stripped)

    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for name in names:
        if name in seen:
            duplicates.append(name)
        else:
            seen.add(name)
            unique.append(name)

    if duplicates:
        sys.stderr.write(f"warning: 重复企业名（已去重）: {', '.join(duplicates)}\n")

    return unique


def _check_concurrency_limit(
    *,
    company_concurrency: int,
    dimension_concurrency: int,
    query_concurrency: int,
    force: bool,
) -> None:
    """Warn if estimated concurrency >30; exit 1 if >50 without force flag."""
    est = company_concurrency * dimension_concurrency * query_concurrency
    if est > _CONCURRENCY_HARD_LIMIT and not force:
        sys.stderr.write(
            f"error: estimated max concurrency {est} > {_CONCURRENCY_HARD_LIMIT}. "
            "Add --force-high-concurrency to proceed.\n"
        )
        sys.exit(1)
    if est > _CONCURRENCY_WARN_THRESHOLD:
        sys.stderr.write(
            f"⚠️ warning: estimated max concurrency {est} (> {_CONCURRENCY_WARN_THRESHOLD}, check API rate limits)\n"
        )
    else:
        sys.stderr.write(f"info: estimated max concurrency {est}\n")


def _target_hash(target: str) -> str:
    return hashlib.sha1(target.encode(), usedforsecurity=False).hexdigest()[:6]


def _make_batch_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    uid = uuid.uuid4().hex[:6]
    return f"{ts}-batch-{uid}"


@dataclasses.dataclass
class _BatchRunContext:
    bd: Path
    batch_id: str
    targets: list[str]
    config: AppConfig
    resume: bool
    batch_dir: str | None


def _init_batch_dir(  # noqa: PLR0913
    targets: list[str],
    config: AppConfig,
    *,
    resume: bool,
    batch_dir: str | None,
) -> _BatchRunContext:
    """Resolve or create batch directory; validate resume consistency."""
    batch_cfg = config.batch
    if resume and batch_dir:
        bd = Path(batch_dir)
        stored = BatchRunMeta.model_validate_json((bd / "batch_meta.json").read_text())
        current_map = {i + 1: t for i, t in enumerate(targets)}
        if current_map != stored.index_target_map:
            sys.stderr.write(
                "error: input file does not match original batch. Use original input file or remove --resume.\n"
            )
            msg = "batch mismatch"
            raise ValueError(msg)
        return _BatchRunContext(
            bd=bd, batch_id=stored.batch_id, targets=targets, config=config, resume=resume, batch_dir=batch_dir
        )

    batch_id = _make_batch_id()
    bd = Path(batch_cfg.batch_runs_dir) / batch_id
    bd.mkdir(parents=True, exist_ok=True)
    return _BatchRunContext(bd=bd, batch_id=batch_id, targets=targets, config=config, resume=resume, batch_dir=None)


def _write_summary(  # noqa: PLR0913
    bd: Path,
    batch_id: str,
    results: list[CompanyRunResult],
    targets: list[str],
    started_at: datetime,
    config_path: str,
    input_file: str,
) -> None:
    """Write batch_meta.json, batch_summary.csv, batch_summary.md, batch_errors.json."""
    success = sum(1 for r in results if r.status == "success")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")

    meta = BatchRunMeta(
        batch_id=batch_id,
        input_file=input_file,
        index_target_map={i + 1: t for i, t in enumerate(targets)},
        total=len(targets),
        success=success,
        partial=partial,
        failed=failed,
        skipped=skipped,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        config_path=config_path,
    )
    (bd / "batch_meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    write_json(bd / "batch_manifest.json", meta.model_dump(mode="json"))

    csv_path = bd / "batch_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "index",
                "target",
                "status",
                "run_id",
                "report_path",
                "required_failed",
                "failed_dimensions",
                "error",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "index": r.index,
                    "target": r.target,
                    "status": r.status,
                    "run_id": r.run_id or "",
                    "report_path": r.report_path or "",
                    "required_failed": r.required_failed,
                    "failed_dimensions": ",".join(r.failed_dimensions),
                    "error": r.error or "",
                }
            )

    md_lines = [
        "# Batch Due Diligence Summary",
        f"\n> Batch ID: {batch_id}",
        f"> Total: {len(targets)} | Success: {success} | Partial: {partial} | Failed: {failed} | Skipped: {skipped}",
        "\n## Company List",
        "\n| # | Company | Status | Required Failed | Report |",
        "|---|---------|--------|----------------|--------|",
    ]
    for r in results:
        report = r.report_path or "--"
        md_lines.append(f"| {r.index} | {r.target} | {r.status} | {'yes' if r.required_failed else 'no'} | {report} |")
    md_path = bd / "batch_summary.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    errors_data = [
        {"index": r.index, "target": r.target, "error": r.error} for r in results if r.status == "failed" and r.error
    ]
    errors_path = bd / "batch_errors.json"
    errors_path.write_text(
        json.dumps(errors_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    rows = [_quality_row(result) for result in results]
    failed_path = write_failed_companies(bd, rows)
    quality_json, quality_md = write_quality_report(bd, batch_id, rows, pipeline="diligence", title="批量尽调质量报告")
    write_delivery_manifest(
        batch_dir=bd,
        batch_id=batch_id,
        rows=rows,
        summary_csv=csv_path,
        summary_md=md_path,
        quality_json=quality_json,
        quality_md=quality_md,
        failed_path=failed_path,
        extra_files=[{"type": "batch_errors", "path": str(errors_path)}],
    )

    sys.stderr.write("--\n")
    sys.stderr.write(f"done: {success} success / {partial} partial / {failed} failed / {skipped} skipped\n")
    sys.stderr.write(f"summary: {bd}/batch_summary.md\n")


def _quality_row(result: CompanyRunResult) -> dict[str, object]:
    return {
        "company_name": result.target,
        "target": result.target,
        "status": result.status,
        "run_id": result.run_id or "",
        "output_dir": result.artifacts_dir or "",
        "report_path": result.report_path or "",
        "result_path": "",
        "required_failed": result.required_failed,
        "failed_dimensions": ",".join(result.failed_dimensions),
        "error": result.error or "",
    }


def _exit_code(results: list[CompanyRunResult]) -> int:
    if any(r.status == "failed" for r in results):
        return 3
    if any(r.required_failed for r in results):
        return 2
    return 0


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


def _dry_run_preview(targets: list[str], dims: list[Dimension], config: AppConfig, *, verbose: bool) -> None:  # noqa: FBT001
    batch_cfg = config.batch
    est = batch_cfg.company_concurrency * config.dimension_concurrency * config.query_concurrency_per_dimension
    sys.stderr.write("batch dry-run preview\n")
    sys.stderr.write("--\n")
    sys.stderr.write(f"  companies: {len(targets)}\n")
    sys.stderr.write(f"  active dimensions: {len(dims)}\n")
    sys.stderr.write(
        f"  company concurrency: {batch_cfg.company_concurrency} | "
        f"dimension concurrency: {config.dimension_concurrency} | "
        f"query concurrency/dim: {config.query_concurrency_per_dimension}\n"
    )
    sys.stderr.write(f"  estimated max concurrency: {est}\n\n")
    sys.stderr.write("  first 5 companies:\n")
    for i, t in enumerate(targets[:5], 1):
        sys.stderr.write(f"    {i}. {t}\n")
    if verbose and targets and dims:
        sys.stderr.write(f"\n  [sample] dimension '{dims[0].name}' queries for '{targets[0]}':\n")
        _write_dimension_query_preview(dims[0], targets[0])
    sys.stderr.write("--\n")


async def _process_one(  # noqa: PLR0913
    *,
    idx: int,
    target: str,
    total: int,
    companies_dir: Path,
    semaphore: asyncio.Semaphore,
    config: AppConfig,
    resume: bool,
    batch_dir: str | None,
    config_path: str = "",
    all_dimension_names: dict[str, str] | None = None,
) -> CompanyRunResult:
    """Process a single company with resume support."""
    th = _target_hash(target)
    company_dir = companies_dir / f"{idx:03d}-{th}"

    if resume and batch_dir:
        report_file = company_dir / "final_report.md"
        meta_file = company_dir / "run_meta.json"
        if report_file.exists() and meta_file.exists():
            try:
                rm = RunMeta.model_validate_json(meta_file.read_text())
                if rm.status in ("success", "partial"):
                    sys.stderr.write(f"  [{idx}/{total}] skip (already done): {target}\n")
                    return CompanyRunResult(index=idx, target=target, status="skipped")
            except (ValueError, KeyError):
                pass

    sys.stderr.write(f"  [{idx}/{total}] {target}\n")
    company_dir.mkdir(parents=True, exist_ok=True)
    batch_cfg = config.batch

    async with semaphore:
        try:
            result = await run_company_graph(
                target=target,
                config=config,
                output_dir=str(company_dir),
                config_path=config_path,
                all_dimension_names=all_dimension_names,
            )
            return result.model_copy(update={"index": idx})
        except (RuntimeError, ValueError, OSError) as exc:
            if not batch_cfg.continue_on_company_error:
                raise
            log.warning("company_failed", target=target, error=str(exc))
            return CompanyRunResult(index=idx, target=target, status="failed", error=str(exc))


async def run_batch(  # noqa: C901, PLR0913, PLR0911, PLR0912, PLR0915
    *,
    input_file: str,
    config: AppConfig,
    config_path: str,
    only: list[str] | None,
    skip: list[str] | None,
    dry_run: bool,
    crawler_mode: bool = False,
    resume: bool,
    batch_dir: str | None,
    force_high_concurrency: bool,
    verbose: bool,
    name_column: str,
) -> int:
    """Run batch due xft for a list of companies."""
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

    targets = parse_input_file(input_file, name_column=name_column)
    if not targets:
        sys.stderr.write("error: input file is empty or contains no valid company names\n")
        return 1

    batch_cfg = config.batch
    _check_concurrency_limit(
        company_concurrency=batch_cfg.company_concurrency,
        dimension_concurrency=config.dimension_concurrency,
        query_concurrency=config.query_concurrency_per_dimension,
        force=force_high_concurrency,
    )

    if dry_run:
        _dry_run_preview(targets, dims, config, verbose=verbose)
        return 0

    if crawler_mode:
        from xft.pipeline.diligence.crawler_mode import CrawlerStats, run_crawler_mode_for_target
        from xft.settings import settings

        if not settings.cache_enabled:
            sys.stderr.write("error: --crawler-mode requires CACHE_ENABLED=true\n")
            return 1
        sys.stderr.write(f"batch crawler mode: {len(targets)} companies, concurrency={batch_cfg.company_concurrency}\n")
        sys.stderr.write("--\n")
        semaphore = asyncio.Semaphore(batch_cfg.company_concurrency)
        total_stats = CrawlerStats()

        async def crawl_one(idx: int, target: str) -> CrawlerStats:
            async with semaphore:
                sys.stderr.write(f"  [{idx}/{len(targets)}] {target}\n")
                return await run_crawler_mode_for_target(target, config)

        crawler_results = await asyncio.gather(
            *(crawl_one(i + 1, t) for i, t in enumerate(targets)),
            return_exceptions=True,
        )
        for i, result in enumerate(crawler_results):
            if isinstance(result, Exception):
                log.error("company_crashed", target=targets[i], error=str(result))
                sys.stderr.write(f"  [{i + 1}/{len(targets)}] {targets[i]} -- CRASHED: {result}\n")
                continue
            total_stats.add(cast(CrawlerStats, result))
        sys.stderr.write("--\n")
        sys.stderr.write(
            "crawler complete: "
            f"targets={total_stats.targets}, queries={total_stats.queries_total}, "
            f"l1_hit={total_stats.l1_hits}, l1_miss={total_stats.l1_misses}, "
            f"search_failed={total_stats.search_failed}, urls={total_stats.urls_considered}, "
            f"full_text={total_stats.full_text_items}\n"
        )
        return 0 if total_stats.search_failed == 0 else 1

    try:
        ctx = _init_batch_dir(targets, config, resume=resume, batch_dir=batch_dir)
    except ValueError:
        return 1

    if not resume and not batch_dir:
        shutil.copy2(input_file, ctx.bd / Path(input_file).name)

    companies_dir = ctx.bd / "companies"
    companies_dir.mkdir(exist_ok=True)

    sys.stderr.write(f"batch run: {len(targets)} companies, concurrency={batch_cfg.company_concurrency}\n")
    sys.stderr.write("--\n")

    started_at = datetime.now(UTC)
    semaphore = asyncio.Semaphore(batch_cfg.company_concurrency)

    results = list(
        await asyncio.gather(
            *[
                _process_one(
                    idx=i + 1,
                    target=t,
                    total=len(targets),
                    companies_dir=companies_dir,
                    semaphore=semaphore,
                    config=config,
                    resume=resume,
                    batch_dir=batch_dir,
                    config_path=config_path,
                    all_dimension_names=all_dimension_names,
                )
                for i, t in enumerate(targets)
            ]
        )
    )

    _write_summary(ctx.bd, ctx.batch_id, results, targets, started_at, config_path, input_file)
    return _exit_code(results)
