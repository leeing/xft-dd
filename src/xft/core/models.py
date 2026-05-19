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


class ScenarioConfig(BaseModel):
    """Generic scenario bundle entry config."""

    version: str = "1.0"
    id: str
    name: str
    description: str | None = None
    extends: str | None = None
    dimensions_config: str = "analysis_dimensions.yaml"
    web_search_config: str = "web_search.yaml"
    web_extract_llm_config: str = "web_extract_llm.yaml"
    evidence_policy_config: str = "evidence_policy.yaml"
    business_modules_config: str | None = None
    prompts: dict[str, str] = Field(default_factory=dict)
    output_dir: str | None = None
    web_cache_root: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    patches: dict[str, Any] = Field(default_factory=dict)
