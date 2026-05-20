"""LangGraph state for the recommender pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from xft.pipeline.recommender.business_models import BusinessRecommendationConfig, BusinessRecommendationResult


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {**a, **b}


def keep_nonempty_str(a: str, b: str) -> str:
    return b if b else a


class RecommenderState(TypedDict):
    company_name: str
    warehouse_db: str
    output_root: str
    run_id: str
    use_llm: bool
    llm_debug: bool
    llm_concurrency: int
    llm_call_events: Annotated[list[dict[str, Any]], operator.add]
    with_business_web: bool
    refresh_business_web: bool
    business_web_config_path: str
    business_web_providers: list[str] | None
    scenario_id: str | None
    scenario_name: str | None
    prompt_paths: dict[str, str]
    business_config: BusinessRecommendationConfig | None
    profile: Annotated[dict[str, Any], merge_dicts]
    business_evidence: dict[str, list[dict[str, Any]]]
    business_web_evidence: dict[str, list[dict[str, Any]]]
    business_web_trace: list[dict[str, Any]]
    business_recommendation: BusinessRecommendationResult | None
    needs_web_enrichment: bool
    errors: Annotated[list[str], operator.add]
    output_dir: Annotated[str, keep_nonempty_str]
    report_path: Annotated[str, keep_nonempty_str]
    result_path: Annotated[str, keep_nonempty_str]
