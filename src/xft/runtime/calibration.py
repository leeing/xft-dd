"""Business calibration helpers for recommendation batch outputs."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from xft.pipeline.recommender.batch import BatchRunResult

LOW_TOP_SCORE_THRESHOLD = 60
LOW_COMPLETENESS_THRESHOLD = 0.6
HIGH_CONFLICT_THRESHOLD = 3
DOMINANT_MODULE_RATIO_THRESHOLD = 0.6
MIN_COMPANIES_FOR_DISTRIBUTION_WARNING = 5
LOW_ACCEPTABLE_ACCURACY_THRESHOLD = 0.7


class CalibrationIssue(BaseModel):
    """One actionable calibration finding."""

    severity: str
    title: str
    detail: str
    recommendation: str


class CalibrationLabel(BaseModel):
    """Human business label for one calibration target."""

    company_name: str
    expected_top_module: str = ""
    acceptable_modules: list[str] = Field(default_factory=list)
    comment: str = ""


class CalibrationReport(BaseModel):
    """Aggregated recommendation calibration report."""

    batch_id: str
    company_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    top_module_distribution: list[dict[str, Any]] = Field(default_factory=list)
    average_top_score: float = 0
    low_score_companies: list[dict[str, Any]] = Field(default_factory=list)
    no_recommendation_companies: list[dict[str, Any]] = Field(default_factory=list)
    low_completeness_companies: list[dict[str, Any]] = Field(default_factory=list)
    high_conflict_companies: list[dict[str, Any]] = Field(default_factory=list)
    labeled_count: int = 0
    top1_match_count: int = 0
    acceptable_match_count: int = 0
    top1_accuracy: float = 0
    acceptable_accuracy: float = 0
    label_mismatches: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[CalibrationIssue] = Field(default_factory=list)


async def run_recommendation_calibration(  # noqa: PLR0913
    *,
    company_names: list[str],
    warehouse_db: str,
    scenario_path: str = "config/scenarios/sales_recommendation",
    batch_id: str | None = None,
    batch_output: str | None = None,
    limit: int | None = None,
    use_llm: bool = False,
    with_web: bool = False,
    labels_path: str | Path | None = None,
) -> tuple[BatchRunResult, CalibrationReport, Path, Path]:
    """Run a recommendation batch and write calibration JSON/Markdown reports."""
    from xft.pipeline.recommender.batch import BatchOptions, run_recommendation_batch  # noqa: PLC0415

    batch = await run_recommendation_batch(
        company_names=company_names,
        batch_id=batch_id,
        batch_output=batch_output,
        limit=limit,
        skip_existing=False,
        options=BatchOptions(
            warehouse_db=warehouse_db,
            scenario_path=scenario_path,
            use_llm=use_llm,
            with_web=with_web,
            use_web_evidence=with_web,
            continue_on_error=True,
        ),
    )
    labels = load_calibration_labels(labels_path) if labels_path else []
    report = build_calibration_report(batch.batch_id, batch.rows, labels=labels)
    batch_dir = Path(batch.batch_dir)
    json_path = batch_dir / "calibration_report.json"
    md_path = batch_dir / "calibration_report.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_calibration_report(report), encoding="utf-8")
    return batch, report, json_path, md_path


def load_calibration_labels(path: str | Path) -> list[CalibrationLabel]:
    """Load business calibration labels from CSV."""
    labels: list[CalibrationLabel] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            company_name = str(row.get("company_name") or "").strip()
            if not company_name:
                continue
            expected = str(row.get("expected_top_module") or "").strip()
            acceptable = _split_modules(str(row.get("acceptable_modules") or ""))
            if expected and expected not in acceptable:
                acceptable.insert(0, expected)
            labels.append(
                CalibrationLabel(
                    company_name=company_name,
                    expected_top_module=expected,
                    acceptable_modules=acceptable,
                    comment=str(row.get("comment") or "").strip(),
                )
            )
    return labels


def build_calibration_report(
    batch_id: str,
    rows: list[dict[str, Any]],
    *,
    labels: list[CalibrationLabel] | None = None,
) -> CalibrationReport:
    """Build a calibration report from recommendation batch summary rows."""
    status_counts = Counter(str(row.get("status") or "") for row in rows)
    module_counts = Counter(str(row.get("top_module_id") or "") for row in rows if row.get("top_module_id"))
    scored_rows = [row for row in rows if row.get("top_score") not in ("", None)]
    scores = [_float(row.get("top_score")) for row in scored_rows]
    low_score = [
        _company_metric(row, "top_score")
        for row in scored_rows
        if _float(row.get("top_score")) < LOW_TOP_SCORE_THRESHOLD
    ]
    no_recommendation = [
        _company_metric(row, "recommendation_count")
        for row in rows
        if _int(row.get("recommendation_count")) == 0 and row.get("status") != "failed"
    ]
    low_completeness = [
        _company_metric(row, "profile_completeness")
        for row in rows
        if row.get("profile_completeness") not in ("", None)
        and _float(row.get("profile_completeness")) < LOW_COMPLETENESS_THRESHOLD
    ]
    high_conflict = [
        _company_metric(row, "conflict_count")
        for row in rows
        if _int(row.get("conflict_count")) >= HIGH_CONFLICT_THRESHOLD
    ]
    top_distribution = [
        {"module_id": module_id, "count": count, "ratio": round(count / len(rows), 4) if rows else 0}
        for module_id, count in module_counts.most_common()
    ]
    report = CalibrationReport(
        batch_id=batch_id,
        company_count=len(rows),
        status_counts=dict(status_counts),
        top_module_distribution=top_distribution,
        average_top_score=_average(scores),
        low_score_companies=low_score,
        no_recommendation_companies=no_recommendation,
        low_completeness_companies=low_completeness,
        high_conflict_companies=high_conflict,
        **_label_metrics(rows, labels or []),
    )
    report.issues.extend(_detect_issues(report))
    return report


def render_calibration_report(report: CalibrationReport) -> str:
    """Render a human-readable calibration report."""
    lines = [
        "# 推荐规则校准报告",
        "",
        "## 总览",
        "",
        f"- 批次 ID：{report.batch_id}",
        f"- 企业数：{report.company_count}",
        f"- 状态分布：{json.dumps(report.status_counts, ensure_ascii=False)}",
        f"- 平均 Top 推荐分：{report.average_top_score:.1f}",
        "",
        "## Top1 产品分布",
        "",
        "| 产品 | 次数 | 占比 |",
        "| --- | ---: | ---: |",
    ]
    if report.top_module_distribution:
        lines.extend(
            f"| {item['module_id']} | {item['count']} | {item['ratio']:.1%} |"
            for item in report.top_module_distribution
        )
    else:
        lines.append("| 无 | 0 | 0.0% |")
    lines.extend(["", "## 需要关注的企业", ""])
    lines.extend(_metric_section("低分企业", report.low_score_companies, "top_score"))
    lines.extend(_metric_section("无推荐企业", report.no_recommendation_companies, "recommendation_count"))
    lines.extend(_metric_section("低画像完整度企业", report.low_completeness_companies, "profile_completeness"))
    lines.extend(_metric_section("高冲突企业", report.high_conflict_companies, "conflict_count"))
    lines.extend(["", "## 业务标注命中率", ""])
    if report.labeled_count:
        lines.extend(
            [
                f"- 标注企业数：{report.labeled_count}",
                f"- Top1 命中率：{report.top1_accuracy:.1%} ({report.top1_match_count}/{report.labeled_count})",
                "- 可接受命中率："
                f"{report.acceptable_accuracy:.1%} ({report.acceptable_match_count}/{report.labeled_count})",
                "",
                "### 错配案例",
                "",
            ]
        )
        if report.label_mismatches:
            lines.extend(
                (
                    "- {company_name}：实际 {actual_top_module}，期望 {expected_top_module}"
                    "，可接受 {acceptable_modules}"
                ).format(**item)
                for item in report.label_mismatches[:10]
            )
        else:
            lines.append("- 无")
    else:
        lines.append("- 未提供业务标注。")
    lines.extend(["", "## 校准建议", ""])
    if report.issues:
        lines.extend(
            f"- [{issue.severity}] {issue.title}：{issue.detail} 建议：{issue.recommendation}"
            for issue in report.issues
        )
    else:
        lines.append("- 暂无明显规则分布问题。")
    lines.append("")
    return "\n".join(lines)


def _label_metrics(rows: list[dict[str, Any]], labels: list[CalibrationLabel]) -> dict[str, Any]:
    if not labels:
        return {}
    row_by_company = {str(row.get("company_name") or row.get("target") or ""): row for row in rows}
    labeled_count = 0
    top1_match_count = 0
    acceptable_match_count = 0
    mismatches: list[dict[str, Any]] = []
    for label in labels:
        row = row_by_company.get(label.company_name)
        if row is None:
            continue
        top_module = str(row.get("top_module_id") or "")
        if not label.expected_top_module and not label.acceptable_modules:
            continue
        labeled_count += 1
        top1_match = bool(label.expected_top_module and top_module == label.expected_top_module)
        acceptable_match = top_module in set(label.acceptable_modules)
        top1_match_count += int(top1_match)
        acceptable_match_count += int(acceptable_match)
        if not acceptable_match:
            mismatches.append(
                {
                    "company_name": label.company_name,
                    "actual_top_module": top_module,
                    "expected_top_module": label.expected_top_module,
                    "acceptable_modules": label.acceptable_modules,
                    "comment": label.comment,
                }
            )
    return {
        "labeled_count": labeled_count,
        "top1_match_count": top1_match_count,
        "acceptable_match_count": acceptable_match_count,
        "top1_accuracy": round(top1_match_count / labeled_count, 4) if labeled_count else 0,
        "acceptable_accuracy": round(acceptable_match_count / labeled_count, 4) if labeled_count else 0,
        "label_mismatches": mismatches,
    }


def _detect_issues(report: CalibrationReport) -> list[CalibrationIssue]:
    issues: list[CalibrationIssue] = []
    if (
        report.top_module_distribution
        and report.top_module_distribution[0]["ratio"] >= DOMINANT_MODULE_RATIO_THRESHOLD
        and report.company_count >= MIN_COMPANIES_FOR_DISTRIBUTION_WARNING
    ):
        top = report.top_module_distribution[0]
        issues.append(
            CalibrationIssue(
                severity="medium",
                title="Top1 产品集中度偏高",
                detail=f"{top['module_id']} 占比 {top['ratio']:.1%}",
                recommendation="检查 base_score/priority 是否压过了维度差异，或补充负向规则拉开产品边界。",
            )
        )
    if report.average_top_score and report.average_top_score < LOW_TOP_SCORE_THRESHOLD:
        issues.append(
            CalibrationIssue(
                severity="high",
                title="平均推荐分偏低",
                detail=f"平均 Top 分 {report.average_top_score:.1f}",
                recommendation="检查维度 evidence_templates 是否过严，或为高价值本地字段补充 positive_rules。",
            )
        )
    if report.low_completeness_companies:
        issues.append(
            CalibrationIssue(
                severity="medium",
                title="存在低画像完整度企业",
                detail=f"{len(report.low_completeness_companies)} 家企业低于 60%",
                recommendation="优先补齐 warehouse 字段映射或开启 Web 富化后再重新校准。",
            )
        )
    if report.high_conflict_companies:
        issues.append(
            CalibrationIssue(
                severity="medium",
                title="存在高冲突企业",
                detail=f"{len(report.high_conflict_companies)} 家企业冲突数 >= 3",
                recommendation="复查 Web evidence 抽取和冲突消解，销售使用前先人工核验。",
            )
        )
    if report.no_recommendation_companies:
        issues.append(
            CalibrationIssue(
                severity="high",
                title="存在无推荐企业",
                detail=f"{len(report.no_recommendation_companies)} 家企业无推荐",
                recommendation="检查 fallback 推荐逻辑和产品 target_needs 覆盖面。",
            )
        )
    if report.labeled_count and report.acceptable_accuracy < LOW_ACCEPTABLE_ACCURACY_THRESHOLD:
        issues.append(
            CalibrationIssue(
                severity="high",
                title="业务标注命中率偏低",
                detail=f"可接受命中率 {report.acceptable_accuracy:.1%}",
                recommendation="复查产品规则 target_needs、positive_rules 和业务标注样本，优先分析错配案例。",
            )
        )
    return issues


def _metric_section(title: str, rows: list[dict[str, Any]], metric: str) -> list[str]:
    lines = [f"### {title}", ""]
    if not rows:
        lines.append("- 无")
        lines.append("")
        return lines
    lines.extend(f"- {row['company_name']}：{metric}={row.get(metric, '')}" for row in rows[:10])
    lines.append("")
    return lines


def _company_metric(row: dict[str, Any], metric: str) -> dict[str, Any]:
    return {
        "company_name": str(row.get("company_name") or row.get("target") or ""),
        metric: row.get(metric, ""),
        "status": row.get("status", ""),
        "top_module_id": row.get("top_module_id", ""),
    }


def _split_modules(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace(";", ",").replace("；", ",").replace("|", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


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
