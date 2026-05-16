"""Run configurable Web enrichment for recommender dimensions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from diligence.recommender.config_loader import load_dimensions_config
from diligence.recommender.dimension_analyzer import analyze_dimensions
from diligence.recommender.profile_repository import CompanyProfileRepository
from diligence.recommender.web.cache_index import find_existing_run
from diligence.recommender.web.cache_writer import WebCacheWriter, safe_dir_name
from diligence.recommender.web.config_loader import load_web_extract_llm_config, load_web_search_config
from diligence.recommender.web.evidence import extract_evidence_batch
from diligence.recommender.web.fetcher import fetch_and_cache_pages
from diligence.recommender.web.models import (
    RecordStatus,
    WebRunManifest,
    WebRunResult,
    WebSearchQueryRecord,
    WebSearchResultRecord,
    WebSkippedQueryRecord,
)
from diligence.recommender.web.planner import plan_web_search
from diligence.recommender.web.providers import build_provider
from diligence.recommender.web.search_service import run_provider_query
from diligence.recommender.web.web_loader import load_web_cache_to_duckdb


def make_web_run_id(company_name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in company_name)[:32].strip("_") or "company"
    return f"web_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{safe}"


async def run_web_enrichment(  # noqa: C901, PLR0912, PLR0913, PLR0915
    *,
    company_name: str,
    warehouse_db: str = "cache/company_warehouse.duckdb",
    web_config_path: str = "config/recommender/web_search.yaml",
    web_extract_llm_config_path: str = "config/recommender/web_extract_llm.yaml",
    dimensions_config_path: str = "config/recommender/analysis_dimensions.yaml",
    output_root: str | None = None,
    only_dimensions: list[str] | None = None,
    providers: list[str] | None = None,
    run_id: str | None = None,
    load_to_duckdb: bool = True,
    refresh: bool = False,
    force_dimensions: bool = False,
    use_llm_extraction: bool = True,
    fetch_pages: bool | None = None,
) -> WebRunResult:
    web_config = load_web_search_config(web_config_path)
    llm_config = load_web_extract_llm_config(web_extract_llm_config_path)
    if not web_config.enabled:
        return WebRunResult(
            company_name=company_name,
            status="skipped",
            web_run_id=run_id or make_web_run_id(company_name),
            output_dir="",
            queries=0,
            results=0,
            evidence=0,
            error="web search disabled",
        )
    profile = CompanyProfileRepository(warehouse_db).get_by_company_name(company_name)
    if profile is None:
        return WebRunResult(
            company_name=company_name,
            status="failed",
            web_run_id=run_id or make_web_run_id(company_name),
            output_dir="",
            queries=0,
            results=0,
            evidence=0,
            error=f"company not found in company_profile: {company_name}",
        )
    dimensions = load_dimensions_config(dimensions_config_path).dimensions
    analyses = analyze_dimensions(profile=profile, dimensions=dimensions)
    effective_refresh = refresh or web_config.execution.refresh
    plan = plan_web_search(
        analyses,
        only_dimensions=only_dimensions,
        force_dimensions=force_dimensions,
        refresh=effective_refresh,
        max_queries_per_dimension=web_config.execution.max_queries_per_dimension,
    )
    provider_names = providers or web_config.default_providers
    provider_names = [
        name
        for name in provider_names
        if web_config.providers.get(name, None) and web_config.providers[name].enabled
    ]

    rid = run_id or make_web_run_id(str(profile.get("company_name") or company_name))
    root = Path(output_root or web_config.cache_root)
    company_dir = safe_dir_name(
        _str_or_none(profile.get("credit_code")),
        str(profile.get("company_name") or company_name),
    )
    existing = find_existing_run(root / company_dir)
    if existing is not None and not effective_refresh and run_id is None:
        duckdb_loaded = False
        if load_to_duckdb:
            load_web_cache_to_duckdb(input_root=root, warehouse_db=warehouse_db, rebuild=False)
            duckdb_loaded = True
        return WebRunResult(
            company_name=str(profile.get("company_name") or company_name),
            credit_code=_str_or_none(profile.get("credit_code")),
            status="skipped",
            web_run_id=existing.run_dir.name,
            output_dir=str(existing.run_dir),
            queries=existing.queries,
            results=existing.results,
            evidence=existing.evidence,
            duckdb_loaded=duckdb_loaded,
            error="reused existing web cache; pass --refresh to crawl again",
        )
    out_dir = root / company_dir / rid
    writer = WebCacheWriter(out_dir)
    manifest = WebRunManifest(
        web_run_id=rid,
        company_name=str(profile.get("company_name") or company_name),
        credit_code=_str_or_none(profile.get("credit_code")),
        warehouse_db=warehouse_db,
        created_at=datetime.now(UTC),
        config={
            "web_config_path": web_config_path,
            "dimensions_config_path": dimensions_config_path,
            "web_extract_llm_config_path": web_extract_llm_config_path,
        },
        providers=provider_names,
        dimensions=[item.analysis.dimension_id for item in plan.planned],
        status="success",
    )
    writer.write_manifest(manifest)
    writer.write_plan(
        {
            "planned": [
                {
                    "dimension_id": item.analysis.dimension_id,
                    "queries": item.queries,
                    "reason": item.reason,
                }
                for item in plan.planned
            ],
            "skipped": [
                {
                    "dimension_id": item.analysis.dimension_id,
                    "queries": item.queries,
                    "reason": item.reason,
                    "profile_facts": item.profile_facts,
                }
                for item in plan.skipped
            ],
        }
    )
    for skipped in plan.skipped:
        for query in skipped.queries:
            writer.append_skipped_query(
                WebSkippedQueryRecord(
                    web_run_id=rid,
                    credit_code=_str_or_none(profile.get("credit_code")),
                    company_name=str(profile.get("company_name") or company_name),
                    dimension_id=skipped.analysis.dimension_id,
                    query=query,
                    reason=skipped.reason,
                    profile_facts=skipped.profile_facts,
                    created_at=datetime.now(UTC),
                )
            )

    query_count = 0
    result_count = 0
    evidence_count = 0
    errors: list[str] = []
    for planned in plan.planned:
        dim = planned.analysis
        dimension_results: list[WebSearchResultRecord] = []
        queries_by_id: dict[str, WebSearchQueryRecord] = {}
        for query in planned.queries:
            for provider_name in provider_names:
                provider_cfg = web_config.providers[provider_name]
                query_count += 1
                query_id = f"q_{query_count:04d}"
                search_output = await run_provider_query(
                    provider_name=provider_name,
                    provider_cfg=provider_cfg,
                    query=query,
                    query_id=query_id,
                    query_index=query_count,
                    web_run_id=rid,
                    profile=profile,
                    company_name=company_name,
                    dimension_id=dim.dimension_id,
                    max_results=web_config.execution.max_results_per_query,
                    writer=writer,
                    provider_factory=build_provider,
                )
                q_record = search_output.query_record
                writer.append_query(q_record)
                queries_by_id[q_record.query_id] = q_record
                if search_output.error:
                    errors.append(f"{provider_name} {dim.dimension_id}: {search_output.error}")
                result_count += len(search_output.results)
                pending_results = search_output.results
                pending_results = await fetch_and_cache_pages(
                    records=pending_results,
                    writer=writer,
                    web_run_id=rid,
                    target=str(profile.get("company_name") or company_name),
                    config=(
                        web_config.fetch
                        if _should_fetch_pages(config_value=web_config.execution.fetch_pages, override=fetch_pages)
                        else web_config.fetch.model_copy(update={"enabled": False})
                    ),
                )
                for result in pending_results:
                    writer.append_result(result)
                    dimension_results.append(result)
        evidence_items, extraction_request, extraction_result = await extract_evidence_batch(
            profile=profile,
            analysis=dim,
            results=dimension_results,
            queries_by_id=queries_by_id,
            llm_config=llm_config,
            use_llm=use_llm_extraction,
        )
        if extraction_request:
            writer.append_extraction_request(
                {"dimension_id": dim.dimension_id, "web_run_id": rid, "payload": extraction_request}
            )
        if extraction_result:
            writer.append_extraction_result(
                {"dimension_id": dim.dimension_id, "web_run_id": rid, "payload": extraction_result}
            )
        for evidence in evidence_items:
            writer.append_evidence(evidence)
            if evidence.evidence_type == "conflict":
                writer.append_conflict(evidence)
            evidence_count += 1

    status: RecordStatus = "success" if not errors else "partial" if result_count else "failed"
    manifest = manifest.model_copy(update={"status": status, "errors": errors})
    writer.write_manifest(manifest)
    duckdb_loaded = False
    if load_to_duckdb:
        load_web_cache_to_duckdb(input_root=root, warehouse_db=warehouse_db, rebuild=False)
        duckdb_loaded = True
    return WebRunResult(
        company_name=manifest.company_name,
        credit_code=manifest.credit_code,
        status=status,
        web_run_id=rid,
        output_dir=str(out_dir),
        queries=query_count,
        results=result_count,
        evidence=evidence_count,
        duckdb_loaded=duckdb_loaded,
        error="; ".join(errors) or None,
    )


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _should_fetch_pages(*, config_value: bool, override: bool | None) -> bool:
    return config_value if override is None else override
