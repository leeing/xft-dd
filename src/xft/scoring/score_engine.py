"""Config-driven product score engine."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from xft.core.models import (
    DimensionAnalysis,
    ScoreBreakdown,
    ScoreRuleTrace,
    ScoringSubject,
    ScoringSummary,
)
from xft.scoring.models import ProductScoreResult, RuleEvaluation, ScoringContext, ScoringRunResult
from xft.scoring.rule_evaluator import (
    collect_product_evidence,
    collect_product_gaps,
    evaluate_exclusion_rule,
    evaluate_score_rule,
)

SUPPORTED_DIMENSION_SCORE = 5
PARTIAL_DIMENSION_SCORE = 2
LOCAL_EVIDENCE_SCORE_PER_ITEM = 1
LOCAL_EVIDENCE_SCORE_CAP = 8
WEB_CONFIRMATION_SCORE_PER_ITEM = 2
WEB_CONFIRMATION_SCORE_CAP = 8
WEB_SUPPLEMENT_SCORE_PER_ITEM = 1
WEB_SUPPLEMENT_SCORE_CAP = 5
CONFLICT_PENALTY_PER_ITEM = 8
MISSING_EVIDENCE_PENALTY_CAP = 15
EXCLUSION_SCORE_CAP = 20


def score_products(
    *,
    products: Sequence[Any] | None = None,
    subjects: Sequence[ScoringSubject] | None = None,
    context: ScoringContext,
) -> ScoringRunResult:
    """Score all products using configured positive/negative/exclusion rules."""
    score_subjects = (
        list(subjects)
        if subjects is not None
        else [_subject_from_product(product) for product in products or []]
    )
    product_scores = [_score_product(product, context) for product in score_subjects]
    product_scores = sorted(product_scores, key=_score_sort_key, reverse=True)
    summary = ScoringSummary(
        rules_evaluated=sum(
            len(item.product.positive_rules) + len(item.product.negative_rules) + len(item.product.exclusion_rules)
            for item in product_scores
        ),
        rules_matched=sum(
            len(item.score_breakdown.matched_rules)
            + len(item.score_breakdown.penalty_rules)
            + len(item.score_breakdown.exclusion_rules)
            for item in product_scores
        ),
        products_excluded=sum(1 for item in product_scores if item.excluded),
        conflict_count=sum(len(analysis.conflicts) for analysis in context.dimension_analyses),
        missing_evidence_count=sum(len(analysis.missing_evidence) for analysis in context.dimension_analyses),
    )
    return ScoringRunResult(product_scores=product_scores, summary=summary)


def _score_product(product: ScoringSubject, context: ScoringContext) -> ProductScoreResult:
    related = [item for item in context.dimension_analyses if item.dimension_id in product.target_dimensions]
    base_score = product.base_score if product.base_score is not None else int(product.priority * 0.45)
    positive_results = [
        evaluate_score_rule(rule, context, rule_type="positive")
        for rule in product.positive_rules
    ]
    negative_results = [
        evaluate_score_rule(rule, context, rule_type="negative")
        for rule in product.negative_rules
    ]
    exclusion_results = [evaluate_exclusion_rule(rule, context) for rule in product.exclusion_rules]

    matched_positive = [item for item in positive_results if item.matched]
    matched_negative = [item for item in negative_results if item.matched]
    matched_exclusions = [item for item in exclusion_results if item.matched]

    dimension_support = _dimension_support(related)
    evidence_support = min(
        LOCAL_EVIDENCE_SCORE_CAP,
        sum(len(item.local_evidence) for item in related) * LOCAL_EVIDENCE_SCORE_PER_ITEM,
    )
    confirmation_count = sum(
        len([ev for ev in item.web_evidence if ev.relation_to_profile == "confirmation"])
        for item in related
    )
    supplement_count = sum(
        len([ev for ev in item.web_evidence if ev.relation_to_profile == "supplement"])
        for item in related
    )
    web_support = min(WEB_CONFIRMATION_SCORE_CAP, confirmation_count * WEB_CONFIRMATION_SCORE_PER_ITEM) + min(
        WEB_SUPPLEMENT_SCORE_CAP,
        supplement_count * WEB_SUPPLEMENT_SCORE_PER_ITEM,
    )
    conflict_penalty = -sum(len(item.conflicts) for item in related) * CONFLICT_PENALTY_PER_ITEM
    missing_penalty = -min(MISSING_EVIDENCE_PENALTY_CAP, sum(len(item.missing_evidence) for item in related))
    positive_score = sum(item.delta for item in matched_positive)
    negative_score = sum(item.delta for item in matched_negative)
    raw_score = (
        base_score
        + dimension_support
        + evidence_support
        + web_support
        + positive_score
        + negative_score
        + missing_penalty
        + conflict_penalty
    )
    final_score = _clamp(raw_score)
    if matched_exclusions:
        final_score = min(final_score, EXCLUSION_SCORE_CAP)

    breakdown = ScoreBreakdown(
        base_priority=base_score,
        dimension_support=dimension_support,
        evidence_support=evidence_support,
        web_support=web_support,
        missing_evidence_penalty=missing_penalty,
        conflict_penalty=conflict_penalty,
        positive_score=positive_score,
        negative_score=negative_score,
        final_score=final_score,
        excluded=bool(matched_exclusions),
        matched_rules=[_trace(item) for item in matched_positive],
        penalty_rules=[_trace(item) for item in matched_negative],
        exclusion_rules=[_trace(item) for item in matched_exclusions],
    )
    return ProductScoreResult(
        product=product,
        final_score=final_score,
        score_breakdown=breakdown,
        evidence=collect_product_evidence(context.dimension_analyses, product.target_dimensions),
        data_gaps=collect_product_gaps(context.dimension_analyses, product.target_dimensions),
        excluded=bool(matched_exclusions),
        exclusion_reasons=[item.reason for item in matched_exclusions],
    )


def _dimension_support(related: Sequence[DimensionAnalysis]) -> int:
    supported = sum(1 for item in related if item.status == "supported")
    partial = sum(1 for item in related if item.status == "partial")
    return supported * SUPPORTED_DIMENSION_SCORE + partial * PARTIAL_DIMENSION_SCORE


def _score_sort_key(item: ProductScoreResult) -> tuple[int, int, int, int, int, int]:
    breakdown = item.score_breakdown
    return (
        item.final_score,
        0 if item.excluded else 1,
        breakdown.negative_score,
        breakdown.positive_score,
        breakdown.dimension_support,
        item.product.priority,
    )


def _subject_from_product(product: Any) -> ScoringSubject:
    """Adapt the recommender's ProductModule into the generic scoring subject."""
    return ScoringSubject(
        id=str(product.module_id),
        name=str(product.module_name),
        priority=int(product.priority),
        target_dimensions=list(product.target_needs),
        match_rule=str(product.match_rule),
        base_score=product.base_score,
        positive_rules=list(product.positive_rules),
        negative_rules=list(product.negative_rules),
        exclusion_rules=list(product.exclusion_rules),
    )


def _trace(item: RuleEvaluation) -> ScoreRuleTrace:
    return ScoreRuleTrace(
        rule_id=item.rule_id,
        rule_type=item.rule_type,
        delta=item.delta,
        reason=item.reason,
        evidence_ids=item.evidence_ids,
        matched=item.matched,
    )


def _clamp(value: int) -> int:
    return max(0, min(100, value))
