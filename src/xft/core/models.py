"""Scenario-agnostic models shared by analysis pipelines."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from xft.evidence.models import EvidenceRecord

Confidence = Literal["高", "中", "低", "待补充"]
DimensionStatus = Literal["supported", "partial", "insufficient"]
RuleOperator = Literal[">", ">=", "<", "<=", "==", "!=", "contains", "exists"]


class EvidenceTemplate(BaseModel):
    """A configured local field used as evidence for one analysis dimension."""

    field: str
    label: str


class SupportRule(BaseModel):
    """A configured inference rule for one analysis dimension."""

    field: str
    op: RuleOperator
    value: Any | None = None
    claim: str
    confidence: Confidence = "低"


class AnalysisDimension(BaseModel):
    """A configurable business analysis dimension."""

    id: str
    level1: str
    level2: str
    level3: str
    role: str
    local_fields: list[str] = Field(default_factory=list)
    evidence_templates: list[EvidenceTemplate] = Field(default_factory=list)
    insufficient_evidence: list[str] = Field(default_factory=list)
    analysis_prompt: str | None = None
    evidence_policy: str | None = None
    support_rules: list[SupportRule] = Field(default_factory=list)
    web_search_queries: list[str] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.level1} / {self.level2} / {self.level3}"


class AnalysisDimensionsConfig(BaseModel):
    """Root config for analysis dimensions."""

    version: str = "1.0"
    dimensions: list[AnalysisDimension]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> AnalysisDimensionsConfig:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for dim in self.dimensions:
            if dim.id in seen:
                duplicates.add(dim.id)
            seen.add(dim.id)
        if duplicates:
            msg = f"duplicate analysis dimension id(s): {', '.join(sorted(duplicates))}"
            raise ValueError(msg)
        return self


class EvidenceFact(BaseModel):
    """One factual evidence item derived from local warehouse profile fields."""

    claim: str
    source: str = "company_profile"
    source_fields: list[str]


class DimensionAnalysis(BaseModel):
    """Structured analysis result for one configured dimension."""

    dimension_id: str
    title: str
    status: DimensionStatus
    confidence: Confidence
    facts: list[EvidenceFact] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    local_evidence: list[EvidenceRecord] = Field(default_factory=list)
    inference_evidence: list[EvidenceRecord] = Field(default_factory=list)
    web_evidence: list[EvidenceRecord] = Field(default_factory=list)
    conflicts: list[EvidenceRecord] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    analysis_prompt: str | None = None
    evidence_policy: str | None = None
    web_search_queries: list[str] = Field(default_factory=list)


class ProductScoreRule(BaseModel):
    """Configurable scoring rule for a score subject."""

    id: str
    reason: str
    weight: int = Field(default=0, ge=0, le=100)
    penalty: int = Field(default=0, ge=0, le=100)
    dimension_id: str | None = None
    source_field: str | None = None
    evidence_type: str | None = None
    relation_to_profile: str | None = None
    missing_evidence: str | None = None
    op: RuleOperator | None = None
    value: Any | None = None


class ProductExclusionRule(BaseModel):
    """Configurable exclusion rule for a score subject."""

    id: str
    reason: str
    dimension_id: str | None = None
    source_field: str | None = None
    evidence_type: str | None = None
    relation_to_profile: str | None = None
    missing_evidence: str | None = None
    op: RuleOperator | None = None
    value: Any | None = None


class ScoringSubject(BaseModel):
    """Generic object that can be scored by the rule engine."""

    id: str
    name: str
    priority: int = Field(ge=0, le=100)
    target_dimensions: list[str]
    match_rule: str = ""
    base_score: int | None = Field(default=None, ge=0, le=100)
    positive_rules: list[ProductScoreRule] = Field(default_factory=list)
    negative_rules: list[ProductScoreRule] = Field(default_factory=list)
    exclusion_rules: list[ProductExclusionRule] = Field(default_factory=list)

    @property
    def module_id(self) -> str:
        return self.id

    @property
    def module_name(self) -> str:
        return self.name

    @property
    def target_needs(self) -> list[str]:
        return self.target_dimensions


class ScoreRuleTrace(BaseModel):
    """One matched scoring rule trace."""

    rule_id: str
    rule_type: Literal["positive", "negative", "exclusion"]
    delta: int = 0
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    matched: bool = True


class ScoreBreakdown(BaseModel):
    """Machine-readable score explanation for one scored subject."""

    base_priority: int = 0
    dimension_support: int = 0
    evidence_support: int = 0
    web_support: int = 0
    missing_evidence_penalty: int = 0
    conflict_penalty: int = 0
    final_score: int = Field(default=0, ge=0, le=100)
    positive_score: int = 0
    negative_score: int = 0
    excluded: bool = False
    matched_rules: list[ScoreRuleTrace] = Field(default_factory=list)
    penalty_rules: list[ScoreRuleTrace] = Field(default_factory=list)
    exclusion_rules: list[ScoreRuleTrace] = Field(default_factory=list)


class ScoringSummary(BaseModel):
    """Run-level scoring diagnostics."""

    rules_evaluated: int = 0
    rules_matched: int = 0
    products_excluded: int = 0
    conflict_count: int = 0
    missing_evidence_count: int = 0


class ScenarioConfig(BaseModel):
    """Generic scenario bundle entry config."""

    version: str = "1.0"
    id: str
    name: str
    description: str | None = None
    extends: str | None = None
    products_config: str = "products.yaml"
    dimensions_config: str = "analysis_dimensions.yaml"
    web_search_config: str = "web_search.yaml"
    web_extract_llm_config: str = "web_extract_llm.yaml"
    prompts: dict[str, str] = Field(default_factory=dict)
    output_dir: str | None = None
    web_cache_root: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
