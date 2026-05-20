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
from pydantic import BaseModel, ValidationError

from xft.ai.client import get_ai_client
from xft.ai.json_extractor import extract_json
from xft.core.search_models import SearchItem
from xft.pipeline.recommender.business_evidence_loader import indicator_key
from xft.pipeline.recommender.business_models import (
    BusinessIndicatorConfig,
    BusinessLabelConfig,
    BusinessModuleConfig,
    BusinessRecommendationConfig,
)
from xft.pipeline.recommender.business_web_policy import WebSearchDecision, should_search_indicator
from xft.settings import settings
from xft.utils.file_io import read_jsonl, write_json, write_jsonl
from xft.utils.misc import str_or_none
from xft.web.config_loader import load_web_search_config
from xft.web.models import WebProviderConfig
from xft.web.providers import build_provider


@dataclass(frozen=True)
class BusinessWebRunResult:
    """Business indicator Web evidence result."""

    evidence: dict[str, list[dict[str, Any]]]
    trace: list[dict[str, Any]]
    queries: int
    results: int
    output_dir: str


class _AutoQueryPayload(BaseModel):
    queries: list[str]


async def run_business_web_evidence(  # noqa: C901, PLR0913
    *,
    config: BusinessRecommendationConfig | None,
    company_name: str,
    profile: dict[str, Any],
    web_config_path: str,
    output_dir: str | Path,
    providers: list[str] | None = None,
    refresh: bool = False,
    provider_factory: Any = build_provider,
    query_planner: Any | None = None,
) -> BusinessWebRunResult:
    """Execute indicator-level Web queries and convert results into evidence."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    queries_path = out_dir / "business_web_queries.jsonl"
    results_path = out_dir / "business_web_results.jsonl"
    trace_path = out_dir / "business_web_trace.json"
    evidence_path = out_dir / "business_indicator_evidence.json"
    if not refresh and queries_path.exists() and results_path.exists():
        cached_queries = read_jsonl(queries_path)
        cached_results = read_jsonl(results_path)
        cached_evidence, cached_trace = _evidence_from_rows(cached_queries, cached_results)
        return BusinessWebRunResult(
            evidence=cached_evidence,
            trace=cached_trace,
            queries=len(cached_queries),
            results=len(cached_results),
            output_dir=str(out_dir),
        )
    if config is None:
        return BusinessWebRunResult(evidence={}, trace=[], queries=0, results=0, output_dir=str(out_dir))
    web_config = load_web_search_config(web_config_path)
    if not web_config.enabled:
        return BusinessWebRunResult(evidence={}, trace=[], queries=0, results=0, output_dir=str(out_dir))
    provider_names = providers or web_config.default_providers
    provider_names = [
        name for name in provider_names if web_config.providers.get(name, None) and web_config.providers[name].enabled
    ]
    query_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    evidence: dict[str, list[dict[str, Any]]] = {}
    query_index = 0
    planner = query_planner or _plan_auto_queries_with_llm
    for module in config.modules:
        for label in module.labels:
            for indicator in label.indicators:
                if indicator.web_search is None:
                    continue
                key = indicator_key(module, label.label_id, indicator)
                decision = should_search_indicator(indicator=indicator, local_evidence=[], rule_result=None)
                if not decision.enabled:
                    trace.append(_skip_trace(module, label, indicator, key, decision))
                    continue
                fixed_queries = _render_queries(company_name=company_name, indicator=indicator)
                auto_queries = await _auto_queries(
                    query_planner=planner,
                    company_name=company_name,
                    profile=profile,
                    module=module,
                    label=label,
                    indicator=indicator,
                )
                if indicator.web_search.auto.enabled and not auto_queries:
                    trace.append(_auto_trace(module, label, indicator, key))
                query_specs = [(query, False) for query in fixed_queries] + [(query, True) for query in auto_queries]
                for query, is_auto in query_specs:
                    for provider_name in provider_names:
                        provider_cfg = web_config.providers[provider_name]
                        query_index += 1
                        query_id = f"bq_{query_index:04d}"
                        query_row, rows = await _run_one_query(
                            provider_name=provider_name,
                            provider_cfg=provider_cfg,
                            provider_factory=provider_factory,
                            query=query,
                            query_id=query_id,
                            indicator_key=key,
                            module=module,
                            label=label,
                            indicator=indicator,
                            company_name=company_name,
                            profile=profile,
                            max_results=min(
                                indicator.web_search.max_results,
                                web_config.execution.max_results_per_query,
                            ),
                            auto=is_auto,
                            decision=decision,
                        )
                        query_rows.append(query_row)
                        result_rows.extend(rows)
                        trace.append(_trace_row(query_row, rows))
                        evidence.setdefault(key, []).extend(_result_evidence(query_row, rows))
    write_jsonl(queries_path, query_rows)
    write_jsonl(results_path, result_rows)
    write_json(trace_path, {"queries": query_rows, "trace": trace})
    write_json(evidence_path, evidence)
    return BusinessWebRunResult(
        evidence=evidence,
        trace=trace,
        queries=len(query_rows),
        results=len(result_rows),
        output_dir=str(out_dir),
    )


async def _run_one_query(  # noqa: PLR0913
    *,
    provider_name: str,
    provider_cfg: WebProviderConfig,
    provider_factory: Any,
    query: str,
    query_id: str,
    indicator_key: str,
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
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
        "created_at": created_at,
    }
    rows: list[dict[str, Any]] = []
    for raw in response.items[:max_results]:
        item = SearchItem.model_validate(raw)
        rows.append(
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
        )
    return query_row, rows


def _render_queries(*, company_name: str, indicator: BusinessIndicatorConfig) -> list[str]:
    if indicator.web_search is None:
        return []
    return [query.format(company_name=company_name) for query in indicator.web_search.fixed_queries]


async def _auto_queries(  # noqa: PLR0913
    *,
    query_planner: Any | None,
    company_name: str,
    profile: dict[str, Any],
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
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
        module=module,
        label=label,
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
    indicator: BusinessIndicatorConfig,
    max_queries: int,
    intent: str,
    **_: Any,
) -> list[str]:
    if not (settings.llm_api_key or settings.minimax_api_key):
        return []
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
        resp = await client.chat.completions.create(
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
    return [query.strip() for query in parsed.queries if query.strip()][:max_queries]


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
        "results": [
            {"title": row.get("title"), "url": row.get("url"), "snippet": row.get("snippet")} for row in rows[:5]
        ],
    }


def _skip_trace(
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
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
    module: BusinessModuleConfig,
    label: BusinessLabelConfig,
    indicator: BusinessIndicatorConfig,
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
