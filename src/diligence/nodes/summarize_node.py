"""summarize_node: call AI to produce structured JSON summary for one dimension."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import structlog
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from diligence.config import AppConfig, Dimension
from diligence.models import DimensionSearchResult, DimensionSummary, RunError
from diligence.settings import settings
from diligence.state import DiligenceState

log = structlog.get_logger(__name__)

_CONFIDENCE_ORDER: dict[str, int] = {"高": 3, "中": 2, "低": 1, "待核实": 0}
_CONFIDENCE_NAMES: dict[int, str] = {3: "高", 2: "中", 1: "低", 0: "待核实"}

JSON_FORMAT_INSTRUCTION = (
    "\nOutput ONLY valid JSON (no markdown code fences):\n"
    '{"summary": "...", "confidence": "高|中|低|待核实", "uncertain_facts": [...], "evidence_item_ids": [...]}'
)


class _AISummaryOutput(BaseModel):
    summary: str
    confidence: str
    uncertain_facts: list[str]
    evidence_item_ids: list[str]


def get_ai_client() -> OpenAI:
    """Return an OpenAI-compatible client pointed at MiniMax."""
    return OpenAI(api_key=settings.minimax_api_key, base_url=settings.minimax_base_url)


def _apply_confidence_floor(
    confidence: str,
    items_count: int,
    *,
    all_urls_empty: bool,
    status: str,
) -> str:
    """Hard program rules that override AI-assigned confidence."""
    if status == "failed" or items_count == 0:
        return "待核实"
    level = _CONFIDENCE_ORDER.get(confidence, 0)
    if items_count == 1:
        level = min(level, _CONFIDENCE_ORDER["低"])
    if all_urls_empty:
        level = min(level, _CONFIDENCE_ORDER["低"])
    return _CONFIDENCE_NAMES[level]


def _render_results(dsr: DimensionSearchResult) -> str:
    lines = [
        f"[id={item.id}] title: {item.title} | url: {item.url or 'none'} | snippet: {item.snippet}"
        for item in dsr.items
    ]
    return "\n".join(lines) if lines else "(no search results)"


_SNIPPET_TRUNCATE_LIMIT = 1500


def _fallback_summary(dsr: DimensionSearchResult, max_sources: int) -> DimensionSummary:
    raw = " ".join(item.snippet for item in dsr.items)
    truncated = raw[:_SNIPPET_TRUNCATE_LIMIT]
    if len(raw) > _SNIPPET_TRUNCATE_LIMIT:
        truncated += "（以下为部分原始搜索片段）"
    return DimensionSummary(
        dimension_id=dsr.dimension_id,
        dimension_name=dsr.dimension_name,
        status="partial",
        summary=truncated or "no search results",
        confidence="待核实",
        uncertain_facts=["AI summary parse failed, showing raw snippets"],
        evidence_item_ids=[item.id for item in dsr.items[:max_sources]],
        error="JSON parse failed",
    )


async def summarize_node(state: DiligenceState) -> dict:
    """Call AI to summarise search results for the current dimension."""
    dim: Dimension = state["current_dimension"]
    target: str = state["target"]
    config: AppConfig = state["config"]
    dsr: DimensionSearchResult = state["search_results_by_dimension"][dim.id]
    max_sources = config.report_options.max_sources_per_dimension

    rendered = _render_results(dsr)
    prompt = (
        dim.summary_prompt.replace("{target}", target).replace("{results}", rendered)
        + JSON_FORMAT_INSTRUCTION
    )

    errors: list[RunError] = []
    try:
        client = get_ai_client()
        response = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_content = response.choices[0].message.content or ""
        parsed = _AISummaryOutput.model_validate_json(raw_content)

        valid_ids = {item.id for item in dsr.items}
        invalid_ids = [eid for eid in parsed.evidence_item_ids if eid not in valid_ids]
        if invalid_ids:
            log.warning("hallucinated_ids_filtered", dimension=dim.id, count=len(invalid_ids))
            parsed.evidence_item_ids = [eid for eid in parsed.evidence_item_ids if eid in valid_ids]

        all_urls_empty = all(item.url is None for item in dsr.items) if dsr.items else False
        final_confidence = _apply_confidence_floor(
            parsed.confidence, len(dsr.items), all_urls_empty=all_urls_empty, status=dsr.status
        )
        summary = DimensionSummary(
            dimension_id=dim.id,
            dimension_name=dim.name,
            status="success" if dsr.status == "success" else dsr.status,
            summary=parsed.summary,
            confidence=final_confidence,
            uncertain_facts=parsed.uncertain_facts,
            evidence_item_ids=parsed.evidence_item_ids,
        )
        log.info("summarize_complete", dimension=dim.id, confidence=final_confidence)
        sys.stderr.write(f"  [{dim.name}] summary done, confidence={final_confidence}\n")

    except (json.JSONDecodeError, ValidationError, OpenAIError) as exc:
        errors.append(RunError(
            dimension_id=dim.id,
            stage="summarize",
            message=f"JSON parse failed: {exc}",
            timestamp=datetime.now(UTC),
        ))
        summary = _fallback_summary(dsr, max_sources)
        log.warning("summarize_fallback", dimension=dim.id, error=str(exc))
        sys.stderr.write(f"  [{dim.name}] summary parse failed, using raw snippets\n")

    return {"summaries_by_dimension": {dim.id: summary}, "errors": errors}
