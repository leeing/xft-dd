"""Collect indicator-level Web evidence for business recommendation."""

from __future__ import annotations

from pathlib import Path

from xft.pipeline.recommender.business_web_evidence import run_business_web_evidence
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display


async def business_web_evidence_node(state: RecommenderState) -> dict[str, object]:
    """Run indicator-level Web queries declared by business Web policies."""
    if not state.get("with_business_web", False):
        display.skip("业务 Web 证据: 未启用")
        return {}
    display.phase(4, 6, "业务 Web 证据")
    out_dir = Path(state["output_root"]) / state["run_id"]
    result = await run_business_web_evidence(
        config=state.get("business_config"),
        company_name=state["company_name"],
        profile=state.get("profile", {}),
        web_config_path=state["business_web_config_path"],
        output_dir=out_dir,
        providers=state.get("business_web_providers"),
        refresh=state.get("refresh_business_web", False),
        business_evidence=state.get("business_evidence", {}),
    )
    if result.queries:
        display.ok(f"业务 Web → {result.queries} 次查询, {result.results} 条结果")
    else:
        display.skip("业务 Web 证据: 无可执行的指标级查询或无可用 provider")
    return {
        "business_web_evidence": result.evidence,
        "business_web_trace": result.trace,
    }
