"""Generate business-facing recommendation output."""

from __future__ import annotations

from xft.pipeline.recommender.evaluator import evaluate_recommendation
from xft.pipeline.recommender.evidence_utils import merge_indicator_evidence
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display


async def recommend_node(state: RecommenderState) -> dict[str, object]:
    """Evaluate optional business modules into the final business JSON shape."""
    config = state.get("modules_config")
    if config is None:
        display.skip("业务结果: 未配置 modules.yaml")
        return {"recommendation": None}
    display.phase(3, 4, "业务推荐评估")
    events: list[dict[str, object]] = []
    evidence = merge_indicator_evidence(
        state.get("evidence", {}),
        state.get("web_evidence", {}),
    )
    result = await evaluate_recommendation(
        config=config,
        company_name=state["company_name"],
        profile=state.get("profile", {}),
        evidence=evidence,
        web_trace=state.get("web_trace", []),
        use_llm=state.get("use_llm", True),
        llm_debug=state.get("llm_debug", False),
        llm_concurrency=state.get("llm_concurrency", 4),
        llm_events=events,
    )
    if result and result.selected_module:
        selected = result.selected_module
        display.ok(
            f"业务结果 → {selected.module_name} / {selected.acceptance_result} "
            f"({selected.attributes_number}个标签, {selected.indicators_number}个指标)"
        )
    return {"recommendation": result, "llm_call_events": events}
