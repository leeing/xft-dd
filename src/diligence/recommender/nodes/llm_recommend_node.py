"""Create final recommendations from match results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, ValidationError

from diligence.ai.client import get_ai_client
from diligence.ai.json_extractor import extract_json
from diligence.recommender.config_loader import load_prompt
from diligence.recommender.models import ProductModule, RecommendationItem, RecommendationOutput
from diligence.recommender.recommendation_normalizer import normalize_recommendation_payload
from diligence.recommender.state import RecommenderState
from diligence.settings import settings

LLM_TIMEOUT_SECONDS = 60


class _RecommendationPayload(BaseModel):
    summary: str
    recommendations: list[RecommendationItem]


def _priority_by_id(products: list[ProductModule]) -> dict[str, int]:
    return {item.module_id: item.priority for item in products}


def _fallback_recommendation(state: RecommenderState) -> RecommendationOutput:
    priorities = _priority_by_id(state["products"])
    candidates = [item for item in state["match_results"] if item.matched]
    if not candidates:
        candidates = sorted(state["match_results"], key=lambda item: item.score, reverse=True)[:3]
    ranked = sorted(
        candidates,
        key=lambda item: item.score * 0.8 + priorities.get(item.module_id, 0) * 0.2,
        reverse=True,
    )
    recs: list[RecommendationItem] = []
    for idx, item in enumerate(ranked[:5], 1):
        recs.append(
            RecommendationItem(
                rank=idx,
                module_id=item.module_id,
                module_name=item.module_name,
                score=item.score,
                priority=priorities.get(item.module_id, 0),
                business_need=item.business_need,
                reason=item.reason,
                suggested_pitch=f"可围绕“{item.business_need}”切入，先核实缺失证据后再形成正式方案。",
                evidence_dimensions=item.supporting_dimensions,
                data_gaps=item.missing_evidence,
            )
        )
    profile = state.get("profile", {})
    summary = (
        f"{profile.get('company_name', state['company_name'])} 当前基于本地 DuckDB 画像生成推荐；"
        "证据不足项已保留，后续可通过 Web 搜索补充。"
    )
    output = RecommendationOutput(
        company_name=str(profile.get("company_name") or state["company_name"]),
        scenario=state["products_config"].scenario,
        summary=summary,
        recommendations=recs,
        needs_web_enrichment=state["needs_web_enrichment"],
        profile_completeness=float(profile.get("profile_completeness") or 0),
    )
    return normalize_recommendation_payload(
        output,
        company_name=output.company_name,
        scenario=output.scenario,
        products=state["products"],
        match_results=state["match_results"],
        needs_web_enrichment=output.needs_web_enrichment,
        profile_completeness=output.profile_completeness,
    )


async def llm_recommend_node(state: RecommenderState) -> dict[str, object]:
    if not state.get("use_llm", True) or not (settings.llm_api_key or settings.minimax_api_key):
        return {"recommendation": _fallback_recommendation(state)}
    try:
        system_prompt = load_prompt(Path("config/recommender/prompts/recommend_system.md"))
        priorities = _priority_by_id(state["products"])
        payload = {
            "company_profile": state["profile"],
            "dimension_analysis": [item.model_dump() for item in state["dimension_analysis"]],
            "match_results": [item.model_dump() for item in state["match_results"]],
            "product_priorities": priorities,
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
        parsed: Any = json.loads(extract_json(raw))
        fallback = _fallback_recommendation(state)
        try:
            rec_payload = _RecommendationPayload.model_validate(parsed)
            parsed_payload: Any = rec_payload
        except ValidationError:
            parsed_payload = parsed
        recommendation = normalize_recommendation_payload(
            parsed_payload,
            company_name=str(state["profile"].get("company_name") or state["company_name"]),
            scenario=state["products_config"].scenario,
            products=state["products"],
            match_results=state["match_results"],
            needs_web_enrichment=state["needs_web_enrichment"],
            profile_completeness=float(state["profile"].get("profile_completeness") or 0),
            fallback=fallback,
        )
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, KeyError, TypeError, ValueError):
        recommendation = _fallback_recommendation(state)
    return {"recommendation": recommendation}
