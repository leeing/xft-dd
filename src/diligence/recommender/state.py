"""LangGraph state for the recommender pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from diligence.recommender.models import (
    AnalysisDimensionsConfig,
    DimensionAnalysis,
    MatchResult,
    ProductModule,
    ProductsConfig,
    RecommendationOutput,
)


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
    products_config: ProductsConfig
    dimensions_config: AnalysisDimensionsConfig
    products: list[ProductModule]
    profile: Annotated[dict[str, Any], merge_dicts]
    dimension_analysis: list[DimensionAnalysis]
    match_results: list[MatchResult]
    recommendation: RecommendationOutput | None
    needs_web_enrichment: bool
    errors: Annotated[list[str], operator.add]
    output_dir: Annotated[str, keep_nonempty_str]
    report_path: Annotated[str, keep_nonempty_str]
    result_path: Annotated[str, keep_nonempty_str]
