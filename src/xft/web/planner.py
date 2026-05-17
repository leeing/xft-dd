"""Plan which recommender dimensions should use Web search."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from xft.core.models import DimensionAnalysis
from xft.progress import display

SUPPORTED_FACTS_TO_SKIP_WEB = 3

log = structlog.get_logger(__name__)


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
            log.debug("dimension_no_queries", dimension_id=analysis.dimension_id)
            continue
        explicitly_selected = analysis.dimension_id in selected_ids
        if selected_ids and not explicitly_selected:
            log.debug("dimension_not_selected", dimension_id=analysis.dimension_id)
            continue
        if _should_skip(analysis, explicitly_selected=explicitly_selected, force_dimensions=force_dimensions):
            log.info(
                "web_search_skipped",
                dimension_id=analysis.dimension_id,
                status=analysis.status,
                facts_count=len(analysis.facts),
                queries=queries,
            )
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
        log.info(
            "web_search_planned",
            dimension_id=analysis.dimension_id,
            reason=reason,
            status=analysis.status,
            queries=queries,
        )
        planned.append(PlannedDimension(analysis=analysis, queries=queries, reason=reason))
    log.info(
        "web_search_plan_summary",
        total_dimensions=len(analyses),
        planned=len(planned),
        skipped=len(skipped),
        force_dimensions=force_dimensions,
    )
    display.info(f"维度规划: {len(planned)}/{len(analyses)} 需搜索, {len(skipped)}/{len(analyses)} 跳过")
    for s in skipped:
        display.branch(f"⏭ {s.analysis.dimension_id}: 跳过 ({s.reason}, {len(s.profile_facts)}条本地事实)")
    for p in planned:
        display.branch(f"🔍 {p.analysis.dimension_id}: 加入搜索 ({p.reason}, {len(p.queries)}条查询)")
    return WebSearchPlan(planned=planned, skipped=skipped)


def _should_skip(analysis: DimensionAnalysis, *, explicitly_selected: bool, force_dimensions: bool) -> bool:
    if explicitly_selected or force_dimensions:
        return False
    return analysis.status == "supported" and len(analysis.facts) >= SUPPORTED_FACTS_TO_SKIP_WEB
