"""Generate business-facing recommendation output."""

from __future__ import annotations

from xft.pipeline.recommender.business_evaluator import evaluate_business_recommendation
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display


async def business_recommend_node(state: RecommenderState) -> dict[str, object]:
    """Evaluate optional business modules into the final business JSON shape."""
    config = state.get("business_config")
    if config is None:
        display.skip("业务结果: 未配置 business_modules.yaml")
        return {"business_recommendation": None}
    display.phase(3, 4, "业务推荐评估")
    events: list[dict[str, object]] = []
    business_evidence = _merge_indicator_evidence(
        state.get("business_evidence", {}),
        state.get("business_web_evidence", {}),
    )
    result = await evaluate_business_recommendation(
        config=config,
        company_name=state["company_name"],
        profile=state.get("profile", {}),
        business_evidence=business_evidence,
        business_web_trace=state.get("business_web_trace", []),
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
    return {"business_recommendation": result, "llm_call_events": events}


def _merge_indicator_evidence(
    local: dict[str, list[dict[str, object]]],
    web: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    merged = {key: list(value) for key, value in local.items()}
    for key, items in web.items():
        merged.setdefault(key, []).extend(items)
    return merged
