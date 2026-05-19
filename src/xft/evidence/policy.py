"""Configurable evidence policy for planning, resolving, and reporting."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from xft.evidence.models import EvidenceConfidence


class WebPlanningPolicy(BaseModel):
    """Rules for deciding whether Web search should run."""

    supported_facts_to_skip_web: int = Field(default=3, ge=0)


class DimensionAnalysisPolicy(BaseModel):
    """Rules for local dimension support classification."""

    supported_facts_threshold: int = Field(default=3, ge=1)


class QualityScorePolicy(BaseModel):
    """Weights used to compute evidence quality scores."""

    primary: float = 15
    confirmation: float = 10
    supplement: float = 5
    inference: float = 3
    conflict_penalty: float = 10
    min_score: float = 0
    max_score: float = 100


def _default_authority_boost() -> dict[str, dict[EvidenceConfidence, EvidenceConfidence]]:
    return {
        "high": {"低": "中", "中": "高", "待补充": "中", "待核实": "中"},
        "medium": {"低": "中", "待补充": "低", "待核实": "低"},
    }


class ResolverPolicy(BaseModel):
    """Rules for evidence conflict resolution and confidence boosting."""

    source_priority: dict[str, int] = Field(
        default_factory=lambda: {
            "local_json": 0,
            "manual": 1,
            "rule": 2,
            "web": 3,
            "llm_extraction": 4,
        }
    )
    authority_boost: dict[str, dict[EvidenceConfidence, EvidenceConfidence]] = Field(
        default_factory=_default_authority_boost
    )
    quality_score: QualityScorePolicy = Field(default_factory=QualityScorePolicy)


class RecommenderEvidencePolicy(BaseModel):
    """Rules for translating resolved evidence quality into recommender state."""

    max_web_evidence_per_dimension: int = Field(default=5, ge=0)
    supported_quality_threshold: float = 45
    partial_quality_threshold: float = 15
    high_confidence_quality_threshold: float = 60
    medium_confidence_quality_threshold: float = 30
    low_confidence_quality_threshold: float = 10


class EvidencePolicy(BaseModel):
    """Root evidence policy loaded from YAML."""

    version: str = "1.0"
    web_planning: WebPlanningPolicy = Field(default_factory=WebPlanningPolicy)
    dimension_analysis: DimensionAnalysisPolicy = Field(default_factory=DimensionAnalysisPolicy)
    resolver: ResolverPolicy = Field(default_factory=ResolverPolicy)
    recommender: RecommenderEvidencePolicy = Field(default_factory=RecommenderEvidencePolicy)


def load_evidence_policy(path: str | Path | None = None) -> EvidencePolicy:
    """Load evidence policy from YAML, falling back to built-in defaults."""
    if path is None:
        return EvidencePolicy()
    else:
        from xft.core.scenario import maybe_scenario_path

        scenario = maybe_scenario_path(path)
        if scenario is not None:
            return load_evidence_policy(scenario.evidence_policy_path)
        path = Path(path)
    if not path.exists():
        return EvidencePolicy()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return EvidencePolicy()
    return EvidencePolicy.model_validate(data)
