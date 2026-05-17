"""Extract concise Web evidence from normalized search and fetched-page records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.core.models import Confidence, DimensionAnalysis
from xft.evidence.models import EvidenceResolution
from xft.evidence.models import normalize_resolution as _normalize_base
from xft.progress import display
from xft.web.models import (
    EvidenceType,
    WebEvidenceRecord,
    WebExtractLLMConfig,
    WebSearchQueryRecord,
    WebSearchResultRecord,
)

CLAIM_MAX_CHARS = 300


def normalize_resolution(raw: str | None, *, is_conflict: bool = False) -> EvidenceResolution | None:
    """Normalize LLM-produced resolution, defaulting to 'use_local' for conflicts."""
    result = _normalize_base(raw)
    if result is not None:
        return result
    return "use_local" if is_conflict else None


# Common Chinese company suffixes stripped when extracting the core identifying name.
_COMPANY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "合伙企业",
    "普通合伙",
    "有限合伙",
)

# Known company names that are similar to but different from common targets.
# Maps core name → names that look similar but are different companies.
_KNOWN_FALSE_COMPANY_PATTERNS: dict[str, list[str]] = {}


def _company_name_key(target: str) -> str:
    """Extract the core identifying portion of a Chinese company name.

    >>> _company_name_key("广东信华电器有限公司")
    '信华电器'
    """
    key = target
    for prefix in (
        "广东", "深圳市", "北京市", "上海市", "广州市", "浙江省", "江苏省",
        "深圳", "北京", "上海", "广州", "浙江", "江苏", "杭州", "成都", "武汉",
    ):
        if key.startswith(prefix) and len(key) > len(prefix) + 2:
            key = key[len(prefix):]
            break
    for suffix in _COMPANY_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix) + 1:
            key = key[:-len(suffix)]
            break
    return key


def _is_relevant_claim(claim: str, *, company_name: str, core_name: str) -> bool:
    """Check whether a claim is about the target company (not a similar name).

    Returns False for claims that:
    - Don't mention the target company at all
    - Mention a similarly-named but different company
    """
    if not claim.strip():
        return False
    # Must mention either the core name or full company name
    return bool(core_name in claim or company_name in claim)


class _ExtractedClaim(BaseModel):
    type: EvidenceType
    claim: str
    confidence: str = "低"
    source_result_id: str
    source_quote: str | None = None
    json_field: str | None = None
    json_value: str | None = None
    web_value: str | None = None
    conflict_note: str | None = None
    resolution: str | None = None


class _ExtractedClaims(BaseModel):
    claims: list[_ExtractedClaim] = Field(default_factory=list)


def build_web_evidence(result: WebSearchResultRecord, query: WebSearchQueryRecord) -> WebEvidenceRecord:
    """Create one low-confidence fallback evidence item from a search result."""
    text = result.snippet or result.full_text_preview or result.full_text[:300]
    claim = f"{result.title}：{text}" if text else result.title
    return WebEvidenceRecord(
        evidence_id=f"e_{result.result_id.removeprefix('r_')}",
        web_run_id=result.web_run_id,
        result_id=result.result_id,
        query_id=result.query_id,
        credit_code=result.credit_code,
        company_name=result.company_name,
        dimension_id=result.dimension_id,
        provider=result.provider,
        claim=claim[:CLAIM_MAX_CHARS],
        evidence_type="supplement",
        relation_to_profile="supplement",
        source_url=result.url,
        source_title=result.title,
        query=query.query,
        raw_response_path=result.raw_response_path,
        created_at=datetime.now(UTC),
    )


async def extract_evidence_batch(  # noqa: PLR0913
    *,
    profile: dict[str, Any],
    analysis: DimensionAnalysis,
    results: list[WebSearchResultRecord],
    queries_by_id: dict[str, WebSearchQueryRecord],
    llm_config: WebExtractLLMConfig,
    use_llm: bool = True,
) -> tuple[list[WebEvidenceRecord], dict[str, Any], dict[str, Any]]:
    """Extract concise evidence using an LLM; fallback is deterministic."""
    if not results:
        return [], {}, {}
    company_name = str(profile.get("company_name", ""))
    request_payload = _build_request_payload(
        profile=profile, analysis=analysis, results=results, llm_config=llm_config,
        company_name=company_name,
    )
    source_count = len(request_payload.get("sources", []))
    if not use_llm or not llm_config.enabled:
        fallback_evidence = _fallback_extract(results, queries_by_id)
        display.branch(f"🧠 {analysis.dimension_id}: 兜底提取 → {len(fallback_evidence)}条 (输入{source_count}个来源)")
        return fallback_evidence, request_payload, {"mode": "fallback"}
    try:
        raw_prompt = Path(llm_config.prompt_file).read_text(encoding="utf-8")
        system_prompt = (
            raw_prompt.replace("{company_name}", company_name).replace(
                "{dimension_name}", analysis.dimension_id
            )
        )
        task = llm_config.tasks.get("web_evidence_extract")
        client = get_ai_client()
        response = await client.chat.completions.create(
            model=_configured_model(llm_config),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False, default=str)},
            ],
            temperature=task.temperature if task else 0,
            timeout=task.timeout_seconds if task else 90,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = _ExtractedClaims.model_validate_json(extract_json(raw))
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
        fallback_evidence = _fallback_extract(results, queries_by_id)
        display.branch(f"🧠 {analysis.dimension_id}: LLM失败, 兜底提取 → {len(fallback_evidence)}条")
        return fallback_evidence, request_payload, {"mode": "fallback"}
    evidence = _records_from_claims(
        parsed.claims, results=results, queries_by_id=queries_by_id, llm_config=llm_config,
        company_name=company_name,
    )
    total_claims = len(parsed.claims)
    accepted = len(evidence)
    rejected = total_claims - accepted
    status = f"提取{total_claims}条, 采纳{accepted}条"
    if rejected:
        status += f", 相关性过滤{rejected}条"
    display.branch(f"🧠 {analysis.dimension_id}: LLM提取 → {status}")
    return evidence, request_payload, parsed.model_dump(mode="json")


def _build_request_payload(
    *,
    profile: dict[str, Any],
    analysis: DimensionAnalysis,
    results: list[WebSearchResultRecord],
    llm_config: WebExtractLLMConfig,
    company_name: str = "",
) -> dict[str, Any]:
    task = llm_config.tasks.get("web_evidence_extract")
    max_sources = task.max_sources_per_call if task else 8
    max_chars = task.max_chars_per_source if task else 4000
    return {
        "company_name": company_name or str(profile.get("company_name", "")),
        "dimension_name": analysis.dimension_id,
        "company_profile": profile,
        "dimension": analysis.model_dump(mode="json"),
        "local_ground_truth": {
            "facts": [fact.claim for fact in analysis.facts],
            "inferences": analysis.inferences,
        },
        "sources": [
            {
                "source_result_id": item.result_id,
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "content": (item.full_text_preview or item.snippet)[:max_chars],
            }
            for item in results[:max_sources]
        ],
    }


def _fallback_extract(
    results: list[WebSearchResultRecord],
    queries_by_id: dict[str, WebSearchQueryRecord],
) -> list[WebEvidenceRecord]:
    seen: set[str] = set()
    evidence: list[WebEvidenceRecord] = []
    for result in results:
        key = result.url or f"{result.title}{result.snippet}"
        if key in seen:
            continue
        seen.add(key)
        evidence.append(build_web_evidence(result, queries_by_id[result.query_id]))
    return evidence


def _records_from_claims(
    claims: list[_ExtractedClaim],
    *,
    results: list[WebSearchResultRecord],
    queries_by_id: dict[str, WebSearchQueryRecord],
    llm_config: WebExtractLLMConfig,
    company_name: str = "",
) -> list[WebEvidenceRecord]:
    core = _company_name_key(company_name) if company_name else ""
    result_by_id = {item.result_id: item for item in results}
    evidence: list[WebEvidenceRecord] = []
    for idx, claim in enumerate(claims, 1):
        result = result_by_id.get(claim.source_result_id)
        if result is None:
            continue
        if core and not _is_relevant_claim(claim.claim, company_name=company_name, core_name=core):
            continue
        query = queries_by_id[result.query_id]
        evidence.append(
            WebEvidenceRecord(
                evidence_id=f"e_{result.result_id.removeprefix('r_')}_{idx}",
                web_run_id=result.web_run_id,
                result_id=result.result_id,
                query_id=result.query_id,
                credit_code=result.credit_code,
                company_name=result.company_name,
                dimension_id=result.dimension_id,
                provider=result.provider,
                claim=claim.claim[:CLAIM_MAX_CHARS],
                evidence_type=claim.type,
                relation_to_profile=claim.type,
                confidence=_confidence(claim.confidence),
                source_url=result.url,
                source_title=result.title,
                query=query.query,
                source_quote=claim.source_quote,
                json_field=claim.json_field,
                json_value=claim.json_value,
                web_value=claim.web_value,
                conflict_note=claim.conflict_note,
                resolution=normalize_resolution(claim.resolution, is_conflict=claim.type == "conflict"),
                extraction_model=_configured_model(llm_config),
                extraction_prompt_version=llm_config.version,
                raw_response_path=result.raw_response_path,
                created_at=datetime.now(UTC),
            )
        )
    return evidence


def _configured_model(llm_config: WebExtractLLMConfig) -> str:
    provider = llm_config.providers.get(llm_config.provider, {})
    value = provider.get("default_model")
    return str(value or "MiniMax-M2.7-Highspeed")


def _confidence(value: str) -> Confidence:
    return value if value in ("高", "中", "低", "待补充") else "低"  # type: ignore[return-value]
