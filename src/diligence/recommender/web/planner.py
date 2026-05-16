"""Plan which recommender dimensions should use Web search."""

from __future__ import annotations

from dataclasses import dataclass

from diligence.recommender.models import DimensionAnalysis

SUPPORTED_FACTS_TO_SKIP_WEB = 3


@dataclass(frozen=True)
class PlannedDimension:
    analysis: DimensionAnalysis
    queries: list[str]
    reason: str


@dataclass(frozen=True)
class SkippedDimension:
    analysis: DimensionAnalysis
    queries: list[str]
    reason: str
    profile_facts: list[str]


@dataclass(frozen=True)
class WebSearchPlan:
    planned: list[PlannedDimension]
    skipped: list[SkippedDimension]


def plan_web_search(
    analyses: list[DimensionAnalysis],
    *,
    only_dimensions: list[str] | None = None,
    force_dimensions: bool = False,
    refresh: bool = False,
    max_queries_per_dimension: int = 3,
) -> WebSearchPlan:
    """Plan Web queries, skipping locally supported dimensions by default."""
    selected_ids = set(only_dimensions or [])
    planned: list[PlannedDimension] = []
    skipped: list[SkippedDimension] = []
    for analysis in analyses:
        queries = analysis.web_search_queries[:max_queries_per_dimension]
        if not queries:
            continue
        explicitly_selected = analysis.dimension_id in selected_ids
        if selected_ids and not explicitly_selected:
            continue
        if _should_skip(analysis, explicitly_selected=explicitly_selected, force_dimensions=force_dimensions):
            skipped.append(
                SkippedDimension(
                    analysis=analysis,
                    queries=queries,
                    reason="local_dimension_supported",
                    profile_facts=[fact.claim for fact in analysis.facts[:5]],
                )
            )
            continue
        reason = "refresh" if refresh else "forced" if force_dimensions or explicitly_selected else "local_gap"
        planned.append(PlannedDimension(analysis=analysis, queries=queries, reason=reason))
    return WebSearchPlan(planned=planned, skipped=skipped)


def _should_skip(analysis: DimensionAnalysis, *, explicitly_selected: bool, force_dimensions: bool) -> bool:
    if explicitly_selected or force_dimensions:
        return False
    return analysis.status == "supported" and len(analysis.facts) >= SUPPORTED_FACTS_TO_SKIP_WEB
