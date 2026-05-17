"""Shared batch quality and delivery artifact helpers."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

HIGH_CONFLICT_THRESHOLD = 3
LOW_COMPLETENESS_THRESHOLD = 0.6


class BatchQualityReport(BaseModel):
    """Aggregated quality metrics for a batch run."""

    batch_id: str
    pipeline: str = ""
    company_count: int
    success_count: int = 0
    partial_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    average_profile_completeness: float = 0
    average_top_score: float = 0
    top_modules: list[dict[str, Any]] = Field(default_factory=list)
    high_conflict_companies: list[dict[str, Any]] = Field(default_factory=list)
    low_completeness_companies: list[dict[str, Any]] = Field(default_factory=list)
    failed_companies: list[dict[str, Any]] = Field(default_factory=list)


def build_quality_report(batch_id: str, rows: list[dict[str, Any]], *, pipeline: str = "") -> BatchQualityReport:
    """Aggregate common quality metrics from batch summary rows."""
    statuses = Counter(str(row.get("status") or "") for row in rows)
    module_counts = Counter(str(row.get("top_module_id") or "") for row in rows if row.get("top_module_id"))
    completeness = [_float(row.get("profile_completeness")) for row in rows]
    scores = [_float(row.get("top_score")) for row in rows if row.get("top_score") not in ("", None)]
    return BatchQualityReport(
        batch_id=batch_id,
        pipeline=pipeline,
        company_count=len(rows),
        success_count=statuses.get("success", 0),
        partial_count=statuses.get("partial", 0),
        failed_count=statuses.get("failed", 0),
        skipped_count=statuses.get("skipped", 0),
        average_profile_completeness=_average(completeness),
        average_top_score=_average(scores),
        top_modules=[
            {"module_id": module_id, "count": count}
            for module_id, count in module_counts.most_common()
        ],
        high_conflict_companies=[
            {"company_name": _row_company(row), "conflict_count": row.get("conflict_count", 0)}
            for row in rows
            if _int(row.get("conflict_count")) >= HIGH_CONFLICT_THRESHOLD
        ],
        low_completeness_companies=[
            {"company_name": _row_company(row), "profile_completeness": row.get("profile_completeness", 0)}
            for row in rows
            if row.get("profile_completeness") not in ("", None)
            and _float(row.get("profile_completeness")) < LOW_COMPLETENESS_THRESHOLD
        ],
        failed_companies=[
            {"company_name": _row_company(row), "error": row.get("error", "")}
            for row in rows
            if row.get("status") == "failed"
        ],
    )


def write_quality_report(
    batch_dir: Path,
    batch_id: str,
    rows: list[dict[str, Any]],
    *,
    pipeline: str = "",
    title: str = "批量运行质量报告",
) -> tuple[Path, Path]:
    """Write machine-readable and human-readable batch quality reports."""
    report = build_quality_report(batch_id, rows, pipeline=pipeline)
    json_path = batch_dir / "batch_quality_report.json"
    md_path = batch_dir / "batch_quality_report.md"
    write_json(json_path, report.model_dump(mode="json"))
    md_path.write_text(_render_quality_report(report, title=title), encoding="utf-8")
    return json_path, md_path


def write_failed_companies(batch_dir: Path, rows: list[dict[str, Any]]) -> Path:
    """Write failed_companies.txt for retry workflows."""
    path = batch_dir / "failed_companies.txt"
    failed = [_row_company(row) for row in rows if row.get("status") == "failed"]
    path.write_text("\n".join(failed) + ("\n" if failed else ""), encoding="utf-8")
    return path


def write_delivery_manifest(  # noqa: PLR0913
    *,
    batch_dir: Path,
    batch_id: str,
    rows: list[dict[str, Any]],
    summary_json: Path | None = None,
    summary_csv: Path | None = None,
    summary_md: Path | None = None,
    quality_json: Path | None = None,
    quality_md: Path | None = None,
    failed_path: Path | None = None,
    extra_files: list[dict[str, Any]] | None = None,
) -> Path:
    """Write delivery_manifest.json listing generated batch artifacts."""
    files: list[dict[str, Any]] = [{"type": "batch_manifest", "path": str(batch_dir / "batch_manifest.json")}]
    for file_type, path in (
        ("summary_json", summary_json),
        ("summary_csv", summary_csv),
        ("summary_md", summary_md),
        ("quality_json", quality_json),
        ("quality_md", quality_md),
        ("failed_companies", failed_path),
    ):
        if path is not None:
            files.append({"type": file_type, "path": str(path)})
    if extra_files:
        files.extend(extra_files)
    for row in rows:
        company_name = _row_company(row)
        if row.get("report_path"):
            files.append({"type": "company_report", "company_name": company_name, "path": row["report_path"]})
        if row.get("result_path"):
            files.append({"type": "company_result", "company_name": company_name, "path": row["result_path"]})
    path = batch_dir / "delivery_manifest.json"
    write_json(
        path,
        {
            "batch_id": batch_id,
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
        },
    )
    return path


def batch_status(rows: list[dict[str, Any]]) -> str:
    """Return a common status from per-target summary rows."""
    if not rows:
        return "failed"
    if any(row.get("status") == "failed" for row in rows):
        return "partial" if any(row.get("status") in ("success", "partial", "skipped") for row in rows) else "failed"
    if any(row.get("status") == "partial" for row in rows):
        return "partial"
    return "success"


def write_json(path: Path, value: Any) -> None:
    """Write UTF-8 JSON with stable formatting."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _render_quality_report(report: BatchQualityReport, *, title: str) -> str:
    lines = [
        f"# {title}",
        "",
        "## 总览",
        "",
        f"- 批次 ID：{report.batch_id}",
        f"- Pipeline：{report.pipeline or '未指定'}",
        f"- 企业数：{report.company_count}",
        "- 成功 / 部分成功 / 失败 / 跳过："
        f"{report.success_count} / {report.partial_count} / {report.failed_count} / {report.skipped_count}",
        f"- 平均画像完整度：{report.average_profile_completeness:.2f}",
        f"- 平均 Top 推荐分：{report.average_top_score:.1f}",
        "",
        "## 推荐分布",
        "",
        "| 产品 | Top1次数 |",
        "| --- | ---: |",
    ]
    if report.top_modules:
        lines.extend(f"| {item['module_id']} | {item['count']} |" for item in report.top_modules)
    else:
        lines.append("| 无 | 0 |")
    lines.extend(["", "## 数据质量", ""])
    if report.low_completeness_companies:
        lines.extend(
            f"- {item['company_name']}：画像完整度 {item['profile_completeness']}"
            for item in report.low_completeness_companies[:10]
        )
    else:
        lines.append("- 暂无画像完整度低于 60% 的企业。")
    lines.extend(["", "## Web 证据与冲突", ""])
    if report.high_conflict_companies:
        lines.extend(
            f"- {item['company_name']}：冲突 {item['conflict_count']} 处"
            for item in report.high_conflict_companies[:10]
        )
    else:
        lines.append("- 暂无高冲突企业。")
    lines.extend(["", "## 失败清单", ""])
    if report.failed_companies:
        lines.extend(f"- {item['company_name']}：{item['error']}" for item in report.failed_companies)
    else:
        lines.append("- 无失败企业。")
    lines.append("")
    return "\n".join(lines)


def _row_company(row: dict[str, Any]) -> str:
    return str(row.get("company_name") or row.get("target") or "")


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _average(values: list[float]) -> float:
    if not values:
        return 0
    return round(sum(values) / len(values), 4)

