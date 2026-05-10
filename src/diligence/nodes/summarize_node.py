"""summarize_node: call AI to produce structured JSON summary for one dimension."""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from typing import Literal

import structlog
from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from diligence.config import AppConfig, Dimension
from diligence.models import CostRecord, DimensionSearchResult, DimensionSummary, RunError
from diligence.settings import settings
from diligence.state import DiligenceState

log = structlog.get_logger(__name__)

_CONFIDENCE_ORDER: dict[str, int] = {"高": 3, "中": 2, "低": 1, "待核实": 0}
_CONFIDENCE_NAMES: dict[int, Literal["高", "中", "低", "待核实"]] = {3: "高", 2: "中", 1: "低", 0: "待核实"}

JSON_FORMAT_INSTRUCTION = (
    "\n\n【重要】请严格按以下 JSON 格式输出，不加任何 markdown 标记（不加```json）：\n"
    '{"summary": "200字以内的综合摘要", "confidence": "高|中|低|待核实", '
    '"uncertain_facts": ["待核实项1", "待核实项2"], "evidence_item_ids": ["item_id_1"]}'
    "\n不要在 JSON 前后输出任何其他内容。"
)


class _AISummaryOutput(BaseModel):
    summary: str
    confidence: str
    uncertain_facts: list[str]
    evidence_item_ids: list[str]


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(raw: str) -> str:
    """Strip <think> blocks and code fences, then extract the outermost JSON object."""
    cleaned = _THINK_TAG_RE.sub("", raw).strip()
    # Try code-fenced JSON first (```json ... ```)
    fence_match = _CODE_FENCE_RE.search(cleaned)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{"):
            return candidate
    # Fall back to bare JSON object extraction
    m = _JSON_OBJECT_RE.search(cleaned)
    return m.group(0) if m else cleaned


def get_ai_client() -> OpenAI:
    """Return an OpenAI-compatible client for the reasoning/summarize layer.

    Uses LLM_* env vars when set; falls back to MINIMAX_* for backward compatibility.
    """
    api_key = settings.llm_api_key or settings.minimax_api_key
    return OpenAI(api_key=api_key, base_url=settings.llm_base_url)


def _apply_confidence_floor(
    confidence: str,
    items_count: int,
    *,
    all_urls_empty: bool,
    status: str,
) -> Literal["高", "中", "低", "待核实"]:
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
    """Render items for the AI prompt. Uses full_text if available, otherwise snippet."""
    lines = []
    for item in dsr.items:
        content = item.full_text if item.full_text else item.snippet
        source = "full_page" if item.full_text else "snippet"
        lines.append(f"[id={item.id}] [{source}] title: {item.title} | url: {item.url or 'none'}\n{content}")
    return "\n\n---\n".join(lines) if lines else "(no search results)"


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


_JSON_RETRY_PROMPT = (
    "你上一次的输出不是合法 JSON。请重新输出，只输出 JSON 对象，不要任何其他内容：\n"
    '{"summary": "摘要内容", "confidence": "高|中|低|待核实", "uncertain_facts": [], "evidence_item_ids": []}'
)


async def summarize_node(state: DiligenceState) -> dict[str, object]:
    """Call AI to summarise search results for the current dimension."""
    dim: Dimension = state["current_dimension"]  # type: ignore[assignment]
    target: str = state["target"]
    config: AppConfig = state["config"]
    dsr: DimensionSearchResult = state["search_results_by_dimension"][dim.id]
    max_sources = config.report_options.max_sources_per_dimension

    rendered = _render_results(dsr)
    prompt = dim.summary_prompt.replace("{target}", target).replace("{results}", rendered) + JSON_FORMAT_INSTRUCTION

    errors: list[RunError] = []
    llm_calls = 0
    llm_tokens = 0
    try:
        client = get_ai_client()
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": config.summarize_system_prompt},
            {"role": "user", "content": prompt},
        ]
        response = client.chat.completions.create(model=settings.llm_model, messages=messages)
        llm_calls += 1
        llm_tokens += response.usage.total_tokens if response.usage else 0
        raw_content = response.choices[0].message.content or ""

        # Attempt 1: parse directly
        try:
            parsed = _AISummaryOutput.model_validate_json(_extract_json(raw_content))
        except (json.JSONDecodeError, ValidationError):
            # Attempt 2: ask model to fix its own output
            log.warning("json_parse_retry", dimension=dim.id)
            messages = [
                *messages,
                {"role": "assistant", "content": raw_content},
                {"role": "user", "content": _JSON_RETRY_PROMPT},
            ]
            retry_response = client.chat.completions.create(model=settings.llm_model, messages=messages)
            llm_calls += 1
            llm_tokens += retry_response.usage.total_tokens if retry_response.usage else 0
            retry_content = retry_response.choices[0].message.content or ""
            parsed = _AISummaryOutput.model_validate_json(_extract_json(retry_content))

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
        errors.append(
            RunError(
                dimension_id=dim.id,
                stage="summarize",
                message=f"JSON parse failed: {exc}",
                timestamp=datetime.now(UTC),
            )
        )
        summary = _fallback_summary(dsr, max_sources)
        log.warning("summarize_fallback", dimension=dim.id, error=str(exc))
        sys.stderr.write(f"  [{dim.name}] summary parse failed, using raw snippets\n")

    cost = CostRecord(llm_calls=llm_calls, llm_tokens_total=llm_tokens)
    return {"summaries_by_dimension": {dim.id: summary}, "errors": errors, "cost": cost}
