"""search_node: run MiniMax Search for all queries in one dimension, then optionally enrich with Metaso/Playwright."""

from __future__ import annotations

import asyncio
import sys
from typing import Literal

import httpx
import structlog

from xft.pipeline.diligence.config import AppConfig, Dimension
from xft.pipeline.diligence.models import CostRecord, DimensionSearchResult, SearchItem
from xft.pipeline.diligence.state import DiligenceState
from xft.settings import settings
from xft.utils.fetch import enrich_items
from xft.utils.metaso import enrich_with_metaso, enrich_with_metaso_search
from xft.utils.minimax_search import dedup_items, run_search

log = structlog.get_logger(__name__)


def _normalize_target(target: str) -> str:
    """Normalize target name for Metaso query construction.

    Replaces ASCII parentheses with their fullwidth equivalents so that
    company names like '美世乐(广东)新能源科技有限公司' don't break
    Metaso's query parser (which treats bare parentheses as grouping operators).
    MiniMax Search queries are not affected — they use quoted strings which handle
    parentheses correctly.
    """
    return target.replace("(", "（").replace(")", "）")


async def _run_minimax_queries(
    dim: Dimension,
    target: str,
    timeout: int,
    max_results: int,
    semaphore: asyncio.Semaphore,
) -> tuple[list[SearchItem], int, int]:
    """Run all MiniMax Search queries for a dimension; return (items, failed_count, success_count)."""
    all_items: list[SearchItem] = []
    failed_queries = 0
    success_queries = 0

    async def fetch_one(query: str) -> None:
        nonlocal failed_queries, success_queries
        async with semaphore:
            try:
                items = await run_search(
                    query=query,
                    dimension_id=dim.id,
                    timeout=timeout,
                    max_results=max_results,
                )
                all_items.extend(items)
                success_queries += 1
            except httpx.TimeoutException:
                failed_queries += 1
                log.warning("search_timeout", dimension=dim.id, query=query)
                sys.stderr.write(f"  [{dim.name}] search timeout: {query}\n")
            except (httpx.HTTPError, ValueError) as exc:
                failed_queries += 1
                log.warning("search_error", dimension=dim.id, query=query, error=str(exc))
                sys.stderr.write(f"  [{dim.name}] search error: {query} -- {exc}\n")

    queries = [q.replace("{target}", target) for q in dim.minimax_queries]
    await asyncio.gather(*[fetch_one(q) for q in queries])
    return all_items, failed_queries, success_queries


async def _apply_metaso(
    items: list[SearchItem],
    dim: Dimension,
    target: str,
) -> tuple[list[SearchItem], int, int, int]:
    """Enrich items with Metaso (chat or search mode); returns (items, success, failed, credits)."""
    if not (settings.metaso_enabled and settings.metaso_api_key and dim.metaso_queries):
        return items, 0, 0, 0
    normalized = _normalize_target(target)
    metaso_qs = [q.replace("{target}", normalized) for q in dim.metaso_queries]

    if dim.metaso_mode == "search":
        sys.stderr.write(f"  [{dim.name}] 秘塔搜索 ({len(metaso_qs)} 条查询, search模式)...\n")
        try:
            enriched, success, failed, credits = await enrich_with_metaso_search(
                items=items,
                dimension_id=dim.id,
                queries=metaso_qs,
                api_key=settings.metaso_api_key,
                size=dim.metaso_search_size,
                verify_tls=settings.metaso_verify_tls,
            )
        except (OSError, ValueError) as exc:
            log.warning("metaso_search_enrich_failed", dimension=dim.id, error=str(exc))
            sys.stderr.write(f"  [{dim.name}] 秘塔搜索失败（降级到纯MiniMax Search结果）: {exc}\n")
            return items, 0, len(dim.metaso_queries), 0
        else:
            return enriched, success, failed, credits

    sys.stderr.write(f"  [{dim.name}] 秘塔AI搜索 ({len(metaso_qs)} 条查询, chat模式)...\n")
    try:
        enriched, success, failed, credits = await enrich_with_metaso(
            items=items,
            dimension_id=dim.id,
            queries=metaso_qs,
            api_key=settings.metaso_api_key,
            verify_tls=settings.metaso_verify_tls,
        )
    except (OSError, ValueError) as exc:
        log.warning("metaso_enrich_failed", dimension=dim.id, error=str(exc))
        sys.stderr.write(f"  [{dim.name}] 秘塔AI搜索失败（降级到纯MiniMax Search结果）: {exc}\n")
        return items, 0, len(dim.metaso_queries), 0
    else:
        return enriched, success, failed, credits


async def _apply_crawl(
    items: list[SearchItem],
    dim: Dimension,
    config: AppConfig,
    target: str,
) -> list[SearchItem]:
    """Enrich items with full page text via crawl4ai when fetch_enabled; returns original on failure."""
    if not dim.fetch_enabled:
        return items
    sys.stderr.write(f"  [{dim.name}] crawl4ai enrichment...\n")
    try:
        enriched = await enrich_items(
            items=items,
            blocked_domains=config.fetch_blocked_domains,
            target=target,
            fetch_timeout=config.crawl_fetch_timeout,
            concurrency=config.crawl_fetch_concurrency,
            max_full_text_chars=config.max_full_text_chars,
        )
    except (OSError, ValueError) as exc:
        log.warning("enrich_failed", dimension=dim.id, error=str(exc))
        sys.stderr.write(f"  [{dim.name}] enrichment failed (fallback to snippets): {exc}\n")
        return items
    else:
        count = sum(1 for item in enriched if item.full_text)
        sys.stderr.write(f"  [{dim.name}] {count}/{len(enriched)} items enriched with full text\n")
        return enriched


async def search_node(state: DiligenceState) -> dict[str, object]:
    """Run MiniMax Search for every query in the current dimension, dedup, and return results."""
    dim: Dimension = state["current_dimension"]  # type: ignore[assignment]
    target: str = state["target"]
    config = state["config"]

    semaphore = asyncio.Semaphore(config.query_concurrency_per_dimension)
    all_items, failed_queries, success_queries = await _run_minimax_queries(
        dim, target, config.search_timeout_seconds, config.max_results_per_query, semaphore
    )

    deduped = dedup_items(all_items)
    deduped, metaso_success, metaso_failed, metaso_credits = await _apply_metaso(deduped, dim, target)
    before_cross_dedup = len(deduped)
    deduped = dedup_items(deduped)
    removed = before_cross_dedup - len(deduped)
    if removed:
        sys.stderr.write(f"  [{dim.name}] {removed} cross-provider duplicate(s) removed\n")
    deduped = await _apply_crawl(deduped, dim, config, target)

    total_queries = len(dim.minimax_queries)
    dim_status: Literal["success", "partial", "failed"]
    if failed_queries == 0:
        dim_status = "success"
    elif failed_queries < total_queries:
        dim_status = "partial"
    else:
        dim_status = "failed"

    dsr = DimensionSearchResult(
        dimension_id=dim.id,
        dimension_name=dim.name,
        status=dim_status,
        items=deduped,
        error=f"{failed_queries}/{total_queries} queries failed" if failed_queries > 0 else None,
    )
    cost = CostRecord(
        minimax_search_calls=success_queries,
        metaso_calls=metaso_success,
        metaso_failed_calls=metaso_failed,
        metaso_credits_total=metaso_credits,
    )
    log.info("search_complete", dimension=dim.id, items=len(deduped), status=dim_status)
    sys.stderr.write(f"  [{dim.name}] {len(deduped)} results, status={dim_status}\n")
    return {"search_results_by_dimension": {dim.id: dsr}, "cost": cost}
