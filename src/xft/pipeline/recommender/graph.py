"""LangGraph assembly for the local DuckDB-backed recommender."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from langgraph.graph import END, START, StateGraph

from xft.constants import DEFAULT_SCENARIO, DEFAULT_WAREHOUSE
from xft.core.scenario import load_scenario
from xft.pipeline.recommender.config_loader import load_recommendation_config
from xft.pipeline.recommender.models import RecommendationConfig, RecommendationRunResult
from xft.pipeline.recommender.nodes.data_gather_node import data_gather_node
from xft.pipeline.recommender.nodes.recommend_node import recommend_node
from xft.pipeline.recommender.nodes.save_node import save_node
from xft.pipeline.recommender.run_log import write_failure_log
from xft.pipeline.recommender.state import RecommenderState
from xft.progress import display
from xft.runtime.config_manifest import ConfigManifest, file_ref, model_hash, write_config_manifest

log = structlog.get_logger(__name__)

_cache: dict[str, Any] = {}


def _get_graph() -> Any:
    if "graph" not in _cache:
        graph = StateGraph(RecommenderState)
        graph.add_node("data_gather", data_gather_node)
        graph.add_node("recommend", recommend_node)
        graph.add_node("save", save_node)
        graph.add_edge(START, "data_gather")
        graph.add_edge("data_gather", "recommend")
        graph.add_edge("recommend", "save")
        graph.add_edge("save", END)
        _cache["graph"] = graph.compile()
    return _cache["graph"]


TZ = ZoneInfo("Asia/Shanghai")


def make_recommendation_run_id(company_name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in company_name)[:40].strip("_") or "company"
    return f"{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}_{safe}"


async def run_recommendation(  # noqa: PLR0913
    *,
    company_name: str,
    warehouse_db: str = DEFAULT_WAREHOUSE,
    scenario_path: str | None = None,
    output_dir: str | None = None,
    run_id: str | None = None,
    use_llm: bool = True,
    web_config_path: str | None = None,
    with_web: bool = False,
    refresh_web: bool = False,
    web_providers: list[str] | None = None,
    module_ids: list[str] | None = None,
    indicator_ids: list[str] | None = None,
    llm_debug: bool = False,
    llm_concurrency: int = 4,
) -> RecommendationRunResult:
    display.header(company_name)
    scenario = load_scenario(scenario_path or DEFAULT_SCENARIO)
    if scenario is None:
        msg = f"scenario not found: {scenario_path or DEFAULT_SCENARIO}"
        raise FileNotFoundError(msg)
    web_search_path = web_config_path or scenario.web_search_path
    modules_path = scenario.modules_path
    prompt_paths = scenario.prompt_paths
    modules_config = load_recommendation_config(modules_path)
    root = output_dir or scenario.output_dir or "outputs/recommender/xft"
    rid = run_id or make_recommendation_run_id(company_name)
    try:
        modules_config = _filter_modules_config(modules_config, module_ids, indicator_ids)
    except ValueError as exc:
        log_path = write_failure_log(
            out_dir=Path(root) / rid,
            company_name=company_name,
            run_id=rid,
            error=str(exc),
            context={
                "scenario": scenario.config.id,
                "requested_module_ids": module_ids or [],
                "requested_indicator_ids": indicator_ids or [],
            },
        )
        return RecommendationRunResult(
            company_name=company_name,
            status="failed",
            run_id=rid,
            output_dir=str(Path(root) / rid),
            log_path=str(log_path),
            error=str(exc),
        )
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
        web_search_path=web_search_path,
        modules_path=modules_path,
        prompt_paths=prompt_paths,
        modules_config=modules_config,
        use_llm=use_llm,
        with_web=with_web,
        refresh_web=refresh_web,
        module_ids=module_ids,
        indicator_ids=indicator_ids,
        llm_debug=llm_debug,
        llm_concurrency=llm_concurrency,
    )
    if not with_web:
        log.info("web_disabled", company_name=company_name, use_llm=use_llm)
        display.skip("业务 Web 证据: 未启用 (使用 --with-web 开启)")
    initial: RecommenderState = {
        "company_name": company_name,
        "warehouse_db": warehouse_db,
        "output_root": root,
        "run_id": rid,
        "use_llm": use_llm,
        "llm_debug": llm_debug,
        "llm_concurrency": llm_concurrency,
        "llm_call_events": [],
        "with_web": with_web,
        "refresh_web": refresh_web,
        "web_config_path": web_search_path,
        "web_providers": web_providers,
        "scenario_id": scenario.config.id,
        "scenario_name": scenario.config.name,
        "modules_config": modules_config,
        "profile": {},
        "evidence": {},
        "web_evidence": {},
        "web_trace": [],
        "recommendation": None,
        "needs_web_enrichment": False,
        "errors": [],
        "output_dir": "",
        "report_path": "",
        "result_path": "",
        "log_path": "",
    }
    try:
        final = await _get_graph().ainvoke(initial)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        log_path = write_failure_log(
            out_dir=Path(root) / rid,
            company_name=company_name,
            run_id=rid,
            error=str(exc),
            context={
                "scenario": scenario.config.id,
                "warehouse_db": warehouse_db,
                "with_web": with_web,
                "use_llm": use_llm,
            },
        )
        return RecommendationRunResult(
            company_name=company_name,
            status="failed",
            run_id=rid,
            output_dir=str(Path(root) / rid),
            log_path=str(log_path),
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
        log_path=final.get("log_path"),
        error="; ".join(final.get("errors", [])) or None,
    )


def _write_config_manifest(  # noqa: PLR0913
    *,
    out_dir: Path,
    company_name: str,
    run_id: str,
    warehouse_db: str,
    scenario: Any,
    scenario_resolved_path: Path | None,
    web_search_path: str,
    modules_path: str | None,
    prompt_paths: dict[str, str],
    modules_config: Any,
    use_llm: bool,
    with_web: bool,
    refresh_web: bool,
    module_ids: list[str] | None,
    indicator_ids: list[str] | None,
    llm_debug: bool,
    llm_concurrency: int,
) -> Path:
    files = {
        "web_search": file_ref(web_search_path),
    }
    if modules_path:
        files["modules"] = file_ref(modules_path)
        files.update(_module_files(modules_path, modules_config))
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
            "with_web": with_web,
            "refresh_web": refresh_web,
            "module_ids": module_ids or [],
            "indicator_ids": indicator_ids or [],
            "llm_debug": llm_debug,
            "llm_concurrency": llm_concurrency,
        },
        files=files,
        effective_hashes={
            "modules": model_hash(modules_config) if modules_config is not None else "",
        },
    )
    return write_config_manifest(out_dir / "config_manifest.json", manifest)


def _filter_modules_config(
    config: RecommendationConfig | None,
    module_ids: list[str] | None,
    indicator_ids: list[str] | None,
) -> RecommendationConfig | None:
    if config is None:
        return config
    requested = [module_id.strip() for module_id in module_ids or [] if module_id.strip()]
    requested_indicators = [indicator_id.strip() for indicator_id in indicator_ids or [] if indicator_id.strip()]
    if not requested and not requested_indicators:
        return config
    available = {module.module_id: module for module in config.modules}
    missing = [module_id for module_id in requested if module_id not in available]
    if missing:
        msg = f"unknown module_id: {', '.join(missing)}; available module_ids: {', '.join(sorted(available))}"
        raise ValueError(msg)
    selected = [available[module_id] for module_id in requested] if requested else list(config.modules)
    if requested_indicators:
        selected = _filter_indicators(selected, requested_indicators)
    return config.model_copy(update={"modules": selected})


def _filter_indicators(modules: list[Any], indicator_ids: list[str]) -> list[Any]:
    available = {
        indicator.indicator_id for module in modules for label in module.labels for indicator in label.indicators
    }
    missing = [indicator_id for indicator_id in indicator_ids if indicator_id not in available]
    if missing:
        msg = f"unknown indicator_id: {', '.join(missing)}; available indicator_ids: {', '.join(sorted(available))}"
        raise ValueError(msg)
    return [
        module.model_copy(
            update={
                "labels": [
                    label.model_copy(
                        update={
                            "indicators": [
                                indicator for indicator in label.indicators if indicator.indicator_id in indicator_ids
                            ]
                        }
                    )
                    for label in module.labels
                    if any(indicator.indicator_id in indicator_ids for indicator in label.indicators)
                ]
            }
        )
        for module in modules
        if any(indicator.indicator_id in indicator_ids for label in module.labels for indicator in label.indicators)
    ]


def _module_files(modules_path: str, modules_config: Any) -> dict[str, Any]:
    modules_dir = getattr(modules_config, "modules_dir", None)
    if not isinstance(modules_dir, str) or not modules_dir.strip():
        return {}
    base = Path(modules_path).parent
    module_dir = Path(modules_dir)
    if not module_dir.is_absolute():
        module_dir = base / module_dir
    if not module_dir.is_dir():
        return {}
    return {f"module:{path.stem}": file_ref(path) for path in sorted(module_dir.glob("*.yaml"))}
