"""Load company profile from DuckDB."""

from __future__ import annotations

from xft.pipeline.recommender.evidence_loader import load_evidence
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.warehouse.profile_repository import CompanyProfileRepository

MIN_PROFILE_COMPLETENESS = 0.6


async def data_gather_node(state: RecommenderState) -> dict[str, object]:
    display.phase(1, 3, "加载企业画像")
    repo = CompanyProfileRepository(state["warehouse_db"])
    profile = repo.get_by_company_name(state["company_name"])
    if profile is None:
        display.fail(f"company_profile 表未找到: {state['company_name']}")
        return {
            "errors": [f"company profile not found: {state['company_name']}"],
            "needs_web_enrichment": True,
        }
    completeness = float(profile.get("profile_completeness") or 0)
    industry = profile.get("industry", "")
    display.ok(f"DuckDB → company_profile 表 → {state['company_name']} (行业: {industry}, 完整度: {completeness:.0%})")
    evidence = load_evidence(
        config=state.get("modules_config"),
        warehouse_db=state["warehouse_db"],
        profile=profile,
    )
    if evidence:
        evidence_count = sum(len(items) for items in evidence.values())
        display.ok(f"业务指标证据 → {len(evidence)} 个指标, {evidence_count} 条本地证据")
    return {
        "profile": profile,
        "evidence": evidence,
        "needs_web_enrichment": completeness < MIN_PROFILE_COMPLETENESS,
    }
