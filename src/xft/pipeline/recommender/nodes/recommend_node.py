"""Generate business-facing recommendation output."""

from __future__ import annotations

from pathlib import Path

from xft.pipeline.recommender.evaluator import evaluate_recommendation
from xft.pipeline.recommender.state import RecommenderState
from xft.pipeline.recommender.web_resolver import WebResolver
from xft.progress import display


async def recommend_node(state: RecommenderState) -> dict[str, object]:
    """Evaluate optional business modules into the final business JSON shape."""
    config = state.get("modules_config")
    if config is None:
        display.skip("业务结果: 未配置 modules.yaml")
        return {"recommendation": None}
    display.phase(2, 3, "业务推荐评估")
    events: list[dict[str, object]] = []
    web_resolver = None
    if state.get("with_web", False):
        display.info("业务 Web 证据: 按指标缺口延迟搜索")
        web_resolver = WebResolver(
            company_name=state["company_name"],
            profile=state.get("profile", {}),
            web_config_path=state["web_config_path"],
            output_dir=Path(state["output_root"]) / state["run_id"],
            providers=state.get("web_providers"),
            refresh=state.get("refresh_web", False),
        )
    result = await evaluate_recommendation(
        config=config,
        company_name=state["company_name"],
        profile=state.get("profile", {}),
        evidence=state.get("evidence", {}),
        web_trace=state.get("web_trace", []),
        use_llm=state.get("use_llm", True),
        llm_debug=state.get("llm_debug", False),
        llm_concurrency=state.get("llm_concurrency", 4),
        llm_events=events,
        web_resolver=web_resolver,
    )
    web_result = web_resolver.write_outputs() if web_resolver else None
    if web_result and web_result.queries:
        display.ok(f"业务 Web → {web_result.queries} 次按需查询, {web_result.results} 条结果")
    if result and result.selected_module:
        selected = result.selected_module
        display.ok(
            f"业务结果 → {selected.module_name} / {selected.acceptance_result} "
            f"({selected.attributes_number}个标签, {selected.indicators_number}个指标)"
        )
    payload: dict[str, object] = {"recommendation": result, "llm_call_events": events}
    if web_result:
        payload.update({"web_evidence": web_result.evidence, "web_trace": web_result.trace})
    return payload
