"""Generate configured local dimension analysis."""

from __future__ import annotations

from diligence.recommender.dimension_analyzer import analyze_dimensions
from diligence.recommender.state import RecommenderState


async def dimension_analyze_node(state: RecommenderState) -> dict[str, object]:
    profile = state.get("profile", {})
    if not profile:
        return {"dimension_analysis": []}
    analyses = analyze_dimensions(profile=profile, dimensions=state["dimensions_config"].dimensions)
    needs_web = state.get("needs_web_enrichment", False) or any(item.status == "insufficient" for item in analyses)
    return {"dimension_analysis": analyses, "needs_web_enrichment": needs_web}

