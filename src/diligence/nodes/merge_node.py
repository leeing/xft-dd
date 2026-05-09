"""merge_node: call AI to produce the final consolidated report."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import structlog
from openai import OpenAIError

from diligence.config import AppConfig
from diligence.models import DimensionSummary, RunError
from diligence.nodes.summarize_node import get_ai_client
from diligence.state import DiligenceState

log = structlog.get_logger(__name__)


def _format_summaries(summaries: dict, active_ids: list[str]) -> str:
    lines = []
    for dim_id in active_ids:
        s: DimensionSummary | None = summaries.get(dim_id)
        if s is None:
            continue
        lines.append(f"## {s.dimension_name}\n**confidence: {s.confidence}**\n{s.summary}")
        if s.uncertain_facts:
            lines.append("**uncertain: " + "; ".join(s.uncertain_facts) + "**")
    return "\n\n".join(lines)


async def merge_node(state: DiligenceState) -> dict:
    """Merge all dimension summaries into a final report via AI."""
    config: AppConfig = state["config"]
    target: str = state["target"]
    summaries = state["summaries_by_dimension"]
    active_dims = state["active_dimensions"]
    active_ids = [d.id for d in active_dims]
    errors: list[RunError] = []

    required_failed = [
        d for d in active_dims if d.required and (d.id not in summaries or summaries[d.id].status == "failed")
    ]

    formatted = _format_summaries(summaries, active_ids)
    prompt = config.merge_prompt.replace("{target}", target).replace("{summaries}", formatted)

    report_lines: list[str] = [f"WARNING: required dimension [{dim.name}] failed\n" for dim in required_failed]

    try:
        client = get_ai_client()
        response = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
        )
        report_body = response.choices[0].message.content or ""
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
    report = "\n".join(report_lines)

    sys.stdout.write("\n[Final Report]\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.write(report + "\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.flush()

    return {"report": report, "errors": errors}
