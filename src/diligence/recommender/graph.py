"""LangGraph assembly for the local DuckDB-backed recommender."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from langgraph.graph import END, START, StateGraph

from diligence.recommender.config_loader import load_dimensions_config, load_products_config
from diligence.recommender.models import RecommendationRunResult
from diligence.recommender.nodes.data_gather_node import data_gather_node
from diligence.recommender.nodes.dimension_analyze_node import dimension_analyze_node
from diligence.recommender.nodes.llm_match_node import llm_match_node
from diligence.recommender.nodes.llm_recommend_node import llm_recommend_node
from diligence.recommender.nodes.save_node import save_node
from diligence.recommender.nodes.web_evidence_node import web_evidence_node
from diligence.recommender.state import RecommenderState
from diligence.recommender.web import run_web_enrichment

_cache: dict[str, Any] = {}


def _get_graph() -> Any:
    if "graph" not in _cache:
        graph = StateGraph(RecommenderState)
        graph.add_node("data_gather", data_gather_node)
        graph.add_node("dimension_analyze", dimension_analyze_node)
        graph.add_node("web_evidence", web_evidence_node)
        graph.add_node("llm_match", llm_match_node)
        graph.add_node("llm_recommend", llm_recommend_node)
        graph.add_node("save", save_node)
        graph.add_edge(START, "data_gather")
        graph.add_edge("data_gather", "dimension_analyze")
        graph.add_edge("dimension_analyze", "web_evidence")
        graph.add_edge("web_evidence", "llm_match")
        graph.add_edge("llm_match", "llm_recommend")
        graph.add_edge("llm_recommend", "save")
        graph.add_edge("save", END)
        _cache["graph"] = graph.compile()
    return _cache["graph"]


def make_recommendation_run_id(company_name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in company_name)[:40].strip("_") or "company"
    return f"rec_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{safe}"


async def run_recommendation(  # noqa: PLR0913
    *,
    company_name: str,
    warehouse_db: str = "cache/company_warehouse.duckdb",
    products_config_path: str = "config/recommender/products.yaml",
    dimensions_config_path: str = "config/recommender/analysis_dimensions.yaml",
    output_dir: str | None = None,
    run_id: str | None = None,
    use_llm: bool = True,
    use_web_evidence: bool = False,
    with_web: bool = False,
    refresh_web: bool = False,
    web_config_path: str = "config/recommender/web_search.yaml",
    web_extract_llm_config_path: str = "config/recommender/web_extract_llm.yaml",
    web_providers: list[str] | None = None,
    web_fetch_pages: bool | None = None,
    web_use_llm_extraction: bool = True,
) -> RecommendationRunResult:
    products_config = load_products_config(products_config_path)
    dimensions_config = load_dimensions_config(dimensions_config_path)
    root = output_dir or products_config.output_dir
    rid = run_id or make_recommendation_run_id(company_name)
    if with_web and (refresh_web or not _has_web_evidence(warehouse_db, company_name)):
        await run_web_enrichment(
            company_name=company_name,
            warehouse_db=warehouse_db,
            web_config_path=web_config_path,
            web_extract_llm_config_path=web_extract_llm_config_path,
            dimensions_config_path=dimensions_config_path,
            providers=web_providers,
            refresh=refresh_web,
            load_to_duckdb=True,
            use_llm_extraction=web_use_llm_extraction,
            fetch_pages=web_fetch_pages,
        )
        use_web_evidence = True
    elif with_web:
        use_web_evidence = True
    initial: RecommenderState = {
        "company_name": company_name,
        "warehouse_db": warehouse_db,
        "output_root": root,
        "run_id": rid,
        "use_llm": use_llm,
        "use_web_evidence": use_web_evidence,
        "products_config": products_config,
        "dimensions_config": dimensions_config,
        "products": products_config.products,
        "profile": {},
        "dimension_analysis": [],
        "match_results": [],
        "recommendation": None,
        "needs_web_enrichment": False,
        "errors": [],
        "output_dir": "",
        "report_path": "",
        "result_path": "",
    }
    try:
        final = await _get_graph().ainvoke(initial)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        return RecommendationRunResult(
            company_name=company_name,
            status="failed",
            run_id=rid,
            output_dir=str(Path(root) / rid),
            error=str(exc),
        )
    status: str = "failed" if final.get("errors") else "partial" if final.get("needs_web_enrichment") else "success"
    typed_status: Any = status
    return RecommendationRunResult(
        company_name=company_name,
        status=typed_status,
        run_id=rid,
        output_dir=final.get("output_dir") or str(Path(root) / rid),
        report_path=final.get("report_path"),
        result_path=final.get("result_path"),
        error="; ".join(final.get("errors", [])) or None,
    )


def _has_web_evidence(warehouse_db: str, company_name: str) -> bool:
    try:
        conn = duckdb.connect(warehouse_db, read_only=True)
        try:
            row = conn.execute(
                """
                SELECT count(*)
                FROM web_evidence
                WHERE company_name = ?
                """,
                [company_name],
            ).fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except duckdb.Error:
        return False
