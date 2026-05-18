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
    result = await evaluate_business_recommendation(
        config=config,
        company_name=state["company_name"],
        profile=state.get("profile", {}),
        dimension_analysis=state["dimension_analysis"],
        use_llm=state.get("use_llm", True),
    )
    if result and result.selected_module:
        selected = result.selected_module
        display.ok(
            f"业务结果 → {selected.module_name} / {selected.acceptance_result} "
            f"({selected.attributes_number}个标签, {selected.indicators_number}个指标)"
        )
    return {"business_recommendation": result}
