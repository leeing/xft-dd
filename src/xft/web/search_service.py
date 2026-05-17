"""Provider search orchestration for Web enrichment."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from xft.core.search_models import SearchItem
from xft.progress import display
from xft.web.cache_writer import WebCacheWriter
from xft.web.models import (
    ProviderSearchResponse,
    WebProviderConfig,
    WebSearchQueryRecord,
    WebSearchResultRecord,
)
from xft.web.providers import SearchProvider, build_provider


@dataclass(frozen=True)
class SearchQueryOutput:
    """Standardized output from one provider query."""

    query_record: WebSearchQueryRecord
    results: list[WebSearchResultRecord]
    error: str | None = None


async def run_provider_query(  # noqa: PLR0913
    *,
    provider_name: str,
    provider_cfg: WebProviderConfig,
    query: str,
    query_id: str,
    query_index: int,
    web_run_id: str,
    profile: dict[str, Any],
    company_name: str,
    dimension_id: str,
    max_results: int,
    writer: WebCacheWriter,
    provider_factory: Any = build_provider,
) -> SearchQueryOutput:
    """Run one provider query, persist raw provider payload, and normalize results."""
    provider: SearchProvider = provider_factory(provider_name, provider_cfg)
    response = await provider.search(query, dimension_id=dimension_id)
    raw_path = writer.write_provider_response(
        f"{provider_name}__{query_index:04d}.json",
        _provider_payload(response),
    )
    result_count = len(response.items[:max_results])
    if response.error:
        display.branch(f"✗ [{provider_name}] {query[:60]} → {response.error}")
    else:
        display.branch(f'🔍 [{provider_name}] "{query[:50]}" → {result_count}条')
    q_record = WebSearchQueryRecord(
        query_id=query_id,
        web_run_id=web_run_id,
        credit_code=_str_or_none(profile.get("credit_code")),
        company_name=str(profile.get("company_name") or company_name),
        dimension_id=dimension_id,
        provider=provider_name,
        query=query,
        status=response.status,
        raw_response_path=raw_path,
        error=response.error,
        created_at=datetime.now(UTC),
    )
    results: list[WebSearchResultRecord] = []
    for item_data in response.items[:max_results]:
        item = SearchItem.model_validate(item_data)
        results.append(
            make_result_record(
                item=item,
                web_run_id=web_run_id,
                query_id=query_id,
                profile=profile,
                company_name=company_name,
                dimension_id=dimension_id,
                provider_name=provider_name,
                raw_path=raw_path,
            )
        )
    return SearchQueryOutput(query_record=q_record, results=results, error=response.error)


def make_result_record(  # noqa: PLR0913
    *,
    item: SearchItem,
    web_run_id: str,
    query_id: str,
    profile: dict[str, Any],
    company_name: str,
    dimension_id: str,
    provider_name: str,
    raw_path: str,
) -> WebSearchResultRecord:
    """Convert a SearchItem into the recommender Web result record."""
    return WebSearchResultRecord(
        result_id=_result_id(web_run_id, query_id, item.id),
        web_run_id=web_run_id,
        query_id=query_id,
        credit_code=_str_or_none(profile.get("credit_code")),
        company_name=str(profile.get("company_name") or company_name),
        dimension_id=dimension_id,
        provider=provider_name,
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        full_text=item.full_text,
        full_text_preview=item.full_text[:500] if item.full_text else "",
        source=item.source,
        rank=item.rank,
        raw_response_path=raw_path,
        created_at=item.fetched_at,
    )


def _result_id(web_run_id: str, query_id: str, item_id: str) -> str:
    digest = hashlib.sha1(f"{web_run_id}:{query_id}:{item_id}".encode(), usedforsecurity=False).hexdigest()[:12]
    return f"r_{digest}"


def _provider_payload(response: ProviderSearchResponse) -> dict[str, Any]:
    return response.model_dump(mode="json")


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
