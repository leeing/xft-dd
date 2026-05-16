"""Extract concise Web evidence from normalized search and fetched-page records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from diligence.ai.client import get_ai_client
from diligence.ai.json_extractor import extract_json
from diligence.recommender.models import Confidence, DimensionAnalysis
from diligence.recommender.web.models import (
    EvidenceType,
    WebEvidenceRecord,
    WebExtractLLMConfig,
    WebSearchQueryRecord,
    WebSearchResultRecord,
)

CLAIM_MAX_CHARS = 300


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
    request_payload = _build_request_payload(profile=profile, analysis=analysis, results=results, llm_config=llm_config)
    if not use_llm or not llm_config.enabled:
        return _fallback_extract(results, queries_by_id), request_payload, {"mode": "fallback"}
    try:
        system_prompt = Path(llm_config.prompt_file).read_text(encoding="utf-8")
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
        return _fallback_extract(results, queries_by_id), request_payload, {"mode": "fallback"}
    evidence = _records_from_claims(parsed.claims, results=results, queries_by_id=queries_by_id, llm_config=llm_config)
    return evidence, request_payload, parsed.model_dump(mode="json")


def _build_request_payload(
    *,
    profile: dict[str, Any],
    analysis: DimensionAnalysis,
    results: list[WebSearchResultRecord],
    llm_config: WebExtractLLMConfig,
) -> dict[str, Any]:
    task = llm_config.tasks.get("web_evidence_extract")
    max_sources = task.max_sources_per_call if task else 8
    max_chars = task.max_chars_per_source if task else 4000
    return {
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
) -> list[WebEvidenceRecord]:
    result_by_id = {item.result_id: item for item in results}
    evidence: list[WebEvidenceRecord] = []
    for idx, claim in enumerate(claims, 1):
        result = result_by_id.get(claim.source_result_id)
        if result is None:
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
                resolution="use_json" if claim.type == "conflict" else claim.resolution,
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
