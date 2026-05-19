"""Pydantic models for the configurable recommender."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from xft.core.models import (
    AnalysisDimension,
    AnalysisDimensionsConfig,
    Confidence,
    DimensionAnalysis,
    DimensionStatus,
    EvidenceFact,
    EvidenceTemplate,
    ScenarioConfig,
    SupportRule,
)

__all__ = [
    "AnalysisDimension",
    "AnalysisDimensionsConfig",
    "Confidence",
    "DimensionAnalysis",
    "DimensionStatus",
    "EvidenceFact",
    "EvidenceTemplate",
    "ScenarioConfig",
    "SupportRule",
    "RecommendationRunResult",
]


class RecommendationRunResult(BaseModel):
    """Public result returned by run_recommendation."""

    company_name: str
    status: Literal["success", "partial", "failed"]
    run_id: str
    output_dir: str
    report_path: str | None = None
    result_path: str | None = None
    error: str | None = None
