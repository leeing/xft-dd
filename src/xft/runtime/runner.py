"""Dispatch common pipeline requests to scenario-specific runners."""

from __future__ import annotations

from pathlib import Path

from xft.runtime.models import PipelineRunRequest, PipelineRunResult


async def run_pipeline(request: PipelineRunRequest) -> PipelineRunResult:
    """Run a configured pipeline through the common runtime protocol."""
    if request.pipeline == "recommender":
        return await _run_recommender(request)
    if request.pipeline == "diligence":
        return await _run_diligence(request)
    msg = f"unsupported pipeline: {request.pipeline}"
    raise ValueError(msg)


async def _run_recommender(request: PipelineRunRequest) -> PipelineRunResult:
    from xft.pipeline.recommender import run_recommendation

    result = await run_recommendation(
        company_name=request.target,
        warehouse_db=request.warehouse_db,
        scenario_path=request.scenario_path,
        products_config_path=_str_option(request, "products_config_path"),
        dimensions_config_path=_str_option(request, "dimensions_config_path"),
        output_dir=request.output_dir,
        run_id=request.run_id,
        use_llm=request.use_llm,
        use_web_evidence=request.use_web_evidence or request.use_web,
        with_web=request.use_web,
        refresh_web=request.refresh_web,
        web_config_path=_str_option(request, "web_config_path"),
        web_extract_llm_config_path=_str_option(request, "web_extract_llm_config_path"),
        scoring_policy_path=_str_option(request, "scoring_policy_path"),
        evidence_policy_path=_str_option(request, "evidence_policy_path"),
        web_providers=_list_option(request, "web_providers"),
        web_fetch_pages=_bool_or_none_option(request, "web_fetch_pages"),
        web_force_dimensions=_bool_option(request, "web_force_dimensions", default=False),
        web_use_llm_extraction=_bool_option(request, "web_use_llm_extraction", default=True),
    )
    return PipelineRunResult(
        pipeline="recommender",
        target=result.company_name,
        status=result.status,
        run_id=result.run_id,
        output_dir=result.output_dir,
        result_path=result.result_path,
        report_path=result.report_path,
        error=result.error,
        raw=result.model_dump(mode="json"),
    )


async def _run_diligence(request: PipelineRunRequest) -> PipelineRunResult:
    from xft.pipeline.diligence.config import load_config, validate_dimension_ids
    from xft.pipeline.diligence.graph import run_company_graph
    from xft.pipeline.diligence.nodes.init_node import make_run_id

    config_path = request.config_path or request.scenario_path or "config"
    config = load_config(config_path)
    all_dimension_names = {dim.id: dim.name for dim in config.dimensions if dim.enabled}
    dims = [dim for dim in config.dimensions if dim.enabled]
    if request.only_dimensions:
        if err := validate_dimension_ids(request.only_dimensions, config.dimensions, label="only_dimensions"):
            return _failed_diligence_result(request, err)
        dims = [dim for dim in dims if dim.id in request.only_dimensions]
    if request.skip_dimensions:
        if err := validate_dimension_ids(request.skip_dimensions, config.dimensions, label="skip_dimensions"):
            return _failed_diligence_result(request, err)
        dims = [dim for dim in dims if dim.id not in request.skip_dimensions]
    if not dims:
        return _failed_diligence_result(request, "no active dimensions after filtering")

    config = config.model_copy(update={"dimensions": dims})
    rid = request.run_id or make_run_id(request.target)
    output_dir = request.output_dir or str(Path(config.runs_dir) / rid)
    result = await run_company_graph(
        target=request.target,
        config=config,
        output_dir=output_dir,
        run_id=rid,
        config_path=config_path,
        all_dimension_names=all_dimension_names,
    )
    return PipelineRunResult(
        pipeline="diligence",
        target=result.target,
        status=result.status,
        run_id=result.run_id or rid,
        output_dir=result.artifacts_dir or output_dir,
        report_path=result.report_path,
        artifacts_dir=result.artifacts_dir,
        error=result.error,
        raw=result.model_dump(mode="json"),
    )


def _failed_diligence_result(request: PipelineRunRequest, error: str) -> PipelineRunResult:
    return PipelineRunResult(
        pipeline="diligence",
        target=request.target,
        status="failed",
        run_id=request.run_id or "",
        output_dir=request.output_dir or "",
        error=error,
    )


def _str_option(request: PipelineRunRequest, key: str) -> str | None:
    value = request.options.get(key)
    return str(value) if value not in (None, "") else None


def _list_option(request: PipelineRunRequest, key: str) -> list[str] | None:
    value = request.options.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return None


def _bool_option(request: PipelineRunRequest, key: str, *, default: bool) -> bool:
    value = request.options.get(key, default)
    return bool(value)


def _bool_or_none_option(request: PipelineRunRequest, key: str) -> bool | None:
    value = request.options.get(key)
    return value if isinstance(value, bool) else None
