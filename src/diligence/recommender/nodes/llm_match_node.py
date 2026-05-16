"""Match product modules against dimension analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from diligence.nodes.summarize_node import _extract_json, get_ai_client
from diligence.recommender.config_loader import load_prompt
from diligence.recommender.models import DimensionAnalysis, MatchResult, ProductModule
from diligence.recommender.state import RecommenderState
from diligence.settings import settings

MATCH_SCORE_THRESHOLD = 55
LLM_TIMEOUT_SECONDS = 60


class _MatchList(BaseModel):
    matches: list[MatchResult]


def _facts_for_dimensions(analyses: list[DimensionAnalysis], target_needs: list[str]) -> list[str]:
    facts: list[str] = []
    for item in analyses:
        if item.dimension_id not in target_needs:
            continue
        facts.extend(fact.claim for fact in item.facts[:4])
        facts.extend(item.inferences[:2])
    return facts


def _fallback_match(products: list[ProductModule], analyses: list[DimensionAnalysis]) -> list[MatchResult]:
    by_id = {item.dimension_id: item for item in analyses}
    results: list[MatchResult] = []
    for product in products:
        related = [by_id[dim_id] for dim_id in product.target_needs if dim_id in by_id]
        supported = sum(1 for item in related if item.status == "supported")
        partial = sum(1 for item in related if item.status == "partial")
        score = min(100, int(product.priority * 0.45 + supported * 22 + partial * 10))
        evidence = _facts_for_dimensions(analyses, product.target_needs)
        missing = sorted({gap for item in related for gap in item.missing_evidence[:3]})
        results.append(
            MatchResult(
                module_id=product.module_id,
                module_name=product.module_name,
                matched=score >= MATCH_SCORE_THRESHOLD,
                score=score,
                confidence="中" if supported else "低" if partial else "待补充",
                business_need=product.match_rule,
                reason="基于本地企业画像和配置维度的规则兜底匹配。",
                supporting_dimensions=[item.title for item in related if item.status != "insufficient"],
                evidence_summary=evidence[:8],
                missing_evidence=missing[:8],
            )
        )
    return results


async def llm_match_node(state: RecommenderState) -> dict[str, object]:
    products = state["products"]
    analyses = state["dimension_analysis"]
    prompt_path = Path("config/recommender/prompts/match_system.md")
    if not state.get("use_llm", True) or not (settings.llm_api_key or settings.minimax_api_key):
        return {"match_results": _fallback_match(products, analyses)}
    try:
        system_prompt = load_prompt(prompt_path)
        payload = {
            "company_profile": state["profile"],
            "dimension_analysis": [item.model_dump() for item in analyses],
            "products": [item.model_dump() for item in products],
        }
        client = get_ai_client()
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            temperature=0.1,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed: Any = json.loads(_extract_json(raw))
        matches = _MatchList.model_validate(parsed).matches
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, KeyError, TypeError, ValueError):
        matches = _fallback_match(products, analyses)
    return {"match_results": matches}
