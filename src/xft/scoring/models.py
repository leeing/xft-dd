"""Models used by the config-driven scoring engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from xft.core.models import DimensionAnalysis, ScoreBreakdown, ScoringSubject, ScoringSummary
from xft.evidence.models import EvidenceRecord


class ScoringContext(BaseModel):
    """Inputs available to product scoring rules."""

    company_profile: dict[str, object]
    dimension_analyses: list[DimensionAnalysis]


class RuleEvaluation(BaseModel):
    """Evaluation result for one configured scoring rule."""

    rule_id: str
    rule_type: Literal["positive", "negative", "exclusion"]
    matched: bool
    delta: int = 0
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class ProductScoreResult(BaseModel):
    """Structured score for one product."""

    product: ScoringSubject
    final_score: int
    score_breakdown: ScoreBreakdown
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    excluded: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)


class ScoringPolicy(BaseModel):
    """Runtime-configurable scoring weights."""

    version: str = "1.0"
    dimension_support: dict[str, int] = Field(default_factory=dict)
    evidence_support: dict[str, int] = Field(default_factory=dict)
    web_support: dict[str, int] = Field(default_factory=dict)
    penalties: dict[str, int] = Field(default_factory=dict)
    exclusion: dict[str, int] = Field(default_factory=dict)


class ScoringRunResult(BaseModel):
    """All product scores plus run-level diagnostics."""

    product_scores: list[ProductScoreResult]
    summary: ScoringSummary
