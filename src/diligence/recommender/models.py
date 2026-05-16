"""Pydantic models for the configurable recommender."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Confidence = Literal["高", "中", "低", "待补充"]
DimensionStatus = Literal["supported", "partial", "insufficient"]


class EvidenceTemplate(BaseModel):
    """A configured local field used as evidence for one analysis dimension."""

    field: str
    label: str


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


class ProductModule(BaseModel):
    """A configurable product module to recommend."""

    module_id: str
    module_name: str
    priority: int = Field(ge=0, le=100)
    target_needs: list[str]
    match_rule: str


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


class EvidenceFact(BaseModel):
    """One factual evidence item derived from local DuckDB profile fields."""

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
    missing_evidence: list[str] = Field(default_factory=list)


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


class RecommendationOutput(BaseModel):
    """Final recommendation output."""

    company_name: str
    scenario: str
    summary: str
    recommendations: list[RecommendationItem]
    needs_web_enrichment: bool
    profile_completeness: float


class RecommendationRunResult(BaseModel):
    """Public result returned by run_recommendation."""

    company_name: str
    status: Literal["success", "partial", "failed"]
    run_id: str
    output_dir: str
    report_path: str | None = None
    result_path: str | None = None
    error: str | None = None

