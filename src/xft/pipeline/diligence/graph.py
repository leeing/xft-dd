"""LangGraph pipeline assembly and public run_company_graph() entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from xft.pipeline.diligence.config import AppConfig
from xft.pipeline.diligence.models import CompanyRunResult, CostRecord, RunMeta
from xft.pipeline.diligence.nodes.collect_node import collect_node
from xft.pipeline.diligence.nodes.init_node import init_node
from xft.pipeline.diligence.nodes.merge_node import merge_node
from xft.pipeline.diligence.nodes.route_node import route_node
from xft.pipeline.diligence.nodes.save_node import save_node
from xft.pipeline.diligence.nodes.search_node import search_node
from xft.pipeline.diligence.nodes.summarize_node import summarize_node
from xft.pipeline.diligence.state import DiligenceState, merge_cost

log = structlog.get_logger(__name__)

_cache: dict[str, object] = {}


async def _search_summarize_node(state: DiligenceState) -> dict[str, object]:
    """Combined per-dimension branch: search then summarize, preserving current_dimension."""
    search_out = await search_node(state)
    # Merge search results into a local state copy so summarize can read them
    merged_state: DiligenceState = {
        **state,
        "search_results_by_dimension": {
            **state.get("search_results_by_dimension", {}),
            **search_out.get("search_results_by_dimension", {}),  # type: ignore[dict-item]
        },
    }
    summarize_out = await summarize_node(merged_state)
    # Manually merge cost so neither branch overwrites the other.
    # (Python dict merge with ** would silently drop search_out["cost"].)
    combined_cost = merge_cost(
        search_out.get("cost", CostRecord()),  # type: ignore[arg-type]
        summarize_out.get("cost", CostRecord()),  # type: ignore[arg-type]
    )
    return {
        **search_out,
        **summarize_out,
        "cost": combined_cost,
    }


def _get_compiled() -> Any:
    if "graph" not in _cache:
        g = StateGraph(DiligenceState)
        g.add_node("init_node", init_node)
        g.add_node("search_summarize_node", _search_summarize_node)
        g.add_node("collect_node", collect_node)
        g.add_node("merge_node", merge_node)
        g.add_node("save_node", save_node)

        g.add_edge(START, "init_node")
        g.add_conditional_edges("init_node", route_node, ["search_summarize_node"])
        g.add_edge("search_summarize_node", "collect_node")
        g.add_edge("collect_node", "merge_node")
        g.add_edge("merge_node", "save_node")
        g.add_edge("save_node", END)
        _cache["graph"] = g.compile()
    return _cache["graph"]


async def run_company_graph(  # noqa: PLR0913
    target: str,
    config: AppConfig,
    output_dir: str,
    run_id: str = "",
    config_path: str = "",
    all_dimension_names: dict[str, str] | None = None,
) -> CompanyRunResult:
    """Execute the full single-company due xft pipeline.

    Args:
        target: Company name.
        config: Validated AppConfig.
        output_dir: Directory where all artifacts will be written.

    Returns:
        CompanyRunResult summarising the outcome.
    """
    graph = _get_compiled()
    initial: DiligenceState = {
        "target": target,
        "config": config,
        "run_id": run_id,
        "started_at": None,
        "active_dimensions": [],
        "output_dir": output_dir,
        "current_dimension": None,
        "search_results_by_dimension": {},
        "summaries_by_dimension": {},
        "errors": [],
        "cost": CostRecord(),
        "report": "",
        "report_path": "",
        "artifacts_dir": "",
        "config_path": config_path,
        "all_dimension_names": all_dimension_names or {},
    }
    langgraph_cfg = {"max_concurrency": config.dimension_concurrency}

    try:
        final = await graph.ainvoke(initial, config=langgraph_cfg)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        log.exception("pipeline_failed", target=target, error=str(exc))
        return CompanyRunResult(index=0, target=target, status="failed", error=str(exc))

    meta_path = Path(output_dir) / "run_meta.json"
    if meta_path.exists():
        meta = RunMeta.model_validate_json(meta_path.read_text())
        return CompanyRunResult(
            index=0,
            target=target,
            run_id=meta.run_id,
            status=meta.status,
            report_path=final.get("report_path"),
            artifacts_dir=final.get("artifacts_dir"),
            required_failed=meta.required_failed,
            failed_dimensions=meta.failed_dimensions,
        )

    return CompanyRunResult(index=0, target=target, status="failed", error="run_meta.json not found")
