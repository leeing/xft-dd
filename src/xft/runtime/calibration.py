"""Business calibration helpers for recommendation batch outputs."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from xft.runtime.artifacts import _average, _float, _int

if TYPE_CHECKING:
    from xft.pipeline.recommender.batch import BatchRunResult

LOW_TOP_SCORE_THRESHOLD = 60
LOW_COMPLETENESS_THRESHOLD = 0.6
HIGH_CONFLICT_THRESHOLD = 3
DOMINANT_MODULE_RATIO_THRESHOLD = 0.6
MIN_COMPANIES_FOR_DISTRIBUTION_WARNING = 5
LOW_ACCEPTABLE_ACCURACY_THRESHOLD = 0.7
BUSINESS_WEB_EVIDENCE_COVERAGE_THRESHOLD = 0.8
BUSINESS_WEB_SAMPLE_FIELDS = [
    "company_name",
    "status",
    "top_module_id",
    "top_score",
    "web_evidence_count",
    "conflict_count",
    "sample_evidence_claim",
    "sample_evidence_source_type",
    "sample_evidence_relation",
    "sample_evidence_url",
    "report_path",
    "result_path",
]


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
    use_llm: bool = False
    with_web: bool = False
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
    web_evidence_coverage: float = 0
    web_evidence_zero_companies: list[dict[str, Any]] = Field(default_factory=list)
    llm_fallback_suspected_companies: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[CalibrationIssue] = Field(default_factory=list)


async def run_recommendation_calibration(  # noqa: PLR0913
    *,
    company_names: list[str],
    warehouse_db: str,
    scenario_path: str = "config/recommend/sales_recommendation",
    batch_id: str | None = None,
    batch_output: str | None = None,
    limit: int | None = None,
    use_llm: bool = False,
    with_web: bool = False,
    refresh_web: bool = False,
    web_config_path: str | None = None,
    web_providers: list[str] | None = None,
    labels_path: str | Path | None = None,
) -> tuple[BatchRunResult, CalibrationReport, Path, Path, Path]:
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
            web_config_path=web_config_path,
            with_web=with_web,
            refresh_web=refresh_web,
            web_providers=web_providers,
            continue_on_error=True,
        ),
    )
    labels = load_calibration_labels(labels_path) if labels_path else []
    report = build_calibration_report(
        batch.batch_id,
        batch.rows,
        labels=labels,
        use_llm=use_llm,
        with_web=with_web,
    )
    batch_dir = Path(batch.batch_dir)
    json_path = batch_dir / "calibration_report.json"
    md_path = batch_dir / "calibration_report.md"
    review_path = batch_dir / "web_review_samples.csv"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_calibration_report(report), encoding="utf-8")
    write_web_review_samples(review_path, batch.rows)
    return batch, report, json_path, md_path, review_path


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


def build_calibration_report(  # noqa: PLR0913
    batch_id: str,
    rows: list[dict[str, Any]],
    *,
    labels: list[CalibrationLabel] | None = None,
    use_llm: bool = False,
    with_web: bool = False,
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
        use_llm=use_llm,
        with_web=with_web,
        company_count=len(rows),
        status_counts=dict(status_counts),
        top_module_distribution=top_distribution,
        average_top_score=_average(scores),
        low_score_companies=low_score,
        no_recommendation_companies=no_recommendation,
        low_completeness_companies=low_completeness,
        high_conflict_companies=high_conflict,
        web_evidence_coverage=_web_evidence_coverage(rows),
        web_evidence_zero_companies=[
            _company_metric(row, "web_evidence_count")
            for row in rows
            if with_web and row.get("status") != "failed" and _int(row.get("web_evidence_count")) == 0
        ],
        llm_fallback_suspected_companies=[
            _company_metric(row, "top_module_id")
            for row in rows
            if use_llm and row.get("status") != "failed" and _int(row.get("llm_calls_failed")) > 0
        ],
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
        f"- LLM 推荐：{'启用' if report.use_llm else '未启用'}",
        f"- 业务 Web 证据：{'启用' if report.with_web else '未启用'}",
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
    lines.extend(["", "## 业务 Web 校准", ""])
    if report.with_web:
        lines.extend(
            [
                f"- 业务 Web 证据覆盖率：{report.web_evidence_coverage:.1%}",
                "",
            ]
        )
        lines.extend(
            _metric_section(
                "未形成业务 Web 证据企业",
                report.web_evidence_zero_companies,
                "web_evidence_count",
            )
        )
    else:
        lines.append("- 未启用业务 Web 证据。")
        lines.append("")
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


def write_web_review_samples(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    """Write a small CSV that helps humans inspect business Web evidence quality."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=BUSINESS_WEB_SAMPLE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_review_sample_row(row))
    return out


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
                recommendation="检查 modules.yaml 的 base_score、标签门槛和指标规则是否过于宽松。",
            )
        )
    if report.average_top_score and report.average_top_score < LOW_TOP_SCORE_THRESHOLD:
        issues.append(
            CalibrationIssue(
                severity="high",
                title="平均推荐分偏低",
                detail=f"平均 Top 分 {report.average_top_score:.1f}",
                recommendation="检查 modules.d 的指标规则是否过严，或为高价值本地字段补充业务指标规则。",
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
                recommendation="复查业务指标证据和规则，销售使用前先人工核验。",
            )
        )
    if report.no_recommendation_companies:
        issues.append(
            CalibrationIssue(
                severity="high",
                title="存在无推荐企业",
                detail=f"{len(report.no_recommendation_companies)} 家企业无推荐",
                recommendation="检查 modules.yaml 的模块、标签、指标规则覆盖面。",
            )
        )
    if report.labeled_count and report.acceptable_accuracy < LOW_ACCEPTABLE_ACCURACY_THRESHOLD:
        issues.append(
            CalibrationIssue(
                severity="high",
                title="业务标注命中率偏低",
                detail=f"可接受命中率 {report.acceptable_accuracy:.1%}",
                recommendation="复查 modules.yaml 的指标标准、rule/llm/hybrid 配置和业务标注样本。",
            )
        )
    if report.with_web and report.web_evidence_coverage < BUSINESS_WEB_EVIDENCE_COVERAGE_THRESHOLD:
        issues.append(
            CalibrationIssue(
                severity="medium",
                title="业务 Web 证据覆盖率偏低",
                detail=f"业务 Web 证据覆盖率 {report.web_evidence_coverage:.1%}",
                recommendation="检查 modules.d 中 llm_web 指标的 fixed_queries 和 provider 可用性。",
            )
        )
    return issues


def _review_sample_row(row: dict[str, Any]) -> dict[str, Any]:
    sample = _sample_evidence_from_result(str(row.get("result_path") or ""))
    return {field: _review_value(field, row, sample) for field in BUSINESS_WEB_SAMPLE_FIELDS}


def _review_value(field: str, row: dict[str, Any], sample: dict[str, Any]) -> Any:
    if field.startswith("sample_evidence_"):
        return sample.get(field.removeprefix("sample_evidence_"), "")
    return row.get(field, "")


def _sample_evidence_from_result(result_path: str) -> dict[str, Any]:
    if not result_path:
        return {}
    path = Path(result_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list):
        return {}
    for rec in recommendations:
        sample = _sample_evidence_from_recommendation(rec)
        if sample:
            return sample
    return {}


def _sample_evidence_from_recommendation(rec: Any) -> dict[str, Any]:
    if not isinstance(rec, dict):
        return {}
    trace = rec.get("evidence_trace")
    if not isinstance(trace, list):
        return {}
    for item in trace:
        if isinstance(item, dict) and item.get("source_type") == "web":
            return {
                "claim": item.get("claim", ""),
                "source_type": item.get("source_type", ""),
                "relation": item.get("relation_to_profile", ""),
                "url": item.get("source_url", ""),
            }
    return {}


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


def _web_evidence_coverage(rows: list[dict[str, Any]]) -> float:
    runnable = [row for row in rows if row.get("status") != "failed"]
    if not runnable:
        return 0
    covered = [row for row in runnable if _int(row.get("web_evidence_count")) > 0]
    return round(len(covered) / len(runnable), 4)


def _split_modules(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace(";", ",").replace("；", ",").replace("|", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]
