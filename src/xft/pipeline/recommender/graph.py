"""LangGraph assembly for the local DuckDB-backed recommender."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import structlog
from langgraph.graph import END, START, StateGraph

from xft.core.scenario import DEFAULT_PROMPTS, load_scenario
from xft.evidence.policy import load_evidence_policy
from xft.pipeline.recommender.business_config_loader import load_business_recommendation_config
from xft.pipeline.recommender.config_loader import load_dimensions_config
from xft.pipeline.recommender.models import RecommendationRunResult
from xft.pipeline.recommender.nodes.business_recommend_node import business_recommend_node
from xft.pipeline.recommender.nodes.data_gather_node import data_gather_node
from xft.pipeline.recommender.nodes.dimension_analyze_node import dimension_analyze_node
from xft.pipeline.recommender.nodes.save_node import save_node
from xft.pipeline.recommender.nodes.web_evidence_node import web_evidence_node
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.runtime.config_manifest import ConfigManifest, file_ref, model_hash, write_config_manifest
from xft.web import run_web_enrichment
from xft.web.models import WebRunMetrics

log = structlog.get_logger(__name__)

_cache: dict[str, Any] = {}
DEFAULT_SCENARIO = "config/scenarios/sales_recommendation"


def _get_graph() -> Any:
    if "graph" not in _cache:
        graph = StateGraph(RecommenderState)
        graph.add_node("data_gather", data_gather_node)
        graph.add_node("dimension_analyze", dimension_analyze_node)
        graph.add_node("web_evidence", web_evidence_node)
        graph.add_node("business_recommend", business_recommend_node)
        graph.add_node("save", save_node)
        graph.add_edge(START, "data_gather")
        graph.add_edge("data_gather", "dimension_analyze")
        graph.add_edge("dimension_analyze", "web_evidence")
        graph.add_edge("web_evidence", "business_recommend")
        graph.add_edge("business_recommend", "save")
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
    scenario_path: str | None = None,
    dimensions_config_path: str | None = None,
    output_dir: str | None = None,
    run_id: str | None = None,
    use_llm: bool = True,
    use_web_evidence: bool = False,
    with_web: bool = False,
    refresh_web: bool = False,
    web_config_path: str | None = None,
    web_extract_llm_config_path: str | None = None,
    evidence_policy_path: str | None = None,
    web_providers: list[str] | None = None,
    web_fetch_pages: bool | None = None,
    web_force_dimensions: bool = False,
    web_use_llm_extraction: bool = True,
    llm_debug: bool = False,
    llm_concurrency: int = 4,
) -> RecommendationRunResult:
    display.header(company_name)
    scenario = load_scenario(scenario_path or DEFAULT_SCENARIO)
    if scenario is None:
        msg = f"scenario not found: {scenario_path or DEFAULT_SCENARIO}"
        raise FileNotFoundError(msg)
    dimensions_path = dimensions_config_path or scenario.dimensions_path
    web_search_path = web_config_path or scenario.web_search_path
    web_extract_path = web_extract_llm_config_path or scenario.web_extract_llm_path
    evidence_path = evidence_policy_path or scenario.evidence_policy_path
    business_path = scenario.business_modules_path
    prompt_paths = scenario.prompt_paths or DEFAULT_PROMPTS.copy()
    dimensions_config = load_dimensions_config(dimensions_path)
    evidence_policy = load_evidence_policy(evidence_path)
    business_config = load_business_recommendation_config(business_path)
    root = output_dir or scenario.output_dir or "recommendation_runs"
    rid = run_id or make_recommendation_run_id(company_name)
    out_dir = Path(root) / rid
    scenario_out = out_dir / "scenario_resolved.json"
    scenario_out.parent.mkdir(parents=True, exist_ok=True)
    scenario_resolved_path = scenario.write_resolved_config(scenario_out)
    _write_config_manifest(
        out_dir=out_dir,
        company_name=company_name,
        run_id=rid,
        warehouse_db=warehouse_db,
        scenario=scenario,
        scenario_resolved_path=scenario_resolved_path,
        dimensions_path=dimensions_path,
        web_search_path=web_search_path,
        web_extract_path=web_extract_path,
        evidence_path=evidence_path,
        business_path=business_path,
        prompt_paths=prompt_paths,
        dimensions_config=dimensions_config,
        evidence_policy=evidence_policy,
        business_config=business_config,
        use_llm=use_llm,
        use_web_evidence=use_web_evidence,
        with_web=with_web,
        refresh_web=refresh_web,
        web_force_dimensions=web_force_dimensions,
        web_use_llm_extraction=web_use_llm_extraction,
        llm_debug=llm_debug,
        llm_concurrency=llm_concurrency,
    )
    has_cached = _has_web_evidence(warehouse_db, company_name)
    if with_web and (refresh_web or not has_cached):
        reason = "refresh" if refresh_web else "no_cached_web_evidence"
        log.info(
            "run_with_web_start_enrichment",
            company_name=company_name,
            reason=reason,
            has_cached_web_evidence=has_cached,
        )
        display.info(f"Web 证据: 缓存{'' if has_cached else '不'}存在, 开始搜索")
        web_result = await run_web_enrichment(
            company_name=company_name,
            warehouse_db=warehouse_db,
            scenario_path=scenario_path or DEFAULT_SCENARIO,
            web_config_path=web_search_path,
            web_extract_llm_config_path=web_extract_path,
            dimensions_config_path=dimensions_path,
            evidence_policy_path=evidence_path,
            providers=web_providers,
            refresh=refresh_web,
            force_dimensions=web_force_dimensions,
            load_to_duckdb=True,
            use_llm_extraction=web_use_llm_extraction,
            fetch_pages=web_fetch_pages,
        )
        _write_web_metrics(root, rid, web_result.metrics)
        web_trace_path = str(Path(web_result.output_dir) / "decision_trace_web.json") if web_result.output_dir else ""
        use_web_evidence = True
    elif with_web:
        log.info(
            "run_with_web_reuse_cache",
            company_name=company_name,
            reason="cached_web_evidence_exists",
        )
        display.info("Web 证据: 复用缓存")
        use_web_evidence = True
    elif use_web_evidence:
        display.info("Web 证据: 复用已有 DuckDB 数据")
    else:
        log.info(
            "run_no_web_evidence",
            company_name=company_name,
            use_llm=use_llm,
        )
        display.skip("Web 证据: 未启用 (使用 --with-web 或 --with-web-evidence 开启)")
    initial: RecommenderState = {
        "company_name": company_name,
        "warehouse_db": warehouse_db,
        "output_root": root,
        "run_id": rid,
        "use_llm": use_llm,
        "llm_debug": llm_debug,
        "llm_concurrency": llm_concurrency,
        "llm_call_events": [],
        "use_web_evidence": use_web_evidence,
        "web_trace_path": locals().get("web_trace_path", ""),
        "scenario_id": scenario.config.id,
        "scenario_name": scenario.config.name,
        "prompt_paths": prompt_paths,
        "dimensions_config": dimensions_config,
        "evidence_policy": evidence_policy,
        "business_config": business_config,
        "profile": {},
        "dimension_analysis": [],
        "business_recommendation": None,
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


def _write_web_metrics(output_root: str | None, run_id: str, metrics: WebRunMetrics | None) -> None:
    if metrics is None or not output_root:
        return
    out = Path(output_root) / run_id / "web_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(metrics.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_config_manifest(  # noqa: PLR0913
    *,
    out_dir: Path,
    company_name: str,
    run_id: str,
    warehouse_db: str,
    scenario: Any,
    scenario_resolved_path: Path | None,
    dimensions_path: str,
    web_search_path: str,
    web_extract_path: str,
    evidence_path: str,
    business_path: str | None,
    prompt_paths: dict[str, str],
    dimensions_config: Any,
    evidence_policy: Any,
    business_config: Any,
    use_llm: bool,
    use_web_evidence: bool,
    with_web: bool,
    refresh_web: bool,
    web_force_dimensions: bool,
    web_use_llm_extraction: bool,
    llm_debug: bool,
    llm_concurrency: int,
) -> Path:
    files = {
        "dimensions": file_ref(dimensions_path),
        "web_search": file_ref(web_search_path),
        "web_extract_llm": file_ref(web_extract_path),
        "evidence_policy": file_ref(evidence_path),
    }
    if business_path:
        files["business_modules"] = file_ref(business_path)
    files["scenario"] = file_ref(Path(scenario.root) / "scenario.yaml")
    for key, path in sorted(prompt_paths.items()):
        files[f"prompt:{key}"] = file_ref(path)
    manifest = ConfigManifest(
        pipeline="recommender",
        run_id=run_id,
        target=company_name,
        scenario_id=scenario.config.id,
        scenario_name=scenario.config.name,
        scenario_root=str(scenario.root),
        scenario_resolved_path=str(scenario_resolved_path) if scenario_resolved_path else None,
        warehouse_db=warehouse_db,
        mode={
            "use_llm": use_llm,
            "use_web_evidence": use_web_evidence,
            "with_web": with_web,
            "refresh_web": refresh_web,
            "web_force_dimensions": web_force_dimensions,
            "web_use_llm_extraction": web_use_llm_extraction,
            "llm_debug": llm_debug,
            "llm_concurrency": llm_concurrency,
        },
        files=files,
        effective_hashes={
            "dimensions": model_hash(dimensions_config),
            "evidence_policy": model_hash(evidence_policy),
            "business_modules": model_hash(business_config) if business_config is not None else "",
        },
    )
    return write_config_manifest(out_dir / "config_manifest.json", manifest)


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
