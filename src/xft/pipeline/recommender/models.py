"""Pydantic models for the configurable recommender."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from xft.core.models import (
    AnalysisDimension,
    AnalysisDimensionsConfig,
    Confidence,
    DimensionAnalysis,
    DimensionStatus,
    EvidenceFact,
    EvidenceTemplate,
    ProductExclusionRule,
    ProductScoreRule,
    RuleOperator,
    ScenarioConfig,
    ScoreBreakdown,
    ScoreRuleTrace,
    ScoringSummary,
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
    "ProductExclusionRule",
    "ProductScoreRule",
    "RuleOperator",
    "ScenarioConfig",
    "ScoreBreakdown",
    "ScoreRuleTrace",
    "ScoringSummary",
    "SupportRule",
    "ProductModule",
    "ProductsConfig",
    "MatchResult",
    "EvidenceTraceItem",
    "ConflictSummaryItem",
    "DimensionEvidenceSummary",
    "EvidenceSummary",
    "RecommendationItem",
    "RecommendationOutput",
    "RecommendationRunResult",
]


class ProductModule(BaseModel):
    """A configurable product module to recommend."""

    module_id: str
    module_name: str
    priority: int = Field(ge=0, le=100)
    target_needs: list[str]
    match_rule: str
    base_score: int | None = Field(default=None, ge=0, le=100)
    positive_rules: list[ProductScoreRule] = Field(default_factory=list)
    negative_rules: list[ProductScoreRule] = Field(default_factory=list)
    exclusion_rules: list[ProductExclusionRule] = Field(default_factory=list)


class ProductsConfig(BaseModel):
    """Root product scenario config."""

    version: str = "1.0"
    scenario: str = "product_recommendation"
    output_dir: str = "recommendation_runs"
    products: list[ProductModule]

    @field_validator("products")
    @classmethod
    def validate_unique_modules(cls, products: list[ProductModule]) -> list[ProductModule]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for product in products:
            if product.module_id in seen:
                duplicates.add(product.module_id)
            seen.add(product.module_id)
        if duplicates:
            msg = f"duplicate module_id(s): {', '.join(sorted(duplicates))}"
            raise ValueError(msg)
        return products


class MatchResult(BaseModel):
    """Product match result."""

    module_id: str
    module_name: str
    matched: bool
    score: int = Field(ge=0, le=100)
    confidence: Confidence
    business_need: str
    reason: str
    supporting_dimensions: list[str] = Field(default_factory=list)
    evidence_summary: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class EvidenceTraceItem(BaseModel):
    """One evidence item supporting a recommendation."""

    evidence_id: str
    dimension_id: str | None = None
    source_type: str
    source_name: str
    source_url: str | None = None
    source_field: str | None = None
    claim: str
    confidence: str
    relation_to_profile: str


class ConflictSummaryItem(BaseModel):
    """One conflict surfaced in the final output."""

    dimension_id: str | None = None
    claim: str
    conflict_note: str | None = None
    resolution: str | None = None
    source_url: str | None = None


class DimensionEvidenceSummary(BaseModel):
    """Evidence counts for one analysis dimension."""

    dimension_id: str
    title: str
    local_evidence_count: int = 0
    web_evidence_count: int = 0
    inference_evidence_count: int = 0
    conflict_count: int = 0
    missing_evidence_count: int = 0
    status: DimensionStatus
    confidence: Confidence


class EvidenceSummary(BaseModel):
    """Global evidence summary for a recommendation run."""

    local_evidence_count: int = 0
    web_evidence_count: int = 0
    inference_evidence_count: int = 0
    conflict_count: int = 0
    missing_evidence_count: int = 0
    by_dimension: list[DimensionEvidenceSummary] = Field(default_factory=list)


class RecommendationItem(BaseModel):
    """Final recommendation item."""

    rank: int
    module_id: str
    module_name: str
    score: int = Field(ge=0, le=100)
    priority: int = Field(ge=0, le=100)
    business_need: str
    reason: str
    suggested_pitch: str
    evidence_dimensions: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    evidence_trace: list[EvidenceTraceItem] = Field(default_factory=list)


class RecommendationOutput(BaseModel):
    """Final recommendation output."""

    company_name: str
    scenario: str
    scenario_name: str | None = None
    summary: str
    recommendations: list[RecommendationItem]
    needs_web_enrichment: bool
    profile_completeness: float
    evidence_summary: EvidenceSummary = Field(default_factory=EvidenceSummary)
    conflict_summary: list[ConflictSummaryItem] = Field(default_factory=list)
    scoring_summary: ScoringSummary = Field(default_factory=ScoringSummary)


class RecommendationRunResult(BaseModel):
    """Public result returned by run_recommendation."""

    company_name: str
    status: Literal["success", "partial", "failed"]
    run_id: str
    output_dir: str
    report_path: str | None = None
    result_path: str | None = None
    error: str | None = None
