"""Evaluate product scoring rules against profile and evidence context."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from diligence.evidence.models import EvidenceRecord
from diligence.recommender.models import DimensionAnalysis, ProductExclusionRule, ProductScoreRule, RuleOperator
from diligence.recommender.scoring.models import RuleEvaluation, ScoringContext


def evaluate_score_rule(
    rule: ProductScoreRule,
    context: ScoringContext,
    *,
    rule_type: Literal["positive", "negative"],
) -> RuleEvaluation:
    """Evaluate a positive or negative scoring rule."""
    matched, evidence_ids = _matches_rule(rule, context)
    delta = rule.weight if rule_type == "positive" else -rule.penalty
    return RuleEvaluation(
        rule_id=rule.id,
        rule_type=rule_type,
        matched=matched,
        delta=delta if matched else 0,
        reason=rule.reason,
        evidence_ids=evidence_ids,
    )


def evaluate_exclusion_rule(rule: ProductExclusionRule, context: ScoringContext) -> RuleEvaluation:
    """Evaluate a product exclusion rule."""
    matched, evidence_ids = _matches_rule(rule, context)
    return RuleEvaluation(
        rule_id=rule.id,
        rule_type="exclusion",
        matched=matched,
        delta=0,
        reason=rule.reason,
        evidence_ids=evidence_ids,
    )


def collect_product_evidence(
    dimension_analyses: list[DimensionAnalysis],
    target_needs: list[str],
    *,
    limit: int = 12,
) -> list[EvidenceRecord]:
    """Collect ordered evidence for a product's target dimensions."""
    evidence: list[EvidenceRecord] = []
    target_set = set(target_needs)
    for analysis in dimension_analyses:
        if analysis.dimension_id not in target_set:
            continue
        evidence.extend(analysis.local_evidence)
        evidence.extend([item for item in analysis.web_evidence if item.relation_to_profile == "confirmation"])
        evidence.extend([item for item in analysis.web_evidence if item.relation_to_profile == "supplement"])
        evidence.extend(analysis.inference_evidence)
    return evidence[:limit]


def collect_product_gaps(dimension_analyses: list[DimensionAnalysis], target_needs: list[str]) -> list[str]:
    """Collect missing evidence items for a product."""
    target_set = set(target_needs)
    gaps = {
        gap
        for analysis in dimension_analyses
        if analysis.dimension_id in target_set
        for gap in analysis.missing_evidence
    }
    return sorted(gaps)


def _matches_rule(
    rule: ProductScoreRule | ProductExclusionRule,
    context: ScoringContext,
) -> tuple[bool, list[str]]:
    checks: list[bool] = []
    evidence_ids: list[str] = []

    if rule.dimension_id:
        matched, ids = _matches_dimension_rule(rule, context.dimension_analyses)
        checks.append(matched)
        evidence_ids.extend(ids)

    if rule.source_field:
        checks.append(_matches_source_field(rule.source_field, rule.op, rule.value, context.company_profile))

    if rule.missing_evidence:
        matched = any(
            _text_contains(gap, rule.missing_evidence)
            for analysis in context.dimension_analyses
            for gap in analysis.missing_evidence
            if not rule.dimension_id or analysis.dimension_id == rule.dimension_id
        )
        checks.append(matched)

    if rule.relation_to_profile and not rule.dimension_id:
        matched, ids = _matches_relation(rule.relation_to_profile, context.dimension_analyses)
        checks.append(matched)
        evidence_ids.extend(ids)

    if not checks:
        return False, []
    return all(checks), sorted(set(evidence_ids))


def _matches_dimension_rule(
    rule: ProductScoreRule | ProductExclusionRule,
    analyses: list[DimensionAnalysis],
) -> tuple[bool, list[str]]:
    analysis = next((item for item in analyses if item.dimension_id == rule.dimension_id), None)
    if analysis is None:
        return False, []
    evidence_ids: list[str] = []
    checks: list[bool] = []
    if rule.evidence_type in (None, "supported", "partial", "insufficient"):
        expected = rule.evidence_type or "supported"
        checks.append(analysis.status == expected)
        evidence_ids.extend(_evidence_ids_for_analysis(analysis))
    elif rule.evidence_type:
        matched, ids = _matches_relation(rule.evidence_type, [analysis])
        checks.append(matched)
        evidence_ids.extend(ids)
    if rule.relation_to_profile:
        matched, ids = _matches_relation(rule.relation_to_profile, [analysis])
        checks.append(matched)
        evidence_ids.extend(ids)
    return (all(checks) if checks else analysis.status != "insufficient"), sorted(set(evidence_ids))


def _matches_relation(relation: str, analyses: list[DimensionAnalysis]) -> tuple[bool, list[str]]:
    matched: list[EvidenceRecord] = []
    for analysis in analyses:
        records = [
            *analysis.local_evidence,
            *analysis.web_evidence,
            *analysis.inference_evidence,
            *analysis.conflicts,
        ]
        matched.extend(item for item in records if item.relation_to_profile == relation)
    return bool(matched), [item.evidence_id for item in matched]


def _matches_source_field(field: str, op: RuleOperator | None, expected: Any, profile: dict[str, object]) -> bool:
    value = profile.get(field)
    if op is None:
        op = "exists"
    return _compare(value, op, expected)


def _compare(value: Any, op: RuleOperator, expected: Any) -> bool:  # noqa: PLR0911
    if op == "exists":
        return value not in (None, "", [], {})
    if op == "contains":
        return _contains(value, expected)
    if op in ("==", "!="):
        result = _normalize(value) == _normalize(expected)
        return result if op == "==" else not result
    left = _as_number(value)
    right = _as_number(expected)
    if left is None or right is None:
        return False
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return False


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, str):
        return str(expected) in value
    if isinstance(value, Iterable):
        return any(str(expected) in str(item) for item in value)
    return False


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "是", "1"):
            return True
        if lowered in ("false", "否", "0"):
            return False
        return lowered
    return value


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _text_contains(value: str, needle: str) -> bool:
    return needle in value or value in needle


def _evidence_ids_for_analysis(analysis: DimensionAnalysis) -> list[str]:
    records = [*analysis.local_evidence, *analysis.inference_evidence, *analysis.web_evidence]
    return [item.evidence_id for item in records[:8]]
