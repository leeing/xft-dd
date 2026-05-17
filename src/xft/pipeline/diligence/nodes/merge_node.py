"""merge_node: call AI to produce the final consolidated report."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import structlog
from openai import OpenAIError

from xft.pipeline.diligence.config import AppConfig
from xft.pipeline.diligence.models import CostRecord, DimensionSummary, RunError
from xft.pipeline.diligence.nodes.summarize_node import _THINK_TAG_RE, get_ai_client
from xft.pipeline.diligence.state import DiligenceState
from xft.settings import settings
from xft.utils.source_registry import classify_source

log = structlog.get_logger(__name__)


_URL_TRUNCATE_LENGTH = 60


def _format_extraction_table(extractions: dict[str, object] | None) -> str | None:
    """Format structured extraction data as a markdown table for the merge prompt."""
    if not extractions:
        return None
    # Handle nested structure: extractions may be {"extractions": {...}} or {...}
    inner = extractions.get("extractions") if "extractions" in extractions else extractions
    if not isinstance(inner, dict) or not inner:
        return None

    lines = ["### 结构化字段提取数据（优先采信，已多源交叉验证）", ""]
    lines.append("| 字段 | 候选值 | 来源名称 | 来源类型 | 来源URL | 可信度 |")
    lines.append("|------|--------|---------|---------|---------|--------|")
    for field_name, candidates in inner.items():
        if not candidates:
            lines.append(f"| {field_name} | *未找到* | - | - | - | - |")
            continue
        for c in candidates:
            if len(c["source_url"]) > _URL_TRUNCATE_LENGTH:
                url_short = c["source_url"][:_URL_TRUNCATE_LENGTH] + "..."
            else:
                url_short = c["source_url"]
            src = classify_source(c["source_url"])
            val = c["value"].replace("|", "\\|")
            lines.append(
                f"| {field_name} | {val} | {src.display_name} | {src.source_type} | {url_short} | {c['confidence']} |"
            )
    lines.append("")
    return "\n".join(lines)


def _format_summaries(
    summaries: dict[str, DimensionSummary],
    active_ids: list[str],
    search_results: dict[str, object] | None = None,
    skipped_dims: list[dict[str, str]] | None = None,
    active_dim_names: dict[str, str] | None = None,
) -> str:
    lines: list[str] = []
    names = active_dim_names or {}
    for dim_id in active_ids:
        s: DimensionSummary | None = summaries.get(dim_id)
        if s is None:
            dim_name = names.get(dim_id, dim_id)
            lines.append(f"## {dim_name}\n**status: 执行失败**\n搜索或抓取过程异常，未生成摘要。")
            continue
        lines.append(f"## {s.dimension_name}\n**confidence: {s.confidence}**\n{s.summary}")
        if s.uncertain_facts:
            lines.append("**uncertain: " + "; ".join(s.uncertain_facts) + "**")

        # Append extraction data if available
        if search_results:
            dsr = search_results.get(dim_id)
            if dsr is not None:
                ext = getattr(dsr, "extractions", None)
                ext_table = _format_extraction_table(ext)
                if ext_table:
                    lines.append(ext_table)

    # Append skipped (not-run) dimensions so the merge LLM sees them
    if skipped_dims:
        for dim in skipped_dims:
            lines.append(  # noqa: PERF401
                f"## {dim['name']}\n**status: 未执行**\n本维度未在本次运行中检索，无法判断是否存在相关信息。"
            )

    return "\n\n".join(lines)


async def merge_node(state: DiligenceState) -> dict[str, object]:
    """Merge all dimension summaries into a final report via AI."""
    config: AppConfig = state["config"]
    target: str = state["target"]
    summaries = state["summaries_by_dimension"]
    active_dims = state["active_dimensions"]
    active_ids = [d.id for d in active_dims]
    errors: list[RunError] = []

    required_failed = [
        d
        for d in active_dims
        if d.required and (d.id not in summaries or summaries[d.id].status in ("failed", "partial"))
    ]

    all_names: dict[str, str] = state.get("all_dimension_names", {})
    active_dim_names = {d.id: d.name for d in active_dims}
    active_ids_set = set(active_ids)
    skipped_dims = [{"id": dim_id, "name": name} for dim_id, name in all_names.items() if dim_id not in active_ids_set]

    formatted = _format_summaries(
        summaries,
        active_ids,
        search_results=state.get("search_results_by_dimension"),
        skipped_dims=skipped_dims if skipped_dims else None,
        active_dim_names=active_dim_names,
    )
    prompt = config.merge_prompt.replace("{target}", target).replace("{summaries}", formatted)

    report_lines: list[str] = [f"WARNING: required dimension [{dim.name}] failed\n" for dim in required_failed]
    llm_tokens = 0

    try:
        client = get_ai_client()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": config.merge_system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        llm_tokens = response.usage.total_tokens if response.usage else 0
        report_body = _THINK_TAG_RE.sub("", response.choices[0].message.content or "").strip()
    except OpenAIError as exc:
        errors.append(
            RunError(
                stage="merge",
                message=f"AI report generation failed: {exc}",
                timestamp=datetime.now(UTC),
            )
        )
        report_body = formatted
        log.warning("merge_fallback", error=str(exc))

    report_lines.append(report_body)
    # Append real report generation timestamp so it's never hardcoded
    now_cn = datetime.now(UTC).astimezone()
    report_lines.append(f"\n*报告生成时间：{now_cn.year}年{now_cn.month}月{now_cn.day}日*")
    report = "\n".join(report_lines)

    sys.stdout.write("\n[Final Report]\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.write(report + "\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.flush()

    cost = CostRecord(llm_calls=1, llm_tokens_total=llm_tokens)
    return {"report": report, "errors": errors, "cost": cost}
