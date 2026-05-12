"""summarize_node: call AI to produce structured JSON summary for one dimension."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog
from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from diligence.config import AppConfig, Dimension, ExtractField
from diligence.models import CostRecord, DimensionSearchResult, DimensionSummary, RunError, SearchItem
from diligence.settings import settings
from diligence.state import DiligenceState
from diligence.utils.source_registry import classify_source

log = structlog.get_logger(__name__)

_CONFIDENCE_ORDER: dict[str, int] = {"高": 3, "中": 2, "低": 1, "待核实": 0}
_CONFIDENCE_NAMES: dict[int, Literal["高", "中", "低", "待核实"]] = {3: "高", 2: "中", 1: "低", 0: "待核实"}

JSON_FORMAT_INSTRUCTION = (
    "\n\n【重要】请严格按以下 JSON 格式输出，不加任何 markdown 标记（不加```json）：\n"
    '{"summary": "500字以内的综合摘要（尽可能全面，涵盖所有搜索到的字段和细节）", "confidence": "高|中|低|待核实", '
    '"uncertain_facts": ["待核实项1", "待核实项2"], "evidence_item_ids": ["item_id_1"]}'
    "\n不要在 JSON 前后输出任何其他内容。"
)


class _AISummaryOutput(BaseModel):
    summary: str
    confidence: str
    uncertain_facts: list[str]
    evidence_item_ids: list[str]


class _FieldExtraction(BaseModel):
    source_item_id: str
    source_url: str
    value: str
    confidence: Literal["高", "中", "低"]


class _ExtractionsResult(BaseModel):
    extractions: dict[str, list[_FieldExtraction]]


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


_ai_client: AsyncOpenAI | None = None


def get_ai_client() -> AsyncOpenAI:
    """Return a cached AsyncOpenAI-compatible client for the reasoning/summarize layer.

    Uses LLM_* env vars when set; falls back to MINIMAX_* for backward compatibility.
    """
    global _ai_client  # noqa: PLW0603
    if _ai_client is None:
        api_key = settings.llm_api_key or settings.minimax_api_key
        _ai_client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.llm_base_url,
            http_client=httpx.AsyncClient(trust_env=False),
        )
    return _ai_client


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


_EXTRACTION_FULL_TEXT_LIMIT = 5000
_FULL_TEXT_LIMIT_WITH_EXTRACTION = 2000
_URL_TRUNCATE_LENGTH = 60

_MAX_SNIPPET_FALLBACK_ITEMS = 8
_SNIPPET_MIN_LENGTH = 20

# ── field validation ─────────────────────────────────────────────────────────
_VALIDATION_DELETE_VALUES: frozenset[str] = frozenset(
    {
        "",
        "未找到",
        "无",
        "暂无",
        "不详",
        "未知",
        "-",
        "null",
        "None",
        "未披露",
        "不祥",
        "暂无数据",
        "暂无信息",
    }
)

_CREDIT_CODE_RE = re.compile(r"\b[0-9A-Z]{18}\b")
_DATE_RE = re.compile(
    r"(\d{4}[-年./]\d{1,2}[-月./]\d{1,2}日?|\d{4}年?|长期|至今)",
)
_EMAIL_RE = re.compile(r"[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(\+?86[-\s]?)?(1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}|400[-\s]?\d{3}[-\s]?\d{4})",
)
_URL_RE = re.compile(r"https?://[^\s，,；;]+")
_CAPITAL_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(万|万元|亿|亿元|元)?\s*(人民币|美元|港元|CNY|RMB)?",
)


@dataclass(frozen=True)
class _ExtractionSource:
    """Internal structure pairing a SearchItem with its extraction metadata."""

    item: SearchItem
    content: str
    content_type: Literal["full_text", "snippet"]
    evidence_weight: Literal["high", "low"]


_EXTRACTION_RETRY_PROMPT = (
    "你上一次的输出不是合法 JSON。请重新输出，只输出 JSON 对象，不要任何其他内容：\n"
    '{"extractions": {"字段名": [{"source_item_id": "...", "source_url": "...", '
    '"value": "...", "confidence": "高|中|低"}]}}'
)


def _select_extraction_sources(items: list[SearchItem]) -> list[_ExtractionSource]:
    """Select extraction sources: full_text first, then snippets as fallback.

    Full-text items always take priority.  Snippets supplement only when full_text
    is absent for a given URL, and are capped at _MAX_SNIPPET_FALLBACK_ITEMS.
    """
    sources: list[_ExtractionSource] = []
    seen_keys: set[str] = set()

    for item in items:
        if item.full_text:
            key = item.url or item.id
            sources.append(
                _ExtractionSource(
                    item=item,
                    content=item.full_text,
                    content_type="full_text",
                    evidence_weight="high",
                )
            )
            seen_keys.add(key)

    snippet_count = 0
    for item in items:
        if snippet_count >= _MAX_SNIPPET_FALLBACK_ITEMS:
            break
        if not item.snippet or len(item.snippet.strip()) < _SNIPPET_MIN_LENGTH:
            continue
        key = item.url or item.id
        if key in seen_keys:
            continue
        sources.append(
            _ExtractionSource(
                item=item,
                content=item.snippet,
                content_type="snippet",
                evidence_weight="low",
            )
        )
        seen_keys.add(key)
        snippet_count += 1

    return sources


def _build_field_descriptions(extract_fields: list[ExtractField]) -> str:
    """Format extract_fields as a numbered list for the extraction prompt."""
    lines: list[str] = []
    for i, f in enumerate(extract_fields, 1):
        line = f"{i}. {f.field_name}：{f.description}"
        if f.examples:
            line += f"（示例：{f.examples}）"
        lines.append(line)
    return "\n".join(lines)


def _build_extraction_prompt(
    target: str,
    extract_fields: list[ExtractField],
    sources: list[_ExtractionSource],
    user_template: str,
) -> str:
    """Build the extraction user prompt with target, field list, and source contents.

    Each source is annotated with content_type (full_text/snippet) and
    evidence_weight (high/low) so the LLM can calibrate confidence.
    """
    field_descriptions = _build_field_descriptions(extract_fields)
    item_parts: list[str] = []
    for i, es in enumerate(sources, 1):
        text = es.content[:_EXTRACTION_FULL_TEXT_LIMIT]
        src = classify_source(es.item.url, es.item.title)
        source_header = (
            f"[来源 {i}] ID: {es.item.id} | URL: {es.item.url or 'none'} | "
            f"标题: {es.item.title}\n"
            f"来源类型: {src.source_type} | 权威等级: {src.authority_level} | "
            f"来源名称: {src.display_name}\n"
            f"内容类型: {es.content_type} | 证据权重: {es.evidence_weight}"
        )
        if es.content_type == "snippet":
            source_header += "\n注意: 本来源仅为搜索摘要，字段值可作为候选，但应标低置信度，除非有其他来源佐证。"
        item_parts.append(f"{source_header}\n{text}")
    item_contents = "\n\n---\n".join(item_parts)
    return user_template.format(
        target=target,
        field_descriptions=field_descriptions,
        count=len(sources),
        item_contents=item_contents,
    )


def _format_extraction_table(extractions: _ExtractionsResult) -> str:
    """Format extracted fields as a markdown table for the main summarize prompt."""
    lines = ["## 结构化字段提取结果\n"]
    lines.append("| 字段 | 候选值 | 来源 URL | 来源ID | 可信度 |")
    lines.append("|------|--------|---------|--------|--------|")
    for field_name, candidates in extractions.extractions.items():
        if not candidates:
            lines.append(f"| {field_name} | *未找到* | - | - | - |")
            continue
        for c in candidates:
            if len(c.source_url) > _URL_TRUNCATE_LENGTH:
                url_short = c.source_url[:_URL_TRUNCATE_LENGTH] + "..."
            else:
                url_short = c.source_url
            lines.append(f"| {field_name} | {c.value} | {url_short} | {c.source_item_id} | {c.confidence} |")
    return "\n".join(lines)


def _render_results(
    dsr: DimensionSearchResult,
    extraction_table: str | None = None,
) -> str:
    """Render items for the AI prompt. When extraction_table is provided, full_text is truncated."""
    parts: list[str] = []
    if extraction_table:
        parts.append(extraction_table)
        parts.append("")

    item_lines: list[str] = []
    for item in dsr.items:
        content = item.full_text if item.full_text else item.snippet
        source = "full_page" if item.full_text else "snippet"
        if extraction_table and item.full_text and len(content) > _FULL_TEXT_LIMIT_WITH_EXTRACTION:
            content = content[:_FULL_TEXT_LIMIT_WITH_EXTRACTION] + "...(已截断，关键字段已通过结构化提取获得)"
        item_lines.append(f"[id={item.id}] [{source}] title: {item.title} | url: {item.url or 'none'}\n{content}")

    parts.append("\n\n---\n".join(item_lines) if item_lines else "(no search results)")
    return "\n".join(parts)


def _downgrade_confidence(c: _FieldExtraction) -> None:
    """Reduce a field extraction's confidence to 低 if it's currently 高 or 中."""
    if c.confidence in ("高", "中"):
        c.confidence = "低"


def _field_kind(field_name: str) -> str:
    """Classify a field name into a known validation kind via keyword matching."""
    name = field_name.strip()
    for keywords, kind in [
        (("统一社会信用代码", "信用代码"), "credit_code"),
        (("成立日期", "核准日期", "日期", "营业期限"), "date"),
        (("邮箱", "电子邮箱"), "email"),
        (("电话", "联系方式", "联系电话"), "phone"),
        (("官网", "来源URL", "URL", "网址"), "url"),
        (("注册资本", "实缴资本"), "capital"),
    ]:
        if any(kw in name for kw in keywords):
            return kind
    return "unknown"


def _validate_credit_code(c: _FieldExtraction) -> bool:
    """Extract an 18-char credit code from the value.  Returns False if none found."""
    value = c.value.strip().upper()
    m = _CREDIT_CODE_RE.search(value)
    if not m:
        return False
    c.value = m.group(0)
    return True


def _validate_email(c: _FieldExtraction) -> bool:
    """Extract email addresses from the value.  Returns False if none found."""
    matches = _EMAIL_RE.findall(c.value)
    if not matches:
        return False
    c.value = ", ".join(matches)
    return True


def _looks_like_phone(value: str) -> bool:
    """Return True if the value contains a recognisable Chinese phone number."""
    return bool(_PHONE_RE.search(value))


def _normalize_url_value(value: str) -> str:
    """Strip common URL label prefixes like 官网： or 网址：."""
    m = _URL_RE.search(value)
    return m.group(0) if m else value


def _validate_url(c: _FieldExtraction, *, strict: bool) -> bool:
    """Validate a URL field.

    strict=True (来源URL): delete when no http(s) URL found.
    strict=False (官网): keep but downgrade when only a bare www. domain is present
    (no scheme).  Values with a full http(s) URL are normalised to extract just
    the URL and keep their original confidence.
    """
    url_match = _URL_RE.search(c.value)
    if url_match:
        c.value = url_match.group(0)
        return True
    if not strict:
        if c.value.strip().startswith("www."):
            _downgrade_confidence(c)
            return True
        _downgrade_confidence(c)
        return True
    return False


def _looks_like_date(value: str) -> bool:
    """Return True if the value contains a recognisable date pattern."""
    return bool(_DATE_RE.search(value))


def _looks_like_capital(value: str) -> bool:
    """Return True if the value contains a number + optional unit for capital."""
    return bool(_CAPITAL_RE.search(value))


@dataclass
class _ValidationStats:
    removed: int = 0
    downgraded: int = 0
    normalized: int = 0


def _validate_extractions(extractions: _ExtractionsResult) -> _ValidationStats:  # noqa: C901, PLR0912
    """Apply deterministic field-format validation to every candidate.

    Rules (per field kind):
    - credit_code / email: delete when format is unrecognisable
    - phone / date / capital: downgrade confidence when format is unrecognisable
    - url: delete when strict (来源URL) and no URL found; downgrade otherwise
    - unknown: keep as-is (no validation)
    - Placeholder values (空/未找到/无 etc.) are always removed.
    """
    cleaned: dict[str, list[_FieldExtraction]] = {}
    stats = _ValidationStats()

    for field_name, candidates in extractions.extractions.items():
        kind = _field_kind(field_name)
        valid_candidates: list[_FieldExtraction] = []

        for c in candidates:
            raw = c.value.strip()
            if raw in _VALIDATION_DELETE_VALUES:
                stats.removed += 1
                continue

            old_conf = c.confidence
            old_value = c.value
            keep = True

            if kind == "credit_code":
                keep = _validate_credit_code(c)
            elif kind == "email":
                keep = _validate_email(c)
            elif kind == "phone":
                if not _looks_like_phone(c.value):
                    _downgrade_confidence(c)
            elif kind == "url":
                keep = _validate_url(c, strict=("来源URL" in field_name))
            elif kind == "date":
                if not _looks_like_date(c.value):
                    _downgrade_confidence(c)
            elif kind == "capital" and not _looks_like_capital(c.value):
                _downgrade_confidence(c)
            # kind == "unknown": keep as-is

            if not keep:
                stats.removed += 1
            else:
                if c.confidence != old_conf:
                    stats.downgraded += 1
                if c.value != old_value:
                    stats.normalized += 1
                valid_candidates.append(c)

        if valid_candidates:
            cleaned[field_name] = valid_candidates

    extractions.extractions = cleaned
    return stats


def _apply_snippet_confidence_cap(
    extractions: _ExtractionsResult,
    sources: list[_ExtractionSource],
) -> int:
    """Downgrade snippet-only field extractions from 高 to 低 (field-level cap).

    For each field, if every candidate originates from snippet sources
    (evidence_weight=\"low\"), cap any 高 confidence down to 低.
    This is a field-level rule: a single full_text candidate in the field
    preserves the higher confidence for all candidates in that field.
    Individual candidate-level confidence is not adjusted here.

    Returns the number of candidates downgraded.
    """
    full_text_ids = {es.item.id for es in sources if es.content_type == "full_text"}
    downgraded = 0

    for candidates in extractions.extractions.values():
        if not candidates:
            continue
        all_snippet = all(c.source_item_id not in full_text_ids for c in candidates)
        if all_snippet:
            for c in candidates:
                if c.confidence == "高":
                    c.confidence = "低"
                    downgraded += 1

    return downgraded


async def _do_structured_extraction(  # noqa: PLR0913
    items: list[SearchItem],
    extract_fields: list[ExtractField],
    dim_name: str,
    target: str,
    client: AsyncOpenAI,
    config: AppConfig,
) -> tuple[_ExtractionsResult | None, CostRecord]:
    """One LLM call to extract specified fields from search items (full_text + snippet fallback).

    Returns (extractions_result, cost_record).
    extractions_result is None if no sources available or LLM call failed.
    """
    cost = CostRecord()
    sources = _select_extraction_sources(items)
    if not sources:
        log.info("extraction_skipped_no_sources", dimension=dim_name)
        return None, cost

    prompt = _build_extraction_prompt(
        target,
        extract_fields,
        sources,
        config.extract_user_template,
    )
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": config.extract_system_prompt},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
        )
        cost.llm_calls += 1
        cost.llm_tokens_total += response.usage.total_tokens if response.usage else 0
        raw_content = response.choices[0].message.content or ""

        # Attempt 1: parse directly
        try:
            parsed = _ExtractionsResult.model_validate_json(_extract_json(raw_content))
        except (json.JSONDecodeError, ValidationError):
            # Attempt 2: retry
            log.warning("extraction_json_parse_retry", dimension=dim_name)
            messages.extend(
                [
                    {"role": "assistant", "content": raw_content},
                    {"role": "user", "content": _EXTRACTION_RETRY_PROMPT},
                ]
            )
            retry_response = await client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
            )
            cost.llm_calls += 1
            cost.llm_tokens_total += retry_response.usage.total_tokens if retry_response.usage else 0
            retry_content = retry_response.choices[0].message.content or ""
            parsed = _ExtractionsResult.model_validate_json(_extract_json(retry_content))

        # Filter hallucinated source_item_ids — only ids that entered the prompt are valid
        valid_ids = {source.item.id for source in sources}
        cleaned: dict[str, list[_FieldExtraction]] = {}
        for field_name, candidates in parsed.extractions.items():
            valid_candidates = [c for c in candidates if c.source_item_id in valid_ids]
            if valid_candidates:
                cleaned[field_name] = valid_candidates
        parsed.extractions = cleaned

        stats = _validate_extractions(parsed)
        snippet_downgraded = _apply_snippet_confidence_cap(parsed, sources)

        log.info(
            "extraction_complete",
            dimension=dim_name,
            fields_found=len(parsed.extractions),
            fields_configured=len(extract_fields),
            removed=stats.removed,
            format_downgraded=stats.downgraded,
            snippet_downgraded=snippet_downgraded,
            normalized=stats.normalized,
        )
        sys.stderr.write(
            f"  [{dim_name}] structured extraction: {len(parsed.extractions)}/{len(extract_fields)}"
            f" fields found (removed={stats.removed}, fmt↓={stats.downgraded},"
            f" snip↓={snippet_downgraded}, norm={stats.normalized})\n"
        )

    except (json.JSONDecodeError, ValidationError, OpenAIError) as exc:
        log.warning("extraction_failed", dimension=dim_name, error=str(exc))
        sys.stderr.write(f"  [{dim_name}] extraction failed, falling back to full-text summarization\n")
        return None, cost
    else:
        return parsed, cost


_JSON_RETRY_PROMPT = (
    "你上一次的输出不是合法 JSON。请重新输出，只输出 JSON 对象，不要任何其他内容：\n"
    '{"summary": "摘要内容", "confidence": "高|中|低|待核实", "uncertain_facts": [], "evidence_item_ids": []}'
)


async def summarize_node(state: DiligenceState) -> dict[str, object]:  # noqa: PLR0915
    """Call AI to summarise search results for the current dimension."""
    dim: Dimension = state["current_dimension"]  # type: ignore[assignment]
    target: str = state["target"]
    config: AppConfig = state["config"]
    dsr: DimensionSearchResult = state["search_results_by_dimension"][dim.id]
    max_sources = config.report_options.max_sources_per_dimension

    # ── Structured extraction step ──
    extraction_table: str | None = None
    llm_calls = 0
    llm_tokens = 0

    if dim.extract_fields:
        extraction_client = get_ai_client()
        extractions, ext_cost = await _do_structured_extraction(
            items=dsr.items,
            extract_fields=dim.extract_fields,
            dim_name=dim.name,
            target=target,
            client=extraction_client,
            config=config,
        )
        llm_calls += ext_cost.llm_calls
        llm_tokens += ext_cost.llm_tokens_total
        if extractions is not None:
            dsr.extractions = extractions.model_dump()
            extraction_table = _format_extraction_table(extractions)

    rendered = _render_results(dsr, extraction_table=extraction_table)
    prompt = dim.summary_prompt.replace("{target}", target).replace("{results}", rendered) + JSON_FORMAT_INSTRUCTION

    errors: list[RunError] = []
    try:
        client = get_ai_client()
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": config.summarize_system_prompt},
            {"role": "user", "content": prompt},
        ]
        response = await client.chat.completions.create(model=settings.llm_model, messages=messages)
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
            retry_response = await client.chat.completions.create(model=settings.llm_model, messages=messages)
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
