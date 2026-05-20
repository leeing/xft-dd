"""Batch runner and delivery artifacts for business recommendations."""

from __future__ import annotations

import csv
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from xft.pipeline.recommender.models import RecommendationRunResult
from xft.runtime.artifacts import (
    BatchQualityReport,
    batch_status,
    build_quality_report,
    write_delivery_manifest,
    write_failed_companies,
    write_quality_report,
)
from xft.utils.file_io import read_json, write_json

DEFAULT_BATCH_OUTPUT = "outputs/recommender/xft/batches"

TZ = ZoneInfo("Asia/Shanghai")
SUMMARY_FIELDS = [
    "company_name",
    "status",
    "run_id",
    "output_dir",
    "report_path",
    "result_path",
    "scenario",
    "scenario_name",
    "top_module_id",
    "top_module_name",
    "top_score",
    "recommendation_count",
    "profile_completeness",
    "needs_web_enrichment",
    "local_evidence_count",
    "web_evidence_count",
    "conflict_count",
    "missing_evidence_count",
    "matched_attributes",
    "matched_indicators",
    "acceptance_result",
    "web_search_executed",
    "web_search_reused",
    "web_fetch_executed",
    "web_fetch_reused",
    "web_extraction_executed",
    "web_extraction_reused",
    "llm_calls_total",
    "llm_calls_success",
    "llm_calls_failed",
    "llm_elapsed_seconds",
    "error",
    "elapsed_seconds",
]


class BatchManifest(BaseModel):
    """Manifest for a recommender batch run."""

    batch_id: str
    scenario: str | None = None
    scenario_name: str | None = None
    company_count: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    warehouse_db: str
    options: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class BatchOptions:
    """CLI/options passed to each company recommendation run."""

    warehouse_db: str
    scenario_path: str | None = None
    use_llm: bool = True
    web_config_path: str | None = None
    with_web: bool = False
    refresh_web: bool = False
    web_providers: list[str] | None = None
    llm_debug: bool = False
    llm_concurrency: int = 4
    continue_on_error: bool = True


@dataclass(frozen=True)
class BatchRunResult:
    """Public result for one batch."""

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


RecommendationRunner = Callable[..., Awaitable[RecommendationRunResult]]

__all__ = [
    "BatchManifest",
    "BatchOptions",
    "BatchQualityReport",
    "BatchRunResult",
    "build_quality_report",
    "run_recommendation_batch",
    "safe_company_run_id",
    "summarize_run",
    "write_batch_summary",
    "write_delivery_manifest",
    "write_failed_companies",
    "write_quality_report",
]


def make_batch_id() -> str:
    """Return a stable human-readable batch id."""
    return datetime.now(TZ).strftime("batch_%Y%m%d_%H%M%S")


async def run_recommendation_batch(  # noqa: PLR0913
    *,
    company_names: list[str],
    options: BatchOptions,
    batch_id: str | None = None,
    batch_output: str | None = None,
    limit: int | None = None,
    skip_existing: bool = False,
    runner: RecommendationRunner | None = None,
) -> BatchRunResult:
    """Run business recommendations for many companies and write delivery artifacts."""
    from xft.pipeline.recommender import run_recommendation

    runner = runner or run_recommendation
    selected = company_names[:limit] if limit is not None else company_names
    bid = batch_id or make_batch_id()
    batch_dir = Path(batch_output or DEFAULT_BATCH_OUTPUT) / bid
    runs_root = batch_dir / "runs"
    batch_dir.mkdir(parents=True, exist_ok=True)
    runs_root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(TZ)
    manifest = BatchManifest(
        batch_id=bid,
        company_count=len(selected),
        started_at=started_at,
        warehouse_db=options.warehouse_db,
        options=_options_payload(options, limit=limit, skip_existing=skip_existing),
    )
    write_json(batch_dir / "batch_manifest.json", manifest.model_dump(mode="json"))
    (batch_dir / "companies.txt").write_text("\n".join(selected) + ("\n" if selected else ""), encoding="utf-8")

    rows: list[dict[str, Any]] = []
    for company_name in selected:
        run_id = safe_company_run_id(company_name)
        output_dir = runs_root
        result_path = output_dir / run_id / "result.json"
        started = perf_counter()
        if skip_existing and result_path.exists():
            row = _summarize_existing(company_name=company_name, run_id=run_id, result_path=result_path)
            row["elapsed_seconds"] = 0
            rows.append(row)
            continue
        try:
            result = await runner(
                company_name=company_name,
                warehouse_db=options.warehouse_db,
                scenario_path=options.scenario_path,
                output_dir=str(output_dir),
                run_id=run_id,
                use_llm=options.use_llm,
                web_config_path=options.web_config_path,
                with_web=options.with_web,
                refresh_web=options.refresh_web,
                web_providers=options.web_providers,
                llm_debug=options.llm_debug,
                llm_concurrency=options.llm_concurrency,
            )
            row = summarize_run(result)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            row = _failed_row(company_name, run_id, str(output_dir / run_id), str(exc))
            if not options.continue_on_error:
                rows.append(row)
                break
        row["elapsed_seconds"] = round(perf_counter() - started, 3)
        rows.append(row)

    status = batch_status(rows)
    manifest = manifest.model_copy(update={"finished_at": datetime.now(TZ), "status": status})
    write_json(batch_dir / "batch_manifest.json", manifest.model_dump(mode="json"))
    summary_json, summary_csv = write_batch_summary(batch_dir, rows)
    failed_path = write_failed_companies(batch_dir, rows)
    quality_json, quality_md = write_quality_report(
        batch_dir,
        bid,
        rows,
        pipeline="recommender",
        title="批量推荐质量报告",
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
    return BatchRunResult(
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


def summarize_run(result: RecommendationRunResult) -> dict[str, Any]:
    """Build one standardized summary row from a completed recommendation run."""
    output_dir = Path(result.output_dir)
    profile = read_json(output_dir / "profile.json")
    payload = read_json(output_dir / "result.json")
    label_result = read_json(output_dir / "label_result.json")
    selected = label_result.get("selected_module")
    selected_module: dict[str, Any] = selected if isinstance(selected, dict) else {}
    modules = label_result.get("modules")
    module_count = len(modules) if isinstance(modules, list) else 0
    evidence_summary = _evidence_counts(output_dir)
    return _ordered_row(
        {
            "company_name": result.company_name,
            "status": result.status,
            "run_id": result.run_id,
            "output_dir": result.output_dir,
            "report_path": result.report_path or "",
            "result_path": result.result_path or "",
            "scenario": "",
            "scenario_name": "",
            "top_module_id": selected_module.get("module_id", ""),
            "top_module_name": payload.get("Module") or selected_module.get("module_name", ""),
            "top_score": selected_module.get("score", ""),
            "recommendation_count": module_count,
            "profile_completeness": profile.get("profile_completeness", ""),
            "needs_web_enrichment": "",
            "local_evidence_count": evidence_summary.get("local_evidence_count", 0),
            "web_evidence_count": evidence_summary.get("web_evidence_count", 0),
            "conflict_count": evidence_summary.get("conflict_count", 0),
            "missing_evidence_count": evidence_summary.get("missing_evidence_count", 0),
            "matched_attributes": selected_module.get("attributes_number", payload.get("AttributesNumber", 0)),
            "matched_indicators": selected_module.get("indicators_number", payload.get("IndicatorsNumber", 0)),
            "acceptance_result": selected_module.get("acceptance_result", payload.get("AcceptanceResult", "")),
            **_web_metrics(output_dir),
            **_llm_metrics(output_dir),
            "error": result.error or "",
            "elapsed_seconds": 0,
        }
    )


def write_batch_summary(batch_dir: Path, rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    """Write batch_summary.json and batch_summary.csv."""
    json_path = batch_dir / "batch_summary.json"
    csv_path = batch_dir / "batch_summary.csv"
    write_json(json_path, [_ordered_row(row) for row in rows])
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(_ordered_row(row) for row in rows)
    return json_path, csv_path


def safe_company_run_id(company_name: str) -> str:
    """Return a stable directory name for one company in a batch."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in company_name).strip("_")
    return safe[:80] or "company"


def _summarize_existing(*, company_name: str, run_id: str, result_path: Path) -> dict[str, Any]:
    output_dir = result_path.parent
    row = summarize_run(
        RecommendationRunResult(
            company_name=company_name,
            status="success",
            run_id=run_id,
            output_dir=str(output_dir),
            report_path=str(output_dir / "report.md") if (output_dir / "report.md").exists() else None,
            result_path=str(result_path),
        )
    )
    row["status"] = "skipped"
    return row


def _web_metrics(output_dir: Path) -> dict[str, int]:
    """Read web_metrics.json from a run directory if present."""
    path = output_dir / "web_metrics.json"
    if not path.exists():
        return {
            "web_search_executed": 0,
            "web_search_reused": 0,
            "web_fetch_executed": 0,
            "web_fetch_reused": 0,
            "web_extraction_executed": 0,
            "web_extraction_reused": 0,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "web_search_executed": data.get("search_executed", 0),
        "web_search_reused": data.get("search_reused", 0),
        "web_fetch_executed": data.get("fetch_executed", 0),
        "web_fetch_reused": data.get("fetch_reused", 0),
        "web_extraction_executed": data.get("extraction_executed", 0),
        "web_extraction_reused": data.get("extraction_reused", 0),
    }


def _llm_metrics(output_dir: Path) -> dict[str, Any]:
    """Read llm_metrics.json from a run directory if present."""
    path = output_dir / "llm_metrics.json"
    if not path.exists():
        return {
            "llm_calls_total": 0,
            "llm_calls_success": 0,
            "llm_calls_failed": 0,
            "llm_elapsed_seconds": 0,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "llm_calls_total": data.get("total", 0),
        "llm_calls_success": data.get("success", 0),
        "llm_calls_failed": data.get("failed", 0),
        "llm_elapsed_seconds": data.get("elapsed_seconds", 0),
    }


def _evidence_counts(output_dir: Path) -> dict[str, int]:
    """Summarize evidence counts from indicator_evidence.json."""
    path = output_dir / "indicator_evidence.json"
    if not path.exists():
        return {
            "local_evidence_count": 0,
            "web_evidence_count": 0,
            "conflict_count": 0,
            "missing_evidence_count": 0,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = [item for items in value.values() for item in _list(items)] if isinstance(value, dict) else []
    return {
        "local_evidence_count": sum(1 for item in rows if isinstance(item, dict) and item.get("source_type") != "web"),
        "web_evidence_count": sum(1 for item in rows if isinstance(item, dict) and item.get("source_type") == "web"),
        "conflict_count": 0,
        "missing_evidence_count": sum(1 for item in rows if isinstance(item, dict) and not item.get("matched")),
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _failed_row(company_name: str, run_id: str, output_dir: str, error: str) -> dict[str, Any]:
    return _ordered_row(
        {
            "company_name": company_name,
            "status": "failed",
            "run_id": run_id,
            "output_dir": output_dir,
            "report_path": "",
            "result_path": "",
            "scenario": "",
            "scenario_name": "",
            "top_module_id": "",
            "top_module_name": "",
            "top_score": "",
            "recommendation_count": 0,
            "profile_completeness": "",
            "needs_web_enrichment": "",
            "local_evidence_count": 0,
            "web_evidence_count": 0,
            "conflict_count": 0,
            "missing_evidence_count": 0,
            "matched_attributes": 0,
            "matched_indicators": 0,
            "acceptance_result": "",
            "web_search_executed": 0,
            "web_search_reused": 0,
            "web_fetch_executed": 0,
            "web_fetch_reused": 0,
            "web_extraction_executed": 0,
            "web_extraction_reused": 0,
            "llm_calls_total": 0,
            "llm_calls_success": 0,
            "llm_calls_failed": 0,
            "llm_elapsed_seconds": 0,
            "error": error,
            "elapsed_seconds": 0,
        }
    )


def _options_payload(options: BatchOptions, *, limit: int | None, skip_existing: bool) -> dict[str, Any]:
    return {
        "scenario_path": options.scenario_path,
        "use_llm": options.use_llm,
        "web_config_path": options.web_config_path,
        "with_web": options.with_web,
        "refresh_web": options.refresh_web,
        "web_providers": options.web_providers,
        "llm_debug": options.llm_debug,
        "llm_concurrency": options.llm_concurrency,
        "continue_on_error": options.continue_on_error,
        "limit": limit,
        "skip_existing": skip_existing,
    }


def _ordered_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in SUMMARY_FIELDS}
