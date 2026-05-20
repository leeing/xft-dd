"""Pydantic models for the recommender pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Evaluator = Literal["rule", "llm", "hybrid", "llm_web"]
Result = Literal["matched", "possible", "not_matched", "unknown"]
Confidence = Literal["高", "中", "低"]
RuleOperator = Literal[
    ">",
    ">=",
    "<",
    "<=",
    "==",
    "!=",
    "contains",
    "contains_any",
    "exists",
    "text_contains",
]
MergePolicy = Literal["rule_first", "llm_confirm", "require_both"]
DataSourceType = Literal["field", "table", "llm", "llm_web"]
WebSearchWhen = Literal["always", "insufficient", "rule_not_matched", "never"]
WebSearchEffect = Literal["llm_evidence", "evidence_only", "possible_on_evidence"]


class RuleConfig(BaseModel):
    """One deterministic rule used by a business indicator."""

    source_field: str
    op: RuleOperator = "exists"
    value: Any | None = None


class DataSourceConfig(BaseModel):
    """Evidence source used by one business indicator."""

    type: DataSourceType = "field"
    path: str | None = None
    table: str | None = None
    field: str | None = None
    op: RuleOperator = "exists"
    value: Any | None = None
    keywords: list[str] = Field(default_factory=list)
    min_matches: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=200)
    description: str = ""

    @model_validator(mode="after")
    def validate_source(self) -> DataSourceConfig:
        if self.type == "field" and not self.path:
            msg = "field data source requires path"
            raise ValueError(msg)
        if self.type == "table" and not (self.table and self.field):
            msg = "table data source requires table and field"
            raise ValueError(msg)
        return self


class WebAutoConfig(BaseModel):
    """LLM-generated query policy for an indicator."""

    enabled: bool = False
    max_queries: int = Field(default=0, ge=0, le=5)
    intent: str = ""


class WebSearchConfig(BaseModel):
    """Indicator-level web search policy."""

    fixed_queries: list[str] = Field(default_factory=list)
    when: WebSearchWhen | None = None
    effect: WebSearchEffect | None = None
    auto: WebAutoConfig = Field(default_factory=WebAutoConfig)
    max_auto_rounds: int = Field(default=0, ge=0, le=3)
    max_results: int = Field(default=5, ge=1, le=20)

    @field_validator("auto", mode="before")
    @classmethod
    def parse_auto(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return {"enabled": value}
        return value

    @model_validator(mode="after")
    def normalize_auto(self) -> WebSearchConfig:
        if self.auto.enabled and self.auto.max_queries == 0:
            self.auto.max_queries = self.max_auto_rounds or 1
        return self


class IndicatorConfig(BaseModel):
    """One business indicator under a label."""

    indicator_id: str
    indicator_name: str
    evaluator: Evaluator = "rule"
    standard: str
    rule: RuleConfig | None = None
    data_sources: list[DataSourceConfig] = Field(default_factory=list)
    web_search: WebSearchConfig | None = None
    prompt: str | None = None
    evidence_hints: list[str] = Field(default_factory=list)
    merge_policy: MergePolicy = "rule_first"

    @model_validator(mode="after")
    def validate_evaluator_payload(self) -> IndicatorConfig:
        if self.evaluator == "rule" and self.rule is None and not self.data_sources:
            msg = f"rule evaluator requires rule: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator in ("llm", "llm_web") and not (self.prompt or self.standard):
            msg = f"llm evaluator requires prompt or standard: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator == "hybrid" and self.rule is None:
            msg = f"hybrid evaluator requires rule: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator == "hybrid" and not (self.prompt or self.standard):
            msg = f"hybrid evaluator requires prompt or standard: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator == "llm_web" and self.web_search is None:
            msg = f"llm_web evaluator requires web_search: {self.indicator_id}"
            raise ValueError(msg)
        return self


class LabelConfig(BaseModel):
    """One business attribute label under a module."""

    label_id: str
    label_name: str
    description: str = ""
    min_matched_indicators: int = Field(default=1, ge=1)
    indicators: list[IndicatorConfig]


class MarketingPointConfig(BaseModel):
    """Business-facing sales message for one matched label."""

    recommendation: str
    sale_rule: str
    kyc_questions: list[str] = Field(default_factory=list)


class ModuleConfig(BaseModel):
    """One business module to recommend."""

    module_id: str
    module_name: str
    priority: int = Field(default=0, ge=0, le=100)
    base_score: int = Field(default=0, ge=0, le=100)
    acceptance_policy: AcceptancePolicyConfig | None = None
    labels: list[LabelConfig]
    marketing_points: dict[str, MarketingPointConfig] = Field(default_factory=dict)


class IndicatorScoringConfig(BaseModel):
    """Score mapping for indicator results."""

    matched: int = 10
    possible: int = 5
    unknown: int = 0
    not_matched: int = 0

    def score_for(self, result: Result) -> int:
        return int(getattr(self, result))


class LabelScoringConfig(BaseModel):
    """Score mapping for label results."""

    matched: int = 30
    possible: int = 15
    unknown: int = 0
    not_matched: int = 0

    def score_for(self, result: Result) -> int:
        return int(getattr(self, result))


class ScoringConfig(BaseModel):
    """Business scoring policy."""

    indicator_scores: IndicatorScoringConfig = Field(default_factory=IndicatorScoringConfig)
    label_scores: LabelScoringConfig = Field(default_factory=LabelScoringConfig)


class AcceptanceLevelConfig(BaseModel):
    """One acceptance level threshold."""

    result: str
    min_matched_labels: int = Field(ge=0)
    conclusion: str


class AcceptancePolicyConfig(BaseModel):
    """Acceptance policy for the final business result."""

    levels: list[AcceptanceLevelConfig]

    @model_validator(mode="after")
    def validate_levels(self) -> AcceptancePolicyConfig:
        if not self.levels:
            msg = "acceptance_policy.levels cannot be empty"
            raise ValueError(msg)
        return self


class RecommendationConfig(BaseModel):
    """Root business recommendation config."""

    version: str = "1.0"
    scenario: str = "sales_recommendation"
    modules_dir: str | None = None
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    acceptance_policy: AcceptancePolicyConfig
    modules: list[ModuleConfig]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> RecommendationConfig:
        module_ids: set[str] = set()
        for module in self.modules:
            if module.module_id in module_ids:
                msg = f"duplicate business module_id: {module.module_id}"
                raise ValueError(msg)
            module_ids.add(module.module_id)
            label_ids: set[str] = set()
            for label in module.labels:
                if label.label_id in label_ids:
                    msg = f"duplicate label_id in {module.module_id}: {label.label_id}"
                    raise ValueError(msg)
                label_ids.add(label.label_id)
                indicator_ids: set[str] = set()
                for indicator in label.indicators:
                    if indicator.indicator_id in indicator_ids:
                        msg = f"duplicate indicator_id in {module.module_id}.{label.label_id}: {indicator.indicator_id}"
                        raise ValueError(msg)
                    indicator_ids.add(indicator.indicator_id)
        return self


class IndicatorResult(BaseModel):
    """Unified indicator result from either rule or LLM."""

    module_id: str
    module_name: str
    label_id: str
    label_name: str
    indicator_id: str
    indicator_name: str
    result: Result
    confidence: Confidence
    score: int = 0
    current_status: str
    standard: str
    evidence: list[str] = Field(default_factory=list)
    evidence_details: list[dict[str, Any]] = Field(default_factory=list)
    web_search_trace: list[dict[str, Any]] = Field(default_factory=list)
    evaluator: Evaluator
    hybrid_trace: dict[str, Any] = Field(default_factory=dict)


class LabelResult(BaseModel):
    """Aggregated result for one business label."""

    module_id: str
    module_name: str
    label_id: str
    label_name: str
    result: Result
    matched_indicators: int = 0
    possible_indicators: int = 0
    score: int = 0
    key_indicator_verify: str
    indicator_results: list[IndicatorResult] = Field(default_factory=list)


class ModuleResult(BaseModel):
    """Aggregated business result for one module."""

    module_id: str
    module_name: str
    score: int = 0
    acceptance_result: str
    attributes_number: int = 0
    indicators_number: int = 0
    conclusion: str
    label_results: list[LabelResult] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    """Business-facing recommendation result across configured modules."""

    company_name: str
    selected_module: ModuleResult | None = None
    modules: list[ModuleResult] = Field(default_factory=list)
    indicator_results: list[IndicatorResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


"""Pydantic models for the business-first recommender."""


class RecommendationRunResult(BaseModel):
    """Public result returned by run_recommendation."""

    company_name: str
    status: Literal["success", "partial", "failed"]
    run_id: str
    output_dir: str
    report_path: str | None = None
    result_path: str | None = None
    error: str | None = None
