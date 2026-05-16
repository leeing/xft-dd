"""Load company profile from DuckDB."""

from __future__ import annotations

from diligence.recommender.profile_repository import CompanyProfileRepository
from diligence.recommender.state import RecommenderState

MIN_PROFILE_COMPLETENESS = 0.6


async def data_gather_node(state: RecommenderState) -> dict[str, object]:
    repo = CompanyProfileRepository(state["warehouse_db"])
    profile = repo.get_by_company_name(state["company_name"])
    if profile is None:
        return {
            "errors": [f"company profile not found: {state['company_name']}"],
            "needs_web_enrichment": True,
        }
    completeness = float(profile.get("profile_completeness") or 0)
    return {
        "profile": profile,
        "needs_web_enrichment": completeness < MIN_PROFILE_COMPLETENESS,
    }

