"""Generate configured local dimension analysis."""

from __future__ import annotations

from xft.pipeline.recommender.dimension_analyzer import analyze_dimensions
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display


async def dimension_analyze_node(state: RecommenderState) -> dict[str, object]:
    display.phase(2, 5, "维度分析")
    profile = state.get("profile", {})
    if not profile:
        display.fail("无企业画像数据")
        return {"dimension_analysis": []}
    analyses = analyze_dimensions(profile=profile, dimensions=state["dimensions_config"].dimensions)
    needs_web = state.get("needs_web_enrichment", False) or any(item.status == "insufficient" for item in analyses)
    supported = sum(1 for a in analyses if a.status == "supported")
    partial = sum(1 for a in analyses if a.status == "partial")
    insufficient = sum(1 for a in analyses if a.status == "insufficient")
    local_facts = sum(len(a.facts) for a in analyses)
    display.ok(
        f"{len(analyses)} 个维度, {local_facts} 条事实 "
        f"(supported:{supported} partial:{partial} insufficient:{insufficient})"
    )
    for a in analyses:
        if a.status != "supported":
            display.branch(f"{a.dimension_id}: {a.status} ({len(a.facts)}条事实, 需Web补充)")
    return {"dimension_analysis": analyses, "needs_web_enrichment": needs_web}

