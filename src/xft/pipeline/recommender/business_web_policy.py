"""Indicator-level Web search policy decisions."""

from __future__ import annotations

from dataclasses import dataclass

from xft.pipeline.recommender.business_models import (
    BusinessIndicatorConfig,
    BusinessResult,
    BusinessWebSearchEffect,
    BusinessWebSearchWhen,
)


@dataclass(frozen=True)
class WebSearchDecision:
    """Resolved Web search decision for one business indicator."""

    enabled: bool
    when: BusinessWebSearchWhen
    effect: BusinessWebSearchEffect
    reason: str


def should_search_indicator(  # noqa: PLR0911
    *,
    indicator: BusinessIndicatorConfig,
    local_evidence: list[dict[str, object]],
    rule_result: BusinessResult | None,
) -> WebSearchDecision:
    """Resolve whether an indicator should execute Web search."""
    web = indicator.web_search
    if web is None:
        return WebSearchDecision(
            enabled=False,
            when="never",
            effect=_default_effect(indicator),
            reason="no_web_search_config",
        )

    when = web.when or _default_when(indicator)
    effect = web.effect or _default_effect(indicator)
    if when == "never":
        return WebSearchDecision(enabled=False, when=when, effect=effect, reason="web_search_disabled")
    if when == "always":
        reason = "llm_web_web_first" if indicator.evaluator == "llm_web" else "always"
        return WebSearchDecision(enabled=True, when=when, effect=effect, reason=reason)
    if when == "rule_not_matched":
        if rule_result in ("not_matched", "unknown", None):
            return WebSearchDecision(enabled=True, when=when, effect=effect, reason="rule_not_matched")
        return WebSearchDecision(enabled=False, when=when, effect=effect, reason="rule_already_matched")
    if _is_insufficient(local_evidence):
        return WebSearchDecision(enabled=True, when=when, effect=effect, reason="local_evidence_insufficient")
    return WebSearchDecision(enabled=False, when=when, effect=effect, reason="local_evidence_sufficient")


def _default_when(indicator: BusinessIndicatorConfig) -> BusinessWebSearchWhen:
    if indicator.evaluator == "llm_web":
        return "always"
    if indicator.evaluator == "rule":
        return "rule_not_matched"
    return "insufficient"


def _default_effect(indicator: BusinessIndicatorConfig) -> BusinessWebSearchEffect:
    if indicator.evaluator == "rule":
        return "evidence_only"
    return "llm_evidence"


def _is_insufficient(local_evidence: list[dict[str, object]]) -> bool:
    if not local_evidence:
        return True
    return not any(bool(item.get("matched")) for item in local_evidence)
