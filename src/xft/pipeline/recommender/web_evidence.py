"""Indicator-level Web search for business recommendation indicators."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel, Field, ValidationError, field_validator

from xft.ai.chat_json import create_json_chat_completion
from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.core.search_models import SearchItem, make_item_id
from xft.pipeline.recommender.evidence_loader import indicator_key
from xft.pipeline.recommender.models import (
    IndicatorConfig,
    LabelConfig,
    ModuleConfig,
    RecommendationConfig,
)
from xft.pipeline.recommender.web_policy import WebSearchDecision, should_search_indicator
from xft.progress import display
from xft.settings import settings
from xft.utils.file_io import read_jsonl, write_json, write_jsonl
from xft.utils.misc import str_or_none
from xft.web.config_loader import load_web_search_config
from xft.web.models import WebProviderConfig
from xft.web.providers import build_provider

MIN_QUERY_TERM_LEN = 2
MAX_QUERY_TERMS = 3


@dataclass(frozen=True)
class WebRunResult:
    """Business indicator Web evidence result."""

    evidence: dict[str, list[dict[str, Any]]]
    trace: list[dict[str, Any]]
    queries: int
    results: int
    output_dir: str


@dataclass(frozen=True)
class WebRunPaths:
    out_dir: Path
    queries_path: Path
    results_path: Path
    trace_path: Path
    evidence_path: Path


@dataclass(frozen=True)
class WebRunContext:
    company_name: str
    profile: dict[str, Any]
    web_config: Any
    provider_names: list[str]
    provider_factory: Any
    query_planner: Any
    local_evidence_map: dict[str, list[dict[str, Any]]]

    @property
    def resolved_company_name(self) -> str:
        return str(self.profile.get("company_name") or self.company_name)


@dataclass(frozen=True)
class WebQuerySpec:
    module: ModuleConfig
    label: LabelConfig
    indicator: IndicatorConfig
    indicator_key: str
    decision: WebSearchDecision
    query: str
    auto: bool
    provider_name: str


@dataclass
class WebAccumulator:
    query_rows: list[dict[str, Any]]
    result_rows: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    evidence: dict[str, list[dict[str, Any]]]
    query_index: int
    query_cache: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]

    @classmethod
    def create(cls) -> WebAccumulator:
        return cls(query_rows=[], result_rows=[], trace=[], evidence={}, query_index=0, query_cache={})

    def next_query_id(self) -> str:
        self.query_index += 1
        return f"bq_{self.query_index:04d}"


class _AutoQueryPayload(BaseModel):
    queries: list[str] = Field(default_factory=list)

    @field_validator("queries", mode="before")
    @classmethod
    def coerce_queries(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)]


async def run_web_evidence(  # noqa: PLR0913
    *,
    config: RecommendationConfig | None,
    company_name: str,
    profile: dict[str, Any],
    web_config_path: str,
    output_dir: str | Path,
    providers: list[str] | None = None,
    refresh: bool = False,
    provider_factory: Any = build_provider,
    query_planner: Any | None = None,
    evidence: dict[str, list[dict[str, Any]]] | None = None,
) -> WebRunResult:
    """Execute indicator-level Web queries and convert results into evidence."""
    paths = _web_paths(output_dir)
    if not refresh and (cached := _cached_web_result(paths)):
        return cached
    if config is None:
        return _empty_web_result(paths)
    web_config = load_web_search_config(web_config_path)
    if not web_config.enabled:
        return _empty_web_result(paths)

    ctx = WebRunContext(
        company_name=company_name,
        profile=profile,
        web_config=web_config,
        provider_names=_enabled_provider_names(web_config, providers),
        provider_factory=provider_factory,
        query_planner=query_planner or _plan_auto_queries_with_llm,
        local_evidence_map=evidence or {},
    )
    display.info("  规划业务 Web 查询...")
    specs, planning_trace = await _plan_web_queries(config=config, ctx=ctx)
    n_auto = sum(1 for s in specs if s.auto)
    display.info(f"  共 {len(specs)} 个查询 (其中 {n_auto} 个自动生成), 开始执行...")
    acc = await _execute_web_queries(ctx=ctx, specs=specs, initial_trace=planning_trace)

    if web_config.fetch.enabled and acc.result_rows:
        display.info("  crawl4ai 抓取页面内容...")
        url_text_map = await _enrich_result_rows(
            acc.result_rows,
            company_name=ctx.company_name,
            fetch_config=web_config.fetch,
        )
        for row in acc.result_rows:
            url = str(row.get("url") or "")
            if url in url_text_map:
                row["full_text_preview"] = url_text_map[url][:500]
        query_by_id = {str(q.get("query_id")): q for q in acc.query_rows}
        acc.evidence = {}
        by_indicator: dict[str, list[dict[str, Any]]] = {}
        for row in acc.result_rows:
            by_indicator.setdefault(str(row.get("indicator_key") or ""), []).append(row)
        for key, rows in by_indicator.items():
            query_row = query_by_id.get(str(rows[0].get("query_id") or ""), {})
            acc.evidence[key] = _result_evidence(query_row, rows)
        display.info(f"  抓取完成: {len(url_text_map)} 个页面")

    write_jsonl(paths.queries_path, acc.query_rows)
    write_jsonl(paths.results_path, acc.result_rows)
    write_json(paths.trace_path, {"queries": acc.query_rows, "trace": acc.trace})
    write_json(paths.evidence_path, acc.evidence)
    return WebRunResult(
        evidence=acc.evidence,
        trace=acc.trace,
        queries=len(acc.query_rows),
        results=len(acc.result_rows),
        output_dir=str(paths.out_dir),
    )


def _web_paths(output_dir: str | Path) -> WebRunPaths:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return WebRunPaths(
        out_dir=out_dir,
        queries_path=out_dir / "web_queries.jsonl",
        results_path=out_dir / "web_results.jsonl",
        trace_path=out_dir / "web_trace.json",
        evidence_path=out_dir / "indicator_evidence.json",
    )


def _cached_web_result(paths: WebRunPaths) -> WebRunResult | None:
    if not paths.queries_path.exists() or not paths.results_path.exists():
        return None
    cached_queries = read_jsonl(paths.queries_path)
    cached_results = read_jsonl(paths.results_path)
    cached_evidence, cached_trace = _evidence_from_rows(cached_queries, cached_results)
    return WebRunResult(
        evidence=cached_evidence,
        trace=cached_trace,
        queries=len(cached_queries),
        results=len(cached_results),
        output_dir=str(paths.out_dir),
    )


def _empty_web_result(paths: WebRunPaths) -> WebRunResult:
    return WebRunResult(evidence={}, trace=[], queries=0, results=0, output_dir=str(paths.out_dir))


def _enabled_provider_names(web_config: Any, providers: list[str] | None) -> list[str]:
    provider_names = providers or web_config.default_providers
    return [
        name for name in provider_names if web_config.providers.get(name, None) and web_config.providers[name].enabled
    ]


async def _plan_web_queries(
    *,
    config: RecommendationConfig,
    ctx: WebRunContext,
) -> tuple[list[WebQuerySpec], list[dict[str, Any]]]:
    specs: list[WebQuerySpec] = []
    trace: list[dict[str, Any]] = []
    for module in config.modules:
        for label in module.labels:
            for indicator in label.indicators:
                if indicator.web_search is None:
                    continue
                key = indicator_key(module, label.label_id, indicator)
                local_evidence = ctx.local_evidence_map.get(key, [])
                decision = should_search_indicator(indicator=indicator, local_evidence=local_evidence, rule_result=None)
                if not decision.enabled:
                    trace.append(_skip_trace(module, label, indicator, key, decision))
                    continue
                fixed_queries = _render_queries(company_name=ctx.company_name, indicator=indicator)
                auto_queries = await _auto_queries(
                    query_planner=ctx.query_planner,
                    company_name=ctx.company_name,
                    profile=ctx.profile,
                    module=module,
                    label=label,
                    indicator=indicator,
                )
                if indicator.web_search.auto.enabled and not auto_queries:
                    trace.append(_auto_trace(module, label, indicator, key))
                query_specs = [(query, False) for query in fixed_queries] + [(query, True) for query in auto_queries]
                for query, is_auto in query_specs:
                    specs.extend(
                        [
                            WebQuerySpec(
                                module=module,
                                label=label,
                                indicator=indicator,
                                indicator_key=key,
                                decision=decision,
                                query=query,
                                auto=is_auto,
                                provider_name=provider_name,
                            )
                            for provider_name in ctx.provider_names
                        ]
                    )
    return specs, trace


def _query_row_for_spec(
    cached_query_row: dict[str, Any],
    spec: WebQuerySpec,
) -> dict[str, Any]:
    return {
        **cached_query_row,
        "indicator_key": spec.indicator_key,
        "module_id": spec.module.module_id,
        "label_id": spec.label.label_id,
        "indicator_id": spec.indicator.indicator_id,
        "auto": spec.auto,
        "trigger_reason": spec.decision.reason,
        "when": spec.decision.when,
        "effect": spec.decision.effect,
    }


def _append_web_rows(
    *,
    acc: WebAccumulator,
    key: str,
    query_row: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    acc.query_rows.append(query_row)
    acc.result_rows.extend(rows)
    acc.trace.append(_trace_row(query_row, rows))
    if result_evidence := _result_evidence(query_row, rows):
        acc.evidence.setdefault(key, []).extend(result_evidence)


async def _execute_web_queries(
    *,
    ctx: WebRunContext,
    specs: list[WebQuerySpec],
    initial_trace: list[dict[str, Any]],
) -> WebAccumulator:
    acc = WebAccumulator.create()
    acc.trace.extend(initial_trace)
    total = len(specs)
    for i, spec in enumerate(specs, 1):
        provider_cfg = ctx.web_config.providers[spec.provider_name]
        cache_key = f"{spec.query}:{spec.provider_name}"
        if cache_key in acc.query_cache:
            cached_query_row, cached_rows = acc.query_cache[cache_key]
            per_key_row = _query_row_for_spec(cached_query_row, spec)
            _append_web_rows(acc=acc, key=spec.indicator_key, query_row=per_key_row, rows=cached_rows)
            display.info(f"  [{i}/{total}] 复用缓存: {spec.query[:50]}")
            continue
        display.info(f"  [{i}/{total}] 搜索: {spec.query[:50]}")
        web_search = spec.indicator.web_search
        if web_search is None:
            continue
        query_id = acc.next_query_id()
        query_row, rows = await _run_one_query(
            provider_name=spec.provider_name,
            provider_cfg=provider_cfg,
            provider_factory=ctx.provider_factory,
            query=spec.query,
            query_id=query_id,
            indicator_key=spec.indicator_key,
            module=spec.module,
            label=spec.label,
            indicator=spec.indicator,
            company_name=ctx.company_name,
            profile=ctx.profile,
            max_results=min(web_search.max_results, ctx.web_config.execution.max_results_per_query),
            auto=spec.auto,
            decision=spec.decision,
        )
        acc.query_cache[cache_key] = (query_row, rows)
        _append_web_rows(acc=acc, key=spec.indicator_key, query_row=query_row, rows=rows)
    return acc


async def _run_one_query(  # noqa: PLR0913
    *,
    provider_name: str,
    provider_cfg: WebProviderConfig,
    provider_factory: Any,
    query: str,
    query_id: str,
    indicator_key: str,
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
    company_name: str,
    profile: dict[str, Any],
    max_results: int,
    auto: bool,
    decision: WebSearchDecision,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dimension_id = f"business:{indicator_key}"
    created_at = datetime.now(UTC)
    provider = provider_factory(provider_name, provider_cfg)
    response = await provider.search(query, dimension_id=dimension_id)
    raw_result_count = len(response.items)
    query_row = {
        "query_id": query_id,
        "indicator_key": indicator_key,
        "module_id": module.module_id,
        "label_id": label.label_id,
        "indicator_id": indicator.indicator_id,
        "credit_code": str_or_none(profile.get("credit_code")),
        "company_name": str(profile.get("company_name") or company_name),
        "provider": provider_name,
        "query": query,
        "status": response.status,
        "error": response.error,
        "auto": auto,
        "trigger_reason": decision.reason,
        "when": decision.when,
        "effect": decision.effect,
        "cache_key": _cache_key(profile=profile, indicator_key=indicator_key, query=query, provider=provider_name),
        "raw_result_count": raw_result_count,
        "created_at": created_at,
    }
    raw_items = [
        SearchItem.model_validate(raw)
        for raw in response.items
        if _is_company_relevant(raw, company_name=str(profile.get("company_name") or company_name), profile=profile)
        and _is_indicator_relevant(
            raw,
            company_name=str(profile.get("company_name") or company_name),
            indicator=indicator,
            query=query,
        )
    ][:max_results]
    rows = [
        {
            "result_id": _result_id(query_id, item.id),
            "query_id": query_id,
            "indicator_key": indicator_key,
            "module_id": module.module_id,
            "label_id": label.label_id,
            "indicator_id": indicator.indicator_id,
            "credit_code": str_or_none(profile.get("credit_code")),
            "company_name": str(profile.get("company_name") or company_name),
            "provider": provider_name,
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "full_text_preview": item.full_text[:500] if item.full_text else "",
            "source": item.source,
            "rank": item.rank,
            "created_at": item.fetched_at,
        }
        for item in raw_items
    ]
    return query_row, rows


def _render_queries(*, company_name: str, indicator: IndicatorConfig) -> list[str]:
    if indicator.web_search is None:
        return []
    return _indicatorized_queries(
        company_name=company_name,
        indicator=indicator,
        queries=[query.format(company_name=company_name) for query in indicator.web_search.fixed_queries],
        max_queries=len(indicator.web_search.fixed_queries),
    )


async def _auto_queries(  # noqa: PLR0913
    *,
    query_planner: Any | None,
    company_name: str,
    profile: dict[str, Any],
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
) -> list[str]:
    web = indicator.web_search
    if web is None or not web.auto.enabled or web.auto.max_queries <= 0 or query_planner is None:
        return []
    planned = query_planner(
        company_name=company_name,
        profile=profile,
        module_id=module.module_id,
        module_name=module.module_name,
        label_id=label.label_id,
        label_name=label.label_name,
        indicator=indicator,
        intent=web.auto.intent,
        max_queries=web.auto.max_queries,
    )
    queries = await planned if inspect.isawaitable(planned) else planned
    return [str(query).format(company_name=company_name) for query in queries[: web.auto.max_queries]]


async def _plan_auto_queries_with_llm(  # noqa: PLR0913
    *,
    company_name: str,
    profile: dict[str, Any],
    module_id: str,
    module_name: str,
    label_id: str,
    label_name: str,
    indicator: IndicatorConfig,
    max_queries: int,
    intent: str,
) -> list[str]:
    if not (settings.llm_api_key or settings.minimax_api_key):
        return []
    display.info(f"  LLM 自动生成搜索词: {indicator.indicator_name}")
    system = (
        "你是企业推荐系统的Web搜索词规划器。"
        "只输出JSON，字段为queries。"
        "queries最多包含指定数量的中文搜索词。"
        "搜索词必须围绕公司名和指标判断需要的信息，不要编造事实。"
    )
    basic = profile.get("basic")
    user_payload = {
        "company_name": company_name,
        "company_profile": {
            "company_name": profile.get("company_name"),
            "credit_code": profile.get("credit_code"),
            "industry": basic.get("industry") if isinstance(basic, dict) else profile.get("industry"),
        },
        "module": {"id": module_id, "name": module_name},
        "label": {"id": label_id, "name": label_name},
        "indicator": {
            "id": indicator.indicator_id,
            "name": indicator.indicator_name,
            "standard": indicator.standard,
            "prompt": indicator.prompt,
        },
        "intent": intent,
        "max_queries": max_queries,
    }
    client = get_ai_client()
    try:
        resp = await create_json_chat_completion(
            client,
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
            ],
            temperature=0.0,
            timeout=30,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = _AutoQueryPayload.model_validate(json.loads(extract_json(raw)))
    except (OpenAIError, json.JSONDecodeError, ValidationError, OSError, RuntimeError, TypeError, ValueError):
        return []
    return _indicatorized_queries(
        company_name=company_name,
        indicator=indicator,
        queries=[query.strip() for query in parsed.queries if query.strip()],
        max_queries=max_queries,
    )


def _result_evidence(query_row: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_type": "web",
            "source": f"{row.get('provider')}:{row.get('url') or row.get('title')}",
            "matched": True,
            "evidence": _evidence_text(row),
            "query": query_row.get("query"),
            "url": row.get("url"),
            "title": row.get("title"),
            "snippet": row.get("snippet"),
            "full_text_preview": row.get("full_text_preview"),
            "provider": row.get("provider"),
        }
        for row in rows
    ]


async def _enrich_result_rows(
    result_rows: list[dict[str, Any]],
    *,
    company_name: str,
    fetch_config: Any,
) -> dict[str, str]:
    """crawl4ai-fetch unique URLs from search results and return {url: full_text} mapping."""
    from xft.utils.fetch import enrich_items

    url_map: dict[str, dict[str, Any]] = {}
    for row in result_rows:
        url = str(row.get("url") or "")
        if not url or url.startswith("metaso://"):
            continue
        if url not in url_map:
            url_map[url] = row

    if not url_map:
        return {}

    now = datetime.now(UTC)
    candidates: list[SearchItem] = []
    for url, row in url_map.items():
        candidates.append(
            SearchItem(
                id=make_item_id(url=url, title=str(row.get("title") or ""), snippet=str(row.get("snippet") or "")),
                title=str(row.get("title") or ""),
                url=url,
                snippet=str(row.get("snippet") or ""),
                full_text=str(row.get("full_text_preview") or ""),
                query=str(row.get("query") or ""),
                dimension_id=str(row.get("indicator_key") or ""),
                source="minimax",
                rank=0,
                fetched_at=now,
            )
        )

    enriched = await enrich_items(
        candidates,
        blocked_domains=fetch_config.blocked_domains,
        target=company_name,
        fetch_timeout=fetch_config.timeout_seconds,
        concurrency=fetch_config.concurrency,
        max_full_text_chars=fetch_config.max_chars,
    )

    return {item.url: item.full_text for item in enriched if item.url and item.full_text}


def _indicatorized_queries(
    *,
    company_name: str,
    indicator: IndicatorConfig,
    queries: list[str],
    max_queries: int,
) -> list[str]:
    terms = _indicator_terms(indicator)
    rendered: list[str] = []
    for query in queries:
        normalized = " ".join(str(query).split())
        if not normalized:
            continue
        if company_name and company_name not in normalized:
            normalized = f"{company_name} {normalized}"
        if terms and _query_needs_indicator_terms(normalized, company_name):
            normalized = f"{normalized} {terms[0]}"
        if normalized not in rendered:
            rendered.append(normalized)
    return rendered[:max_queries]


def _query_needs_indicator_terms(query: str, company_name: str) -> bool:
    generic = {"官网", "新闻", "公司", "企业", "信息", "介绍", "招聘", "公开"}
    remainder = query.replace(company_name, " ") if company_name else query
    tokens = [token for token in remainder.split() if token]
    return not tokens or all(token in generic for token in tokens)


def _indicator_terms(indicator: IndicatorConfig) -> list[str]:
    chunks = [indicator.indicator_name, indicator.indicator_id, indicator.standard, indicator.prompt or ""]
    chunks.extend(indicator.evidence_hints)
    ignored = {"官网", "新闻", "公司", "企业", "信息", "公开", "判断", "是否", "满足", "指标"}
    terms: list[str] = []
    for chunk in chunks:
        cleaned = (
            str(chunk)
            .replace("_", " ")
            .replace("/", " ")
            .replace("：", " ")
            .replace(":", " ")
            .replace("（", " ")
            .replace("）", " ")
            .replace("(", " ")
            .replace(")", " ")
        )
        for part in cleaned.split():
            term = part.strip("，。、；;,. ")
            if len(term) >= MIN_QUERY_TERM_LEN and term not in ignored and term not in terms:
                terms.append(term)
            if len(terms) >= MAX_QUERY_TERMS:
                return terms
    return terms


def _is_company_relevant(raw: Any, *, company_name: str, profile: dict[str, Any]) -> bool:
    item = SearchItem.model_validate(raw)
    haystack = "\n".join(
        [
            item.title or "",
            item.snippet or "",
            item.full_text[:1000] if item.full_text else "",
            item.url or "",
        ]
    )
    names = [company_name, str(profile.get("company_name") or "")]
    credit_code = str(profile.get("credit_code") or "")
    if credit_code and credit_code in haystack:
        return True
    return any(name and name in haystack for name in names)


def _is_indicator_relevant(raw: Any, *, company_name: str, indicator: IndicatorConfig, query: str) -> bool:
    terms = [*_indicator_terms(indicator), *_query_terms(query=query, company_name=company_name)]
    if not terms:
        return True
    item = SearchItem.model_validate(raw)
    haystack = "\n".join(
        [
            item.title or "",
            item.snippet or "",
            item.full_text[:1000] if item.full_text else "",
        ]
    )
    remainder = haystack.replace(company_name, " ") if company_name else haystack
    return any(term and term in remainder for term in terms)


def _query_terms(*, query: str, company_name: str) -> list[str]:
    generic = {"官网", "新闻", "公司", "企业", "信息", "介绍", "招聘", "公开"}
    remainder = query.replace(company_name, " ") if company_name else query
    terms: list[str] = []
    for token in remainder.split():
        term = token.strip("，。、；;,. ")
        if len(term) >= MIN_QUERY_TERM_LEN and term not in generic and term not in terms:
            terms.append(term)
    return terms[:MAX_QUERY_TERMS]


def _evidence_from_rows(
    queries: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    by_query = {str(item.get("query_id")): item for item in queries}
    rows_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        rows_by_key.setdefault(str(row.get("indicator_key") or ""), []).append(row)
    evidence: dict[str, list[dict[str, Any]]] = {}
    for key, rows in rows_by_key.items():
        query = by_query.get(str(rows[0].get("query_id") or ""), {})
        evidence[key] = _result_evidence(query, rows)
    trace = [
        _trace_row(query, [row for row in results if row.get("query_id") == query.get("query_id")]) for query in queries
    ]
    return evidence, trace


def _trace_row(query_row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "indicator_key": query_row.get("indicator_key"),
        "module_id": query_row.get("module_id"),
        "label_id": query_row.get("label_id"),
        "indicator_id": query_row.get("indicator_id"),
        "query": query_row.get("query"),
        "provider": query_row.get("provider"),
        "status": query_row.get("status"),
        "error": query_row.get("error"),
        "auto": query_row.get("auto", False),
        "trigger_reason": query_row.get("trigger_reason"),
        "when": query_row.get("when"),
        "effect": query_row.get("effect"),
        "result_count": len(rows),
        "filtered_result_count": max(0, int(query_row.get("raw_result_count") or 0) - len(rows)),
        "results": [
            {"title": row.get("title"), "url": row.get("url"), "snippet": row.get("snippet")} for row in rows[:5]
        ],
    }


def _skip_trace(
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
    key: str,
    decision: WebSearchDecision,
) -> dict[str, Any]:
    return {
        "indicator_key": key,
        "module_id": module.module_id,
        "label_id": label.label_id,
        "indicator_id": indicator.indicator_id,
        "status": "skipped",
        "trigger_reason": decision.reason,
        "when": decision.when,
        "effect": decision.effect,
    }


def _auto_trace(
    module: ModuleConfig,
    label: LabelConfig,
    indicator: IndicatorConfig,
    key: str,
) -> dict[str, Any]:
    return {
        "indicator_key": key,
        "module_id": module.module_id,
        "label_id": label.label_id,
        "indicator_id": indicator.indicator_id,
        "status": "skipped",
        "auto": True,
        "max_auto_rounds": indicator.web_search.max_auto_rounds if indicator.web_search else 0,
        "max_queries": indicator.web_search.auto.max_queries if indicator.web_search else 0,
        "note": "auto query generation is configured but no planner/LLM query was produced",
    }


def _evidence_text(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "")
    snippet = str(row.get("snippet") or row.get("full_text_preview") or "")
    if title and snippet:
        return f"{title}：{snippet}"
    return title or snippet


def _cache_key(*, profile: dict[str, Any], indicator_key: str, query: str, provider: str) -> str:
    raw = ":".join(
        [
            str(profile.get("credit_code") or ""),
            str(profile.get("company_name") or ""),
            indicator_key,
            query,
            provider,
        ]
    )
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


def _result_id(query_id: str, item_id: str) -> str:
    digest = hashlib.sha1(f"{query_id}:{item_id}".encode(), usedforsecurity=False).hexdigest()[:12]
    return f"br_{digest}"
