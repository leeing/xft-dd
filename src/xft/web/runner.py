"""Run configurable Web enrichment for recommender dimensions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from xft.constants import DEFAULT_SCENARIO, DEFAULT_WAREHOUSE
from xft.core.config_loader import load_dimensions_config
from xft.core.dimension_analyzer import analyze_dimensions
from xft.core.scenario import load_scenario
from xft.evidence.policy import load_evidence_policy
from xft.progress import display
from xft.warehouse.profile_repository import CompanyProfileRepository
from xft.web.cache_index import (
    CachedQuery,
    copy_cached_page_artifacts,
    find_existing_run,
    find_run_by_id,
    load_cached_extractions,
    load_cached_queries,
    make_extraction_cache_key,
    make_runtime_config,
    make_search_cache_key,
    write_cache_index,
)
from xft.web.cache_writer import WebCacheWriter, safe_dir_name
from xft.web.config_loader import load_web_extract_llm_config, load_web_search_config
from xft.web.evidence import extract_evidence_batch
from xft.web.fetcher import fetch_and_cache_pages
from xft.web.models import (
    RecordStatus,
    WebRunManifest,
    WebRunMetrics,
    WebRunResult,
    WebSearchQueryRecord,
    WebSearchResultRecord,
    WebSkippedQueryRecord,
)
from xft.web.planner import plan_web_search
from xft.web.providers import build_provider
from xft.web.search_service import run_provider_query
from xft.web.web_loader import load_web_cache_to_duckdb

log = structlog.get_logger(__name__)


def make_web_run_id(company_name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in company_name)[:32].strip("_") or "company"
    return f"web_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{safe}"


async def run_web_enrichment(  # noqa: C901, PLR0912, PLR0913, PLR0915
    *,
    company_name: str,
    warehouse_db: str = DEFAULT_WAREHOUSE,
    scenario_path: str | None = None,
    web_config_path: str | None = None,
    web_extract_llm_config_path: str | None = None,
    dimensions_config_path: str | None = None,
    evidence_policy_path: str | None = None,
    output_root: str | None = None,
    only_dimensions: list[str] | None = None,
    providers: list[str] | None = None,
    run_id: str | None = None,
    load_to_duckdb: bool = True,
    refresh: bool = False,
    force_dimensions: bool = False,
    use_llm_extraction: bool = True,
    fetch_pages: bool | None = None,
    reuse_search: bool = True,
    refresh_search: bool = False,
    refresh_fetch: bool = False,
    refresh_extraction: bool = False,
    extract_only: bool = False,
    source_run_id: str | None = None,
) -> WebRunResult:
    log.info("web_enrichment_start", company_name=company_name, refresh=refresh, force_dimensions=force_dimensions)
    scenario = load_scenario(scenario_path or DEFAULT_SCENARIO)
    if scenario is None:
        msg = f"scenario not found: {scenario_path or DEFAULT_SCENARIO}"
        raise FileNotFoundError(msg)
    web_search_path = web_config_path or scenario.web_search_path
    web_extract_path = web_extract_llm_config_path or scenario.web_extract_llm_path
    dimensions_path = dimensions_config_path or scenario.dimensions_path
    evidence_path = evidence_policy_path or scenario.evidence_policy_path
    web_config = load_web_search_config(web_search_path)
    if scenario.web_cache_root:
        web_config = web_config.model_copy(update={"cache_root": scenario.web_cache_root})
    llm_config = load_web_extract_llm_config(web_extract_path)
    evidence_policy = load_evidence_policy(evidence_path)
    if not web_config.enabled:
        log.warning("web_enrichment_disabled", company_name=company_name)
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
        log.warning("web_enrichment_profile_not_found", company_name=company_name, warehouse_db=warehouse_db)
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
    dimensions = load_dimensions_config(dimensions_path).dimensions
    analyses = analyze_dimensions(profile=profile, dimensions=dimensions, policy=evidence_policy)
    effective_refresh = refresh or web_config.execution.refresh
    plan = plan_web_search(
        analyses,
        only_dimensions=only_dimensions,
        force_dimensions=force_dimensions,
        refresh=effective_refresh,
        max_queries_per_dimension=web_config.execution.max_queries_per_dimension,
        policy=evidence_policy,
    )
    provider_names = providers or web_config.default_providers
    provider_names = [
        name for name in provider_names if web_config.providers.get(name, None) and web_config.providers[name].enabled
    ]
    company_display_name = str(profile.get("company_name") or company_name)
    credit_code = _str_or_none(profile.get("credit_code"))
    fetch_config = (
        web_config.fetch
        if _should_fetch_pages(config_value=web_config.execution.fetch_pages, override=fetch_pages)
        else web_config.fetch.model_copy(update={"enabled": False})
    )
    runtime_config = make_runtime_config(
        credit_code=credit_code,
        company_name=company_display_name,
        cache_policy_version=web_config.execution.cache_policy_version,
        provider_configs={name: web_config.providers[name] for name in provider_names},
        max_results_per_query=web_config.execution.max_results_per_query,
        fetch_config=fetch_config,
        extract_prompt_version=llm_config.version,
        extract_prompt_file=llm_config.prompt_file,
        extract_model=_configured_extract_model(llm_config),
        extract_config=llm_config.model_dump(mode="json"),
    )

    rid = run_id or make_web_run_id(company_display_name)
    root = Path(output_root or web_config.cache_root)
    company_dir = safe_dir_name(
        credit_code,
        company_display_name,
    )
    company_root = root / company_dir
    existing_summary = find_existing_run(company_root)
    existing = find_run_by_id(company_root, source_run_id) if source_run_id else None
    if existing is None and existing_summary is not None:
        existing = existing_summary.run_dir
    granular_refresh = refresh_search or refresh_fetch or refresh_extraction or extract_only or bool(source_run_id)
    if existing_summary is not None and not effective_refresh and run_id is None and not granular_refresh:
        log.info(
            "web_enrichment_cache_reuse",
            company_name=company_name,
            run_dir=str(existing_summary.run_dir),
            evidence=existing_summary.evidence,
            queries=existing_summary.queries,
            results=existing_summary.results,
        )
        display.info(f"Web 缓存: 复用已有结果 ({existing_summary.evidence}条证据, {existing_summary.queries}次查询)")
        duckdb_loaded = False
        if load_to_duckdb:
            load_web_cache_to_duckdb(input_root=root, warehouse_db=warehouse_db, rebuild=False)
            duckdb_loaded = True
        return WebRunResult(
            company_name=str(profile.get("company_name") or company_name),
            credit_code=_str_or_none(profile.get("credit_code")),
            status="skipped",
            web_run_id=existing_summary.run_dir.name,
            output_dir=str(existing_summary.run_dir),
            queries=existing_summary.queries,
            results=existing_summary.results,
            evidence=existing_summary.evidence,
            duckdb_loaded=duckdb_loaded,
            error="reused existing web cache; pass --refresh to crawl again",
        )
    if extract_only and existing is None:
        return WebRunResult(
            company_name=str(profile.get("company_name") or company_name),
            credit_code=_str_or_none(profile.get("credit_code")),
            status="failed",
            web_run_id=run_id or make_web_run_id(str(profile.get("company_name") or company_name)),
            output_dir="",
            queries=0,
            results=0,
            evidence=0,
            error="--extract-only requires --source-run-id or an existing web run",
        )
    cached_queries = (
        load_cached_queries(existing, runtime=runtime_config)
        if existing and reuse_search and not refresh_search and not effective_refresh
        else {}
    )
    cached_extractions = (
        load_cached_extractions(existing, runtime=runtime_config)
        if existing and not refresh_extraction and not effective_refresh
        else {}
    )
    out_dir = root / company_dir / rid
    writer = WebCacheWriter(out_dir)
    manifest = WebRunManifest(
        web_run_id=rid,
        company_name=company_display_name,
        credit_code=credit_code,
        warehouse_db=warehouse_db,
        created_at=datetime.now(UTC),
        config={
            "dimensions_config_path": dimensions_path,
            "web_extract_llm_config_path": web_extract_path,
            "web_config_path": web_search_path,
            "evidence_policy_path": evidence_path,
            "scenario_path": scenario_path or DEFAULT_SCENARIO,
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
    cache_stats: dict[str, int] = {
        "search_reused": 0,
        "search_executed": 0,
        "fetch_reused": 0,
        "fetch_executed": 0,
        "extraction_reused": 0,
        "extraction_executed": 0,
    }
    errors: list[str] = []
    for planned in plan.planned:
        dim = planned.analysis
        display.info(f"📡 搜索维度: {dim.dimension_id} ({planned.reason}, {len(planned.queries)}条查询)")
        dimension_results: list[WebSearchResultRecord] = []
        queries_by_id: dict[str, WebSearchQueryRecord] = {}
        for query in planned.queries:
            for provider_name in provider_names:
                provider_cfg = web_config.providers[provider_name]
                query_count += 1
                query_id = f"q_{query_count:04d}"
                search_key = make_search_cache_key(
                    credit_code=runtime_config.credit_code,
                    company_name=runtime_config.company_name,
                    dimension_id=dim.dimension_id,
                    query=query,
                    provider=provider_name,
                    provider_params_hash=runtime_config.provider_params_hashes.get(provider_name, ""),
                    max_results=runtime_config.max_results_per_query,
                    cache_policy_version=runtime_config.cache_policy_version,
                )
                cache_key = search_key.key_hash
                cached_query = cached_queries.get(cache_key)
                if cached_query is not None:
                    search_output = _cached_search_output(
                        cached_query,
                        query_id=query_id,
                        web_run_id=rid,
                        profile=profile,
                        company_name=company_name,
                    )
                    cache_stats["search_reused"] += 1
                    display.branch(f'♻ [{provider_name}] "{query[:50]}" → 复用{len(search_output.results)}条')
                elif extract_only:
                    continue
                else:
                    cache_stats["search_executed"] += 1
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
                q_record = search_output.query_record.model_copy(
                    update={
                        "cache_key": search_key.key_hash,
                        "cache_policy_version": runtime_config.cache_policy_version,
                        "provider_params_hash": runtime_config.provider_params_hashes.get(provider_name, ""),
                        "max_results": runtime_config.max_results_per_query,
                    }
                )
                writer.append_query(q_record)
                queries_by_id[q_record.query_id] = q_record
                if search_output.error:
                    errors.append(f"{provider_name} {dim.dimension_id}: {search_output.error}")
                result_count += len(search_output.results)
                pending_results = search_output.results
                if cached_query is None or refresh_fetch:
                    if fetch_config.enabled:
                        cache_stats["fetch_executed"] += len(pending_results)
                    pending_results = await fetch_and_cache_pages(
                        records=pending_results,
                        writer=writer,
                        web_run_id=rid,
                        target=company_display_name,
                        config=fetch_config,
                    )
                elif fetch_config.enabled:
                    page_records = copy_cached_page_artifacts(
                        source_run_dir=cached_query.source_run_dir,
                        target_run_dir=out_dir,
                        records=pending_results,
                    )
                    for page_record in page_records:
                        writer.append_page(page_record.model_copy(update={"web_run_id": rid}))
                    if page_records:
                        cache_stats["fetch_reused"] += len(page_records)
                for result in pending_results:
                    writer.append_result(result)
                    dimension_results.append(result)
        extraction_key = make_extraction_cache_key(
            credit_code=runtime_config.credit_code,
            company_name=runtime_config.company_name,
            dimension_id=dim.dimension_id,
            results=dimension_results,
            extract_prompt_version=runtime_config.extract_prompt_version,
            extract_prompt_hash=runtime_config.extract_prompt_hash,
            extract_model=runtime_config.extract_model,
            extract_config_hash=runtime_config.extract_config_hash,
            cache_policy_version=runtime_config.cache_policy_version,
        )
        cached_extraction = cached_extractions.get(extraction_key.key_hash)
        if cached_extraction is not None:
            evidence_items = _cached_evidence(
                cached_extraction.evidence,
                web_run_id=rid,
                profile=profile,
                company_name=company_name,
                queries_by_id=queries_by_id,
                results=dimension_results,
            )
            extraction_request = cached_extraction.extraction_request
            extraction_result = {
                **cached_extraction.extraction_result,
                "mode": "cached",
                "source_run_id": cached_extraction.source_run_dir.name,
                "cache_key": cached_extraction.key_hash,
            }
            cache_stats["extraction_reused"] += 1
            display.branch(f"🧠 {dim.dimension_id}: 复用抽取缓存 → {len(evidence_items)}条")
        else:
            cache_stats["extraction_executed"] += 1
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
            prepared_evidence = evidence.model_copy(
                update={
                    "extraction_cache_key": extraction_key.key_hash,
                    "extraction_prompt_hash": runtime_config.extract_prompt_hash,
                }
            )
            writer.append_evidence(prepared_evidence)
            if prepared_evidence.evidence_type == "conflict":
                writer.append_conflict(prepared_evidence)
            evidence_count += 1
        if evidence_items:
            display.ok(f"{dim.dimension_id}: {len(evidence_items)}条证据入库")

    _write_web_decision_trace(
        out_dir=out_dir,
        plan=plan,
        policy_threshold=evidence_policy.web_planning.supported_facts_to_skip_web,
    )
    status: RecordStatus = "success" if not errors else "partial" if result_count else "failed"
    manifest = manifest.model_copy(update={"status": status, "errors": errors})
    writer.write_manifest(manifest)
    duckdb_loaded = False
    if load_to_duckdb:
        load_web_cache_to_duckdb(input_root=root, warehouse_db=warehouse_db, rebuild=False)
        duckdb_loaded = True
    write_cache_index(
        company_root,
        credit_code=credit_code,
        company_name=company_display_name,
        runtime=runtime_config,
    )
    _write_cache_report(
        out_dir=out_dir,
        company_name=company_display_name,
        credit_code=credit_code,
        web_run_id=rid,
        cache_stats=cache_stats,
        query_count=query_count,
        result_count=result_count,
        evidence_count=evidence_count,
        source_run_id=existing.name if existing else None,
        refresh_flags={
            "refresh": effective_refresh,
            "refresh_search": refresh_search,
            "refresh_fetch": refresh_fetch,
            "refresh_extraction": refresh_extraction,
            "extract_only": extract_only,
        },
    )
    display.ok(f"Web 搜索完成: {query_count}次查询, {result_count}条结果, {evidence_count}条证据 → DuckDB")
    log.info(
        "web_enrichment_done",
        company_name=company_name,
        status=status,
        planned=len(plan.planned),
        skipped=len(plan.skipped),
        queries=query_count,
        results=result_count,
        evidence=evidence_count,
        errors=errors,
        duckdb_loaded=duckdb_loaded,
    )
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
        metrics=WebRunMetrics(
            search_executed=cache_stats.get("search_executed", 0),
            search_reused=cache_stats.get("search_reused", 0),
            fetch_executed=cache_stats.get("fetch_executed", 0),
            fetch_reused=cache_stats.get("fetch_reused", 0),
            extraction_executed=cache_stats.get("extraction_executed", 0),
            extraction_reused=cache_stats.get("extraction_reused", 0),
        ),
    )


def _write_web_decision_trace(*, out_dir: Path, plan: Any, policy_threshold: int) -> None:
    queries = _read_jsonl(out_dir / "queries.jsonl")
    results = _read_jsonl(out_dir / "search_results.jsonl")
    evidence = _read_jsonl(out_dir / "web_evidence.jsonl")
    conflicts = _read_jsonl(out_dir / "conflicts.jsonl")
    pages = {str(item.get("result_id") or ""): item for item in _read_jsonl(out_dir / "fetched_pages.jsonl")}
    evidence_by_result: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        evidence_by_result.setdefault(str(item.get("result_id") or ""), []).append(item)
    payload = {
        "web_run_dir": str(out_dir),
        "planning": {
            "threshold": {
                "supported_facts_to_skip_web": policy_threshold,
                "rule": "skip when dimension.status == supported and facts_count >= supported_facts_to_skip_web",
            },
            "planned": [
                {
                    "dimension_id": item.analysis.dimension_id,
                    "decision": "search",
                    "reason": item.reason,
                    "status": item.analysis.status,
                    "facts_count": len(item.analysis.facts),
                    "threshold": policy_threshold,
                    "queries": item.queries,
                    "matched_local_facts": [fact.claim for fact in item.analysis.facts],
                    "missing_evidence": item.analysis.missing_evidence,
                }
                for item in plan.planned
            ],
            "skipped": [
                {
                    "dimension_id": item.analysis.dimension_id,
                    "decision": "skip",
                    "reason": item.reason,
                    "status": item.analysis.status,
                    "facts_count": len(item.analysis.facts),
                    "threshold": policy_threshold,
                    "matched_local_facts": item.profile_facts,
                    "decision_formula": (
                        f"status={item.analysis.status}, facts_count={len(item.analysis.facts)} >= "
                        f"threshold={policy_threshold}"
                    ),
                    "queries_not_run": item.queries,
                }
                for item in plan.skipped
            ],
        },
        "queries": queries,
        "results": [
            {
                **result,
                "decision": (
                    "accepted_as_evidence" if evidence_by_result.get(str(result.get("result_id"))) else "ignored"
                ),
                "decision_reason": _result_decision_reason(result, pages, evidence_by_result),
                "accepted_evidence_ids": [
                    ev.get("evidence_id") for ev in evidence_by_result.get(str(result.get("result_id") or ""), [])
                ],
            }
            for result in results
        ],
        "accepted_evidence": evidence,
        "conflicts": conflicts,
    }
    (out_dir / "decision_trace_web.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _result_decision_reason(
    result: dict[str, Any],
    pages: dict[str, dict[str, Any]],
    evidence_by_result: dict[str, list[dict[str, Any]]],
) -> str:
    result_id = str(result.get("result_id") or "")
    if evidence_by_result.get(result_id):
        return "LLM/fallback extraction produced evidence from this result"
    page = pages.get(result_id)
    if page and page.get("status") not in ("success", None):
        return f"page fetch {page.get('status')}: {page.get('error') or 'no usable page content'}"
    return "extraction produced no claim; likely duplicate, irrelevant, low confidence, or not about target company"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _should_fetch_pages(*, config_value: bool, override: bool | None) -> bool:
    return config_value if override is None else override


def _cached_search_output(
    cached: CachedQuery,
    *,
    query_id: str,
    web_run_id: str,
    profile: dict[str, Any],
    company_name: str,
) -> Any:
    q = cached.query.model_copy(
        update={
            "query_id": query_id,
            "web_run_id": web_run_id,
            "credit_code": _str_or_none(profile.get("credit_code")),
            "company_name": str(profile.get("company_name") or company_name),
            "created_at": datetime.now(UTC),
        }
    )
    results = [
        result.model_copy(
            update={
                "web_run_id": web_run_id,
                "query_id": query_id,
                "credit_code": _str_or_none(profile.get("credit_code")),
                "company_name": str(profile.get("company_name") or company_name),
            }
        )
        for result in cached.results
    ]
    from xft.web.search_service import SearchQueryOutput

    return SearchQueryOutput(query_record=q, results=results, error=q.error)


def _cached_evidence(  # noqa: PLR0913
    evidence: list[Any],
    *,
    web_run_id: str,
    profile: dict[str, Any],
    company_name: str,
    queries_by_id: dict[str, WebSearchQueryRecord],
    results: list[WebSearchResultRecord],
) -> list[Any]:
    """Copy cached evidence records into the current run."""
    by_result_id = {result.result_id: result for result in results}
    copied: list[Any] = []
    for index, item in enumerate(evidence, 1):
        result = by_result_id.get(item.result_id)
        query_id = result.query_id if result else item.query_id
        query = queries_by_id.get(query_id)
        copied.append(
            item.model_copy(
                update={
                    "evidence_id": f"e_cached_{index:04d}_{item.evidence_id.removeprefix('e_')[:16]}",
                    "web_run_id": web_run_id,
                    "query_id": query_id,
                    "credit_code": _str_or_none(profile.get("credit_code")),
                    "company_name": str(profile.get("company_name") or company_name),
                    "query": query.query if query else item.query,
                    "created_at": datetime.now(UTC),
                }
            )
        )
    return copied


def _configured_extract_model(llm_config: Any) -> str:
    provider = llm_config.providers.get(llm_config.provider, {})
    return str(provider.get("default_model") or "MiniMax-M2.7-Highspeed")


def _write_cache_report(  # noqa: PLR0913
    *,
    out_dir: Path,
    company_name: str,
    credit_code: str | None,
    web_run_id: str,
    cache_stats: dict[str, int],
    query_count: int,
    result_count: int,
    evidence_count: int,
    source_run_id: str | None,
    refresh_flags: dict[str, bool],
) -> None:
    payload = {
        "schema_version": "1.0",
        "web_run_id": web_run_id,
        "company_name": company_name,
        "credit_code": credit_code,
        "source_run_id": source_run_id,
        "refresh_flags": refresh_flags,
        "totals": {
            "queries": query_count,
            "results": result_count,
            "evidence": evidence_count,
        },
        "cache": cache_stats,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "web_cache_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Web Cache Report",
        "",
        f"- web_run_id: {web_run_id}",
        f"- company: {company_name}",
        f"- source_run_id: {source_run_id or ''}",
        f"- queries: {query_count}",
        f"- results: {result_count}",
        f"- evidence: {evidence_count}",
        "",
        "## Cache Stats",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in cache_stats.items())
    lines.append("")
    (out_dir / "web_cache_report.md").write_text("\n".join(lines), encoding="utf-8")
