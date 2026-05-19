"""Business-facing rule + LLM recommendation models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

BusinessEvaluator = Literal["rule", "llm", "hybrid"]
BusinessResult = Literal["matched", "possible", "not_matched", "unknown"]
BusinessConfidence = Literal["高", "中", "低"]
BusinessRuleOperator = Literal[">", ">=", "<", "<=", "==", "!=", "contains", "contains_any", "exists"]
HybridMergePolicy = Literal["rule_first", "llm_confirm", "require_both"]


class BusinessRuleConfig(BaseModel):
    """One deterministic rule used by a business indicator."""

    source_field: str
    op: BusinessRuleOperator = "exists"
    value: Any | None = None


class BusinessIndicatorConfig(BaseModel):
    """One business indicator under a label."""

    indicator_id: str
    indicator_name: str
    evaluator: BusinessEvaluator = "rule"
    standard: str
    rule: BusinessRuleConfig | None = None
    prompt: str | None = None
    evidence_hints: list[str] = Field(default_factory=list)
    merge_policy: HybridMergePolicy = "rule_first"

    @model_validator(mode="after")
    def validate_evaluator_payload(self) -> BusinessIndicatorConfig:
        if self.evaluator == "rule" and self.rule is None:
            msg = f"rule evaluator requires rule: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator == "llm" and not (self.prompt or self.standard):
            msg = f"llm evaluator requires prompt or standard: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator == "hybrid" and self.rule is None:
            msg = f"hybrid evaluator requires rule: {self.indicator_id}"
            raise ValueError(msg)
        if self.evaluator == "hybrid" and not (self.prompt or self.standard):
            msg = f"hybrid evaluator requires prompt or standard: {self.indicator_id}"
            raise ValueError(msg)
        return self


class BusinessLabelConfig(BaseModel):
    """One business attribute label under a module."""

    label_id: str
    label_name: str
    description: str = ""
    min_matched_indicators: int = Field(default=1, ge=1)
    indicators: list[BusinessIndicatorConfig]


class BusinessMarketingPointConfig(BaseModel):
    """Business-facing sales message for one matched label."""

    recommendation: str
    sale_rule: str
    kyc_questions: list[str] = Field(default_factory=list)


class BusinessModuleConfig(BaseModel):
    """One business module to recommend."""

    module_id: str
    module_name: str
    priority: int = Field(default=0, ge=0, le=100)
    base_score: int = Field(default=0, ge=0, le=100)
    labels: list[BusinessLabelConfig]
    marketing_points: dict[str, BusinessMarketingPointConfig] = Field(default_factory=dict)


class IndicatorScoringConfig(BaseModel):
    """Score mapping for indicator results."""

    matched: int = 10
    possible: int = 5
    unknown: int = 0
    not_matched: int = 0

    def score_for(self, result: BusinessResult) -> int:
        return int(getattr(self, result))


class LabelScoringConfig(BaseModel):
    """Score mapping for label results."""

    matched: int = 30
    possible: int = 15
    unknown: int = 0
    not_matched: int = 0

    def score_for(self, result: BusinessResult) -> int:
        return int(getattr(self, result))


class BusinessScoringConfig(BaseModel):
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


class BusinessRecommendationConfig(BaseModel):
    """Root business recommendation config."""

    version: str = "1.0"
    scenario: str = "sales_recommendation"
    scoring: BusinessScoringConfig = Field(default_factory=BusinessScoringConfig)
    acceptance_policy: AcceptancePolicyConfig
    modules: list[BusinessModuleConfig]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> BusinessRecommendationConfig:
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


class BusinessIndicatorResult(BaseModel):
    """Unified indicator result from either rule or LLM."""

    module_id: str
    module_name: str
    label_id: str
    label_name: str
    indicator_id: str
    indicator_name: str
    result: BusinessResult
    confidence: BusinessConfidence
    score: int = 0
    current_status: str
    standard: str
    evidence: list[str] = Field(default_factory=list)
    evaluator: BusinessEvaluator
    hybrid_trace: dict[str, Any] = Field(default_factory=dict)


class BusinessLabelResult(BaseModel):
    """Aggregated result for one business label."""

    module_id: str
    module_name: str
    label_id: str
    label_name: str
    result: BusinessResult
    matched_indicators: int = 0
    possible_indicators: int = 0
    score: int = 0
    key_indicator_verify: str
    indicator_results: list[BusinessIndicatorResult] = Field(default_factory=list)


class BusinessModuleResult(BaseModel):
    """Aggregated business result for one module."""

    module_id: str
    module_name: str
    score: int = 0
    acceptance_result: str
    attributes_number: int = 0
    indicators_number: int = 0
    conclusion: str
    label_results: list[BusinessLabelResult] = Field(default_factory=list)


class BusinessRecommendationResult(BaseModel):
    """Business-facing recommendation result across configured modules."""

    company_name: str
    selected_module: BusinessModuleResult | None = None
    modules: list[BusinessModuleResult] = Field(default_factory=list)
    indicator_results: list[BusinessIndicatorResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
