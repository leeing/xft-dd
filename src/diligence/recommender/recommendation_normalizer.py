"""Normalize and validate final recommendation payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from diligence.recommender.models import MatchResult, ProductModule, RecommendationItem, RecommendationOutput

DEFAULT_BUSINESS_NEED = "需结合本地企业画像进一步确认业务需求。"
DEFAULT_REASON = "本地证据有限，建议先核实关键数据后再形成正式推荐。"
DEFAULT_PITCH = "建议先围绕现有画像线索进行需求确认，再补充关键证据。"


def normalize_recommendation_payload(  # noqa: PLR0913
    payload: Mapping[str, Any] | RecommendationOutput,
    *,
    company_name: str,
    scenario: str,
    products: list[ProductModule],
    match_results: list[MatchResult],
    needs_web_enrichment: bool,
    profile_completeness: float,
    fallback: RecommendationOutput | None = None,
) -> RecommendationOutput:
    """Normalize LLM/fallback recommendation output into a stable interface."""
    product_by_id = {item.module_id: item for item in products}
    match_by_id = {item.module_id: item for item in match_results}
    raw_summary, raw_items = _extract_payload(payload)
    seen: set[str] = set()
    items: list[RecommendationItem] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        module_id = str(raw.get("module_id") or "").strip()
        if not module_id or module_id in seen or module_id not in product_by_id:
            continue
        product = product_by_id[module_id]
        match = match_by_id.get(module_id)
        items.append(_build_item(raw, product=product, match=match))
        seen.add(module_id)

    if not items and fallback is not None:
        return normalize_recommendation_payload(
            fallback,
            company_name=company_name,
            scenario=scenario,
            products=products,
            match_results=match_results,
            needs_web_enrichment=needs_web_enrichment,
            profile_completeness=profile_completeness,
            fallback=None,
        )

    sorted_items = sorted(
        items,
        key=lambda item: (-item.score, -item.priority, item.module_id),
    )
    ranked = [item.model_copy(update={"rank": idx}) for idx, item in enumerate(sorted_items, 1)]
    summary = str(raw_summary or "").strip()
    if not summary:
        summary = fallback.summary if fallback else f"{company_name} 当前基于本地 DuckDB 企业画像生成产品模块推荐。"

    return RecommendationOutput(
        company_name=company_name,
        scenario=scenario,
        summary=summary,
        recommendations=ranked,
        needs_web_enrichment=needs_web_enrichment,
        profile_completeness=max(0.0, min(float(profile_completeness), 1.0)),
    )


def _extract_payload(payload: Mapping[str, Any] | RecommendationOutput) -> tuple[str, list[Any]]:
    if isinstance(payload, RecommendationOutput):
        return payload.summary, [item.model_dump() for item in payload.recommendations]
    if isinstance(payload, BaseModel):
        d = payload.model_dump()
        return str(d.get("summary") or ""), list(d.get("recommendations") or [])
    return str(payload.get("summary") or ""), list(payload.get("recommendations") or [])


def _build_item(raw: Mapping[str, Any], *, product: ProductModule, match: MatchResult | None) -> RecommendationItem:
    score = _coerce_score(raw.get("score"), match.score if match else product.priority)
    business_need = _first_text(raw.get("business_need"), match.business_need if match else None, DEFAULT_BUSINESS_NEED)
    reason = _first_text(raw.get("reason"), match.reason if match else None, product.match_rule, DEFAULT_REASON)
    suggested_pitch = _first_text(raw.get("suggested_pitch"), DEFAULT_PITCH)
    evidence_dimensions = _string_list(raw.get("evidence_dimensions")) or (match.supporting_dimensions if match else [])
    data_gaps = _string_list(raw.get("data_gaps")) or (match.missing_evidence if match else [])
    return RecommendationItem(
        rank=1,
        module_id=product.module_id,
        module_name=_first_text(raw.get("module_name"), product.module_name),
        score=score,
        priority=_coerce_score(raw.get("priority"), product.priority),
        business_need=business_need,
        reason=reason,
        suggested_pitch=suggested_pitch,
        evidence_dimensions=evidence_dimensions,
        data_gaps=data_gaps,
    )


def _coerce_score(value: Any, fallback: int) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = fallback
    return max(0, min(numeric, 100))


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]

