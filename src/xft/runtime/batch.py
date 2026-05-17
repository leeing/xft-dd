"""Generic batch runner for XFT pipelines."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from xft.runtime.artifacts import (
    batch_status,
    write_delivery_manifest,
    write_failed_companies,
    write_json,
    write_quality_report,
)
from xft.runtime.models import PipelineId, PipelineRunRequest, PipelineRunResult
from xft.runtime.runner import run_pipeline

DEFAULT_RUNTIME_BATCH_OUTPUT = "runtime_runs/batches"
RUNTIME_BATCH_FIELDS = [
    "company_name",
    "pipeline",
    "status",
    "run_id",
    "output_dir",
    "report_path",
    "result_path",
    "artifacts_dir",
    "error",
    "elapsed_seconds",
]


class PipelineBatchRequest(BaseModel):
    """Common request for running many targets through one pipeline."""

    pipeline: PipelineId
    targets: list[str]
    warehouse_db: str = "cache/company_warehouse.duckdb"
    scenario_path: str | None = None
    config_path: str | None = None
    batch_id: str | None = None
    batch_output: str = DEFAULT_RUNTIME_BATCH_OUTPUT
    limit: int | None = None
    continue_on_error: bool = True
    use_llm: bool = True
    use_web: bool = False
    use_web_evidence: bool = False
    refresh_web: bool = False
    only_dimensions: list[str] | None = None
    skip_dimensions: list[str] | None = None
    options: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class PipelineBatchResult:
    """Public result for a generic runtime batch."""

    batch_id: str
    batch_dir: str
    summary_json: str
    summary_csv: str
    quality_json: str
    quality_md: str
    delivery_manifest: str
    failed_companies_path: str
    rows: list[dict[str, Any]]
    status: str


def make_runtime_batch_id() -> str:
    """Return a readable batch id."""
    return datetime.now(UTC).strftime("batch_%Y%m%d_%H%M%S")


async def run_pipeline_batch(request: PipelineBatchRequest) -> PipelineBatchResult:
    """Run many targets through `run_pipeline()` and write common batch artifacts."""
    targets = request.targets[: request.limit] if request.limit is not None else request.targets
    bid = request.batch_id or make_runtime_batch_id()
    batch_dir = Path(request.batch_output) / bid
    runs_root = batch_dir / "runs"
    batch_dir.mkdir(parents=True, exist_ok=True)
    runs_root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    write_json(
        batch_dir / "batch_manifest.json",
        {
            "batch_id": bid,
            "pipeline": request.pipeline,
            "target_count": len(targets),
            "started_at": started_at.isoformat(),
            "status": "running",
            "options": request.model_dump(mode="json", exclude={"targets"}),
        },
    )
    (batch_dir / "companies.txt").write_text("\n".join(targets) + ("\n" if targets else ""), encoding="utf-8")

    rows: list[dict[str, Any]] = []
    for target in targets:
        run_id = safe_runtime_run_id(target)
        started = perf_counter()
        try:
            result = await run_pipeline(_request_for_target(request, target=target, run_id=run_id, runs_root=runs_root))
            row = _row_from_result(result)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            row = _failed_row(request.pipeline, target, run_id, str(exc), runs_root)
            if not request.continue_on_error:
                row["elapsed_seconds"] = round(perf_counter() - started, 3)
                rows.append(row)
                break
        row["elapsed_seconds"] = round(perf_counter() - started, 3)
        rows.append(row)

    status = batch_status(rows)
    write_json(
        batch_dir / "batch_manifest.json",
        {
            "batch_id": bid,
            "pipeline": request.pipeline,
            "target_count": len(targets),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "status": status,
            "options": request.model_dump(mode="json", exclude={"targets"}),
        },
    )
    summary_json, summary_csv = write_runtime_batch_summary(batch_dir, rows)
    failed_path = write_failed_companies(batch_dir, rows)
    quality_json, quality_md = write_quality_report(
        batch_dir,
        bid,
        rows,
        pipeline=request.pipeline,
        title="批量运行质量报告",
    )
    delivery_manifest = write_delivery_manifest(
        batch_dir=batch_dir,
        batch_id=bid,
        rows=rows,
        summary_json=summary_json,
        summary_csv=summary_csv,
        quality_json=quality_json,
        quality_md=quality_md,
        failed_path=failed_path,
    )
    return PipelineBatchResult(
        batch_id=bid,
        batch_dir=str(batch_dir),
        summary_json=str(summary_json),
        summary_csv=str(summary_csv),
        quality_json=str(quality_json),
        quality_md=str(quality_md),
        delivery_manifest=str(delivery_manifest),
        failed_companies_path=str(failed_path),
        rows=rows,
        status=status,
    )


def write_runtime_batch_summary(batch_dir: Path, rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    """Write generic runtime batch summary JSON and CSV."""
    json_path = batch_dir / "batch_summary.json"
    csv_path = batch_dir / "batch_summary.csv"
    ordered = [_ordered_row(row) for row in rows]
    write_json(json_path, ordered)
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RUNTIME_BATCH_FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    return json_path, csv_path


def safe_runtime_run_id(target: str) -> str:
    """Return a stable directory-safe run id for runtime batches."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in target).strip("_")
    return safe[:80] or "target"


def _request_for_target(
    request: PipelineBatchRequest,
    *,
    target: str,
    run_id: str,
    runs_root: Path,
) -> PipelineRunRequest:
    output_dir = str(runs_root if request.pipeline == "recommender" else runs_root / run_id)
    return PipelineRunRequest(
        pipeline=request.pipeline,
        target=target,
        warehouse_db=request.warehouse_db,
        scenario_path=request.scenario_path,
        config_path=request.config_path,
        output_dir=output_dir,
        run_id=run_id,
        use_llm=request.use_llm,
        use_web=request.use_web,
        use_web_evidence=request.use_web_evidence,
        refresh_web=request.refresh_web,
        only_dimensions=request.only_dimensions,
        skip_dimensions=request.skip_dimensions,
        options=request.options,
    )


def _row_from_result(result: PipelineRunResult) -> dict[str, Any]:
    return _ordered_row(
        {
            "company_name": result.target,
            "pipeline": result.pipeline,
            "status": result.status,
            "run_id": result.run_id,
            "output_dir": result.output_dir,
            "report_path": result.report_path or "",
            "result_path": result.result_path or "",
            "artifacts_dir": result.artifacts_dir or "",
            "error": result.error or "",
            "elapsed_seconds": 0,
        }
    )


def _failed_row(pipeline: PipelineId, target: str, run_id: str, error: str, runs_root: Path) -> dict[str, Any]:
    return _ordered_row(
        {
            "company_name": target,
            "pipeline": pipeline,
            "status": "failed",
            "run_id": run_id,
            "output_dir": str(runs_root / run_id),
            "report_path": "",
            "result_path": "",
            "artifacts_dir": "",
            "error": error,
            "elapsed_seconds": 0,
        }
    )


def _ordered_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in RUNTIME_BATCH_FIELDS}
